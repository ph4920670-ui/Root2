# cogs/pg_user_gateway.py — Gateway WS por user token, descobre threads
#
# Pra cada mediador com token ativo, abre uma conexão WebSocket no gateway
# do Discord. Discord envia automaticamente:
#   - READY: lista todas as threads ativas onde o user é membro
#   - THREAD_CREATE: user foi adicionado a uma thread nova
#   - THREAD_UPDATE: thread renomeada (aguardando-X → fila-X) ou alterada
#   - THREAD_LIST_SYNC: bulk sync periódico
#   - THREAD_DELETE: thread removida
#
# A medida que esses eventos chegam, populamos `_threads_cache` (compartilhado
# com pg_polling). O polling continua processando do cache, sem precisar
# descobrir via REST (que falha pra threads privadas com `with_message:false`).
#
# IMPORTANTE: usar token de usuário em automação viola os ToS do Discord.
# O usuário CONSCIENTEMENTE forneceu o token via "token mode" no painel.

import asyncio
import json
import logging
import time

import aiohttp
from discord.ext import commands

from utils.database import token_mode_listar_todos, token_mode_get
from cogs.pg_polling import _threads_cache  # cache compartilhado

_log = logging.getLogger("salasff.pg_user_gateway")

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# user_id -> asyncio.Task da sessão
_sessions: dict[str, "UserGatewaySession"] = {}

# Tokens marcados como inválidos (auth fail) — não tenta reconectar
_tokens_invalidos: set[str] = set()

# Callback opcional pra MESSAGE_CREATE — registrada pelo pg_polling.
# Permite detecção instantânea de "pg X" sem esperar o próximo round (3s).
# Assinatura: async def cb(user_id: str, msg: dict) -> None
_on_message_cb = None


def register_message_callback(cb):
    """Registra uma callback async que recebe (user_id, msg_dict) pra cada
    MESSAGE_CREATE que chega no gateway. Usada pra ativar o `pg X` event-driven."""
    global _on_message_cb
    _on_message_cb = cb


def gateway_ready(user_id: str) -> bool:
    """True se o user tem uma sessão de gateway WS ativa que já recebeu o READY.
    Quando True, o `_threads_cache` já foi populado com as threads do user e o
    polling pode pular a descoberta REST inteira — basta ler do cache.
    """
    sess = _sessions.get(user_id)
    return bool(sess and sess.session_id and sess.running)


class UserGatewaySession:
    """Uma conexão WS pro gateway do Discord, autenticada com um user token."""

    def __init__(self, user_id: str, token: str):
        self.user_id = user_id
        self.token = token
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.session_id: str | None = None
        self.seq: int | None = None
        self.heartbeat_interval: float = 41.25  # default-ish
        self.heartbeat_task: asyncio.Task | None = None
        self.running = True
        self.threads_count = 0  # quantas threads o user é membro

    def stop(self):
        self.running = False
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()

    async def run(self):
        """Loop externo: conecta, roda, reconecta no fail. Sai se token invalidar."""
        backoff = 5.0
        while self.running:
            if self.token in _tokens_invalidos:
                _log.info(f"[user_gw] user={self.user_id} token marcado como inválido — não reconectando")
                return
            try:
                await self._connect_and_run()
                # desconexão limpa — pequeno delay e reconecta
                _log.info(f"[user_gw] user={self.user_id} desconectado limpo, reconectando em {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 60)
            except _AuthFailed as e:
                _log.warning(f"[user_gw] user={self.user_id} AUTH FAIL: {e} — token marcado como inválido")
                _tokens_invalidos.add(self.token)
                return
            except Exception as e:
                _log.warning(f"[user_gw] user={self.user_id} erro: {e}; reconnect em {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
            finally:
                if self.heartbeat_task and not self.heartbeat_task.done():
                    self.heartbeat_task.cancel()

    async def _connect_and_run(self):
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(
                GATEWAY_URL,
                max_msg_size=0,
                heartbeat=None,  # gerenciamos manualmente
                timeout=30,
            ) as ws:
                self.ws = ws

                # 1) HELLO (op 10)
                first = await ws.receive(timeout=30)
                if first.type != aiohttp.WSMsgType.TEXT:
                    raise RuntimeError(f"esperava HELLO TEXT, recebeu type={first.type}")
                hello = json.loads(first.data)
                if hello.get("op") != 10:
                    raise RuntimeError(f"esperava HELLO op=10, recebeu op={hello.get('op')}")
                self.heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000.0
                _log.info(f"[user_gw] user={self.user_id} HELLO heartbeat={self.heartbeat_interval:.1f}s")

                # 2) Heartbeat task (jitter na primeira)
                self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                # 3) IDENTIFY
                await ws.send_json({
                    "op": 2,
                    "d": {
                        "token": self.token,
                        "capabilities": 16381,
                        "properties": {
                            "os": "Linux",
                            "browser": "Chrome",
                            "device": "",
                            "system_locale": "en-US",
                            "browser_user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            "browser_version": "120.0.0.0",
                            "os_version": "",
                            "referrer": "",
                            "referring_domain": "",
                            "referrer_current": "",
                            "referring_domain_current": "",
                            "release_channel": "stable",
                            "client_build_number": 257770,
                            "client_event_source": None,
                        },
                        "presence": {"status": "invisible", "since": 0, "activities": [], "afk": False},
                        "compress": False,
                        "client_state": {
                            "guild_versions": {},
                            "highest_last_message_id": "0",
                            "read_state_version": 0,
                            "user_guild_settings_version": -1,
                            "user_settings_version": -1,
                            "private_channels_version": "0",
                            "api_code_version": 0,
                        },
                    }
                })

                # 4) Loop de eventos
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                        except Exception as e:
                            _log.warning(f"[user_gw] user={self.user_id} JSON parse erro: {e}")
                            continue
                        await self._dispatch(data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING):
                        close_code = ws.close_code
                        _log.info(f"[user_gw] user={self.user_id} WS CLOSE code={close_code}")
                        # Códigos fatais (auth fail, etc) não devem reconectar
                        if close_code in (4004,):  # 4004 = authentication failed
                            raise _AuthFailed(f"close code {close_code}")
                        break
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        _log.warning(f"[user_gw] user={self.user_id} WS ERROR: {ws.exception()}")
                        break

    async def _dispatch(self, data: dict):
        op = data.get("op")
        s = data.get("s")
        t = data.get("t")
        d = data.get("d") or {}

        if s is not None:
            self.seq = s

        if op == 0:  # Dispatch
            self._handle_event(t, d)
        elif op == 1:  # Heartbeat request from server
            try:
                await self.ws.send_json({"op": 1, "d": self.seq})
            except Exception:
                pass
        elif op == 7:  # Reconnect
            _log.info(f"[user_gw] user={self.user_id} server pediu RECONNECT")
            try:
                await self.ws.close(code=4000)
            except Exception:
                pass
        elif op == 9:  # Invalid Session
            resumable = bool(d) if isinstance(d, bool) else False
            _log.warning(f"[user_gw] user={self.user_id} INVALID_SESSION resumable={resumable}")
            await asyncio.sleep(2)
            try:
                await self.ws.close(code=4000)
            except Exception:
                pass
        elif op == 10:  # Hello (já tratado fora do loop)
            pass
        elif op == 11:  # Heartbeat ACK
            pass

    async def _heartbeat_loop(self):
        # Primeiro batimento com jitter
        try:
            await asyncio.sleep(self.heartbeat_interval * 0.5)
            while self.ws and not self.ws.closed:
                try:
                    await self.ws.send_json({"op": 1, "d": self.seq})
                except Exception as e:
                    _log.warning(f"[user_gw] user={self.user_id} heartbeat send erro: {e}")
                    return
                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            raise

    def _handle_event(self, t: str, d: dict):
        if t == "READY":
            self.session_id = d.get("session_id")
            count = 0
            for g in d.get("guilds", []):
                for th in g.get("threads", []) or []:
                    self._add_thread(th)
                    count += 1
            self.threads_count = count
            _log.info(f"[user_gw] user={self.user_id} READY threads={count}")

        elif t == "THREAD_CREATE":
            self._add_thread(d)
            _log.info(f"[user_gw] user={self.user_id} THREAD_CREATE id={d.get('id')} parent={d.get('parent_id')} name={d.get('name')!r}")

        elif t == "THREAD_UPDATE":
            old_name = None
            cache = _threads_cache.get(str(d.get("parent_id") or ""), {})
            old = cache.get(str(d.get("id") or ""))
            if old:
                old_name = old.get("name")
            self._add_thread(d)
            if old_name and old_name != d.get("name"):
                _log.info(f"[user_gw] user={self.user_id} THREAD_UPDATE id={d.get('id')} {old_name!r} → {d.get('name')!r}")

        elif t == "THREAD_DELETE":
            self._remove_thread(d.get("id"), d.get("parent_id"))
            _log.info(f"[user_gw] user={self.user_id} THREAD_DELETE id={d.get('id')}")

        elif t == "THREAD_LIST_SYNC":
            count = 0
            for th in d.get("threads", []) or []:
                self._add_thread(th)
                count += 1
            if count:
                _log.info(f"[user_gw] user={self.user_id} THREAD_LIST_SYNC guild={d.get('guild_id')} count={count}")

        elif t == "MESSAGE_CREATE":
            # Filtro barato: só dispara callback se a msg começa com "pg ".
            # Assim 99% das msgs (chat normal) são descartadas em <1µs e não
            # geram task overhead. O resto (parsing, busca PIX, etc) é feito
            # na callback do pg_polling.
            content = (d.get("content") or "")
            if len(content) > 3 and content[:3].lower() == "pg ":
                if _on_message_cb is not None:
                    try:
                        asyncio.create_task(_on_message_cb(self.user_id, d))
                    except Exception as e:
                        _log.warning(f"[user_gw] msg_cb erro: {e}")

        # Demais eventos (MESSAGE_CREATE, GUILD_CREATE, etc) ignorados — caro
        # processar, e o polling REST cuida de mensagens.

    def _add_thread(self, th: dict):
        tid = th.get("id")
        pid = th.get("parent_id")
        if not tid or not pid:
            return
        cache = _threads_cache.setdefault(str(pid), {})
        cache[str(tid)] = th

    def _remove_thread(self, tid, pid):
        if not tid:
            return
        if pid:
            cache = _threads_cache.get(str(pid), {})
            cache.pop(str(tid), None)
        else:
            # Sem parent — busca em todas
            for cache in _threads_cache.values():
                cache.pop(str(tid), None)


class _AuthFailed(Exception):
    pass


class UserGatewayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._main_task: asyncio.Task | None = None

    async def cog_load(self):
        # ─── KILL SWITCH ────────────────────────────────────────────
        # Se TOKEN_MODE_ENABLED=False, NÃO abre nenhuma sessão WS com
        # token de usuário. Evita o IP ser detectado pelo anti-abuse
        # do Discord (Cloudflare 1015).
        try:
            import config as _config
            if not getattr(_config, "TOKEN_MODE_ENABLED", False):
                _log.warning("[user_gw] TOKEN_MODE_ENABLED=False — main_loop NÃO iniciado")
                return
        except Exception:
            pass

        self._main_task = asyncio.create_task(self._main_loop())

    def cog_unload(self):
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()
        for sess in list(_sessions.values()):
            sess.stop()
        _sessions.clear()

    async def _main_loop(self):
        # Espera o bot ficar pronto e o pg_polling assentar
        await self.bot.wait_until_ready()
        await asyncio.sleep(20)
        _log.info("[user_gw] iniciando — sincronizando sessões")
        while True:
            try:
                await self._sync_sessions()
            except Exception as e:
                _log.warning(f"[user_gw] _sync_sessions erro: {e}")
            await asyncio.sleep(60)  # re-sincroniza a cada 1min

    async def _sync_sessions(self):
        """Inicia sessões pra mediadores ativos, encerra pros desativados."""
        ativos = await asyncio.to_thread(token_mode_listar_todos)
        ativos_ids: dict[str, str] = {}  # user_id -> token
        for u in ativos:
            if not u.get("ativo"):
                continue
            cfg = await asyncio.to_thread(token_mode_get, u["user_id"])
            tok = cfg.get("token")
            if tok:
                ativos_ids[u["user_id"]] = tok

        # Encerra sessões pra users que não estão mais ativos
        for uid in list(_sessions.keys()):
            if uid not in ativos_ids:
                sess = _sessions.pop(uid, None)
                if sess:
                    sess.stop()
                    _log.info(f"[user_gw] user={uid} sessão encerrada (não ativo)")

        # Inicia sessões pra novos (com pequeno delay entre cada pra não levantar
        # 11 conexões WS simultâneas do mesmo IP)
        novos = []
        for uid, tok in ativos_ids.items():
            if uid in _sessions:
                # Já tem sessão. Se o token mudou, reinicia.
                if _sessions[uid].token != tok:
                    _sessions[uid].stop()
                    _log.info(f"[user_gw] user={uid} token mudou — reiniciando sessão")
                    _sessions[uid] = self._spawn(uid, tok)
                continue
            novos.append((uid, tok))

        for uid, tok in novos:
            _sessions[uid] = self._spawn(uid, tok)
            _log.info(f"[user_gw] user={uid} sessão iniciada")
            await asyncio.sleep(0.5)

    def _spawn(self, uid: str, tok: str) -> UserGatewaySession:
        sess = UserGatewaySession(uid, tok)
        # task gerenciada pela própria sessão; armazenamos a sessão pra controle
        asyncio.create_task(sess.run(), name=f"user_gw_{uid}")
        return sess


async def setup(bot):
    await bot.add_cog(UserGatewayCog(bot))
