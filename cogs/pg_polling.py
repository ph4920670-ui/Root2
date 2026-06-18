# cogs/pg_polling.py — Polling com token de user pra detectar 'pg Nome' em qualquer servidor
import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord.ext import commands, tasks

from utils import pix_credentials as pc
from utils.pix_consulta import listar_pix_recebidos
from utils.database import token_mode_listar_todos, token_mode_get
from cogs.token_mode import enviar_como_usuario, listar_guilds_user

_log = logging.getLogger("salasff.pg_polling")

POLL_INTERVAL = 3
JANELA_MIN_PIX = 10
PG_RE = re.compile(r"^\s*pg\s+(.+)$", re.IGNORECASE)
MAX_TAMANHO_NOME = 80
_processadas: dict[str, set[str]] = {}
_confirmados: set[str] = set()
MAX_PROCESSADAS = 500

# Cooldown anti-429 pro endpoint archived/private (canal_id -> last_call_ts)
_arq_cooldown: dict[str, float] = {}
ARQ_COOLDOWN_SEC = 60  # só chama 1x/minuto por canal pai

# Cache persistente (em memória) de threads já descobertas, por canal pai.
# Uma vez encontrada, nunca mais perde — mesmo que saia da janela de 100 msgs.
# canal_pai_id -> {thread_id: thread_obj}
_threads_cache: dict[str, dict[str, dict]] = {}

# Marca canal pai onde já fizemos o "deep scan" inicial (paginação histórica).
# Só roda 1x na vida do bot por canal pai.
_deep_scan_done: set[str] = set()

# Cache da lista de PIX recebidos por user. Evita refazer busca pesada (Gmail
# fetch de N emails, refresh OAuth, etc) em rajadas tipo "pg breno pg breno
# pg breno". TTL curto (5s) garante que PIX novo aparece quase em tempo real.
# key = (banco, user_id, guild_id) → (timestamp, list_pix)
_pix_cache: dict[tuple, tuple[float, list]] = {}
PIX_CACHE_TTL = 5.0

import unicodedata

def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _tokens(s: str) -> set[str]:
    return {t for t in _normalizar(s).split() if len(t) >= 2}

def _match_score(busca: str, nome_pix: str) -> float:
    tb = _tokens(busca)
    tn = _tokens(nome_pix)
    if not tb or not tn:
        return 0.0
    return 1.0 if tb.issubset(tn) else 0.0


async def _get_threads_canal(token: str, canal_id: str) -> list[dict]:
    """Busca threads ativas de um canal usando token de usuário."""
    threads = []
    try:
        async with aiohttp.ClientSession() as s:
            # Threads ativas do canal pai
            async with s.get(
                f"https://discord.com/api/v10/channels/{canal_id}/threads/active",
                headers={"Authorization": token},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    threads.extend(data.get("threads", []))
    except Exception as e:
        _log.warning(f"[polling] threads canal={canal_id}: {e}")
    return threads


async def _resolve_canais_pai(token: str, ids: list[str], fallback_guild_id: str = "0") -> tuple[list[tuple[str, str]], list[tuple[str, str, dict]]]:
    """Pra cada ID, descobre o tipo:
    - Canal de texto (type 0/5/15) → vai pra `canais_pai` (precisa descobrir threads dentro)
    - Categoria (type 4) → expande pros canais filhos (vão pra `canais_pai`)
    - Thread (type 11/12) → vai pra `threads_diretas` (processa direto, sem descoberta)
    Retorna (canais_pai, threads_diretas), ambos deduplicados.
    """
    canais_pai: list[tuple[str, str]] = []
    threads_diretas: list[tuple[str, str, dict]] = []  # (thread_id, guild_id, info)
    seen: set[str] = set()
    children_cache: dict[str, list[dict]] = {}

    async with aiohttp.ClientSession() as s:
        for cid in ids:
            try:
                async with s.get(
                    f"https://discord.com/api/v10/channels/{cid}",
                    headers={"Authorization": token},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as r:
                    if r.status != 200:
                        _log.warning(f"[polling] resolve {cid} GET channel status={r.status}")
                        continue
                    info = await r.json()
                tipo = info.get("type")
                gid = str(info.get("guild_id") or fallback_guild_id)
                nome = info.get("name") or "?"

                if tipo == 4:
                    # Categoria — expande pros filhos
                    if gid not in children_cache:
                        try:
                            async with s.get(
                                f"https://discord.com/api/v10/guilds/{gid}/channels",
                                headers={"Authorization": token},
                                timeout=aiohttp.ClientTimeout(total=8),
                            ) as r:
                                children_cache[gid] = await r.json() if r.status == 200 else []
                        except Exception:
                            children_cache[gid] = []
                    filhos = [
                        c for c in children_cache[gid]
                        if str(c.get("parent_id")) == str(cid)
                        and c.get("type") in (0, 5, 15)
                    ]
                    _log.info(f"[polling] cat={cid} '{nome}' → {len(filhos)} canais: {[c.get('name') for c in filhos]}")
                    for c in filhos:
                        if c["id"] not in seen:
                            seen.add(c["id"])
                            canais_pai.append((c["id"], gid))
                elif tipo in (0, 5, 15):
                    if cid not in seen:
                        seen.add(cid)
                        canais_pai.append((cid, gid))
                        _log.info(f"[polling] canal={cid} '{nome}' type={tipo}")
                elif tipo in (10, 11, 12):  # 10=NEWS_THREAD, 11=PUBLIC_THREAD, 12=PRIVATE_THREAD
                    if cid not in seen:
                        seen.add(cid)
                        threads_diretas.append((cid, gid, info))
                        _log.info(f"[polling] thread direta cid={cid} '{nome}' type={tipo}")
                else:
                    _log.info(f"[polling] {cid} type={tipo} ignorado (não é canal, categoria nem thread)")
            except Exception as e:
                _log.warning(f"[polling] resolve {cid} erro: {e}")

    return canais_pai, threads_diretas


class PgPollingCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Cache de gmail_creds por user — evita refazer listar_guilds_user/get_creds_guild
        # toda vez que chega um MESSAGE_CREATE. TTL razoável: creds não muda toda hora.
        self._gmail_creds_cache: dict[str, tuple[float, dict]] = {}

        # ─── KILL SWITCH ────────────────────────────────────────────
        # Se TOKEN_MODE_ENABLED=False, NÃO inicia nada. O cog é carregado
        # mas fica inerte — preserva imports do código antigo sem fazer
        # nenhuma requisição com token de usuário.
        try:
            import config as _config
            if not getattr(_config, "TOKEN_MODE_ENABLED", False):
                _log.warning("[polling] TOKEN_MODE_ENABLED=False — task de polling NÃO iniciada")
                return
        except Exception:
            pass

        self.task_polling.start()
        # Registra callback no gateway pra detecção instantânea de "pg X"
        try:
            from cogs.pg_user_gateway import register_message_callback
            register_message_callback(self._on_gateway_message)
            _log.info("[polling] callback MESSAGE_CREATE registrada no gateway")
        except Exception as e:
            _log.warning(f"[polling] não conseguiu registrar callback no gateway: {e}")

    def cog_unload(self):
        try:
            self.task_polling.cancel()
        except Exception:
            pass

    async def _get_gmail_creds(self, user_id: str, token: str) -> dict | None:
        """Pega gmail_creds com cache de 60s por user."""
        agora = time.monotonic()
        cached = self._gmail_creds_cache.get(user_id)
        if cached and (agora - cached[0]) < 60:
            return cached[1]
        gmail_creds = None
        try:
            guilds_token = await listar_guilds_user(token)
            for g in guilds_token:
                try:
                    creds = pc.get_creds_guild(int(g["id"]), "gmail")
                    if creds and creds.get("refresh_token"):
                        gmail_creds = creds
                        break
                except Exception:
                    continue
        except Exception:
            pass
        if not gmail_creds:
            try:
                _, gmail_creds = await asyncio.to_thread(pc.get_creds_fallback)
            except Exception:
                pass
        if gmail_creds:
            self._gmail_creds_cache[user_id] = (agora, gmail_creds)
        return gmail_creds

    async def _on_gateway_message(self, user_id: str, msg: dict):
        """Chamada pelo user_gw quando chega um MESSAGE_CREATE com 'pg ' no
        content. Processa imediatamente, sem esperar o round do polling."""
        try:
            channel_id = str(msg.get("channel_id") or "")
            guild_id = str(msg.get("guild_id") or "")
            content = (msg.get("content") or "").strip()
            mid = str(msg.get("id") or "")
            if not channel_id or not content or not mid:
                return

            # Dedup: marca como já processada pra o polling não retentar
            cache_proc = _processadas.setdefault(channel_id, set())
            if mid in cache_proc:
                return
            cache_proc.add(mid)

            mt = PG_RE.match(content)
            if not mt:
                return
            nome_busca = mt.group(1).strip()
            if len(nome_busca) < 2 or len(nome_busca) > MAX_TAMANHO_NOME:
                return

            # Pega o token do user
            cfg = await asyncio.to_thread(token_mode_get, user_id)
            token = cfg.get("token")
            if not token or not cfg.get("ativo"):
                return

            toks_busca = _tokens(nome_busca)
            if len(toks_busca) < 2:
                _log.info(f"[gw_msg] user={user_id} PG sem sobrenome ch={channel_id} busca='{nome_busca}'")
                txt = (
                    f"⚠️ ⠀Digite **nome e sobrenome**\n"
                    f"-# Ex: `pg Carlos Silva`"
                )
                try:
                    await enviar_como_usuario(token, int(channel_id), txt)
                except Exception as e:
                    _log.warning(f"[gw_msg] erro aviso sobrenome: {e}")
                return

            # Pega Gmail creds (cacheado)
            gmail_creds = await self._get_gmail_creds(user_id, token)
            if not gmail_creds:
                _log.info(f"[gw_msg] user={user_id} sem Gmail configurado")
                return

            author_name = msg.get("author", {}).get("username", "?")
            _log.info(f"[gw_msg] user={user_id} PG ch={channel_id} author={author_name} busca='{nome_busca}'")
            await self._tentar_confirmar(token, channel_id, guild_id, nome_busca, gmail_creds)
        except Exception as e:
            _log.warning(f"[gw_msg] erro: {e}")

    @tasks.loop(seconds=POLL_INTERVAL)
    async def task_polling(self):
        _log.info(f"[polling] === round iniciando ===")
        try:
            await self._round()
        except Exception as e:
            _log.error(f"[polling] erro round: {e}", exc_info=True)
        _log.info(f"[polling] === round terminou ===")

    @task_polling.before_loop
    async def before_polling(self):
        await self.bot.wait_until_ready()
        _log.info("[polling] before_loop: bot pronto, aguardando 15s antes do 1º round")
        await asyncio.sleep(15)
        _log.info("[polling] iniciando primeiro round")

    async def _round(self):
        ativos = await asyncio.to_thread(token_mode_listar_todos)
        ativos_filtro = [u for u in ativos if u.get("ativo")]
        if not ativos_filtro:
            return
        _log.info(f"[polling] round: {len(ativos_filtro)} user(s) com token ativo")

        # Carrega configs em paralelo
        async def _carregar(u):
            cfg = await asyncio.to_thread(token_mode_get, u["user_id"])
            return u["user_id"], cfg.get("token")

        configs = await asyncio.gather(*[_carregar(u) for u in ativos_filtro])

        # Processa users em paralelo — cada token tem seu próprio bucket de rate
        # limit no Discord, então paralelizar entre users é seguro. Internamente
        # cada user ainda processa seus canais em série (rate limit por bucket).
        async def _safe(uid, tok):
            if not tok:
                return
            try:
                await self._processar_user(uid, tok)
            except Exception as e:
                _log.warning(f"[polling] user={uid} erro: {e}")

        await asyncio.gather(*[_safe(uid, tok) for uid, tok in configs])

    async def _processar_user(self, user_id: str, token: str):
        try:
            from cogs.mediador_painel import _get_mediador_doc
            doc = await asyncio.to_thread(_get_mediador_doc, user_id)
        except Exception as e:
            _log.warning(f"[polling] user={user_id} erro doc: {e}")
            return

        orgs = doc.get("orgs", [])
        canais_pai = doc.get("canais_extras", [])  # canais pai cadastrados

        _log.info(f"[polling] user={user_id} orgs={len(orgs)} canais_extras={len(canais_pai)}")

        if not orgs and not canais_pai:
            _log.info(f"[polling] user={user_id} sem orgs nem canais cadastrados — pulando")
            return

        # Busca Gmail
        gmail_creds = None
        try:
            guilds_token = await listar_guilds_user(token)
            for g in guilds_token:
                try:
                    creds = pc.get_creds_guild(int(g["id"]), "gmail")
                    if creds and creds.get("refresh_token"):
                        gmail_creds = creds
                        break
                except Exception:
                    continue
        except Exception:
            pass

        if not gmail_creds:
            try:
                _, gmail_creds = await asyncio.to_thread(pc.get_creds_fallback)
            except Exception:
                pass

        if not gmail_creds:
            _log.info(f"[polling] user={user_id} sem Gmail configurado — pulando")
            return

        # === FAST PATH: gateway WS já populou o _threads_cache ===
        # Quando o user tem sessão WS ativa (READY recebido), o cache já tem
        # todas as threads onde ele é membro — alimentado em tempo real por
        # THREAD_CREATE/UPDATE/LIST_SYNC. Não precisa fazer descoberta REST
        # nenhuma; basta ler mensagens das threads do cache. Reduz round de
        # ~30 chamadas HTTP por user pra ~N (uma por thread ativa).
        try:
            from cogs.pg_user_gateway import gateway_ready
            gw_ok = gateway_ready(user_id)
        except Exception:
            gw_ok = False

        if gw_ok:
            # Coleta todas as threads do cache que pertencem às orgs do user
            # OU às threads diretas/descendentes dos canais_extras.
            orgs_set = {str(g) for g in orgs}
            canais_extras_set = {str(c) for c in canais_pai}
            alvos: list[tuple[str, str]] = []  # (channel_id, guild_id)

            for parent_id, threads in _threads_cache.items():
                for tid, th in threads.items():
                    th_guild = str(th.get("guild_id") or "")
                    nome = (th.get("name") or "").lower()
                    # Aceita thread se:
                    #   (a) guild da thread tá nas orgs do user E nome começa com "fila"
                    #   (b) parent_id é um canal_extra cadastrado (qualquer nome)
                    #   (c) tid em si é um canal_extra (thread cadastrada direto)
                    aceitar = False
                    if th_guild in orgs_set and nome.startswith("fila"):
                        aceitar = True
                    elif parent_id in canais_extras_set:
                        aceitar = True
                    elif tid in canais_extras_set:
                        aceitar = True
                    if aceitar:
                        alvos.append((tid, th_guild or (orgs[0] if orgs else "0")))

            if alvos:
                _log.info(f"[polling] user={user_id} FAST_PATH gw=ready threads={len(alvos)}")
                # Processa todas em paralelo (cada thread = 1 GET messages)
                async def _safe_ch(tid, gid):
                    try:
                        await self._processar_canal(token, user_id, gid, tid, gmail_creds)
                    except Exception as e:
                        _log.warning(f"[polling] fast_path ch={tid} erro: {e}")
                await asyncio.gather(*[_safe_ch(tid, gid) for tid, gid in alvos])
            else:
                _log.info(f"[polling] user={user_id} FAST_PATH gw=ready 0 threads no cache")
            return

        # === SLOW PATH: gateway não ativo — usa descoberta REST tradicional ===
        _log.info(f"[polling] user={user_id} SLOW_PATH gw=off — descoberta REST")

        total_fila = 0

        # --- Orgs: busca threads + entra automaticamente nas novas fila-* ---
        for guild_id in orgs:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(
                        f"https://discord.com/api/v10/guilds/{guild_id}/threads/active",
                        headers={"Authorization": token},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        threads_conhecidas = {t["id"]: t for t in ((await r.json()).get("threads", []) if r.status == 200 else [])}

                    async with s.get(
                        f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                        headers={"Authorization": token},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        canais = await r.json() if r.status == 200 else []

                    for ch in canais:
                        if ch.get("type") not in (0, 5):
                            continue
                        # Threads ativas do canal
                        async with s.get(
                            f"https://discord.com/api/v10/channels/{ch['id']}/threads/active",
                            headers={"Authorization": token},
                            timeout=aiohttp.ClientTimeout(total=6),
                        ) as r:
                            ativas_ch = (await r.json()).get("threads", []) if r.status == 200 else []

                        # Threads PRIVADAS arquivadas onde o user é membro
                        try:
                            async with s.get(
                                f"https://discord.com/api/v10/channels/{ch['id']}/users/@me/threads/archived/private?limit=10",
                                headers={"Authorization": token},
                                timeout=aiohttp.ClientTimeout(total=6),
                            ) as r:
                                arq_ch = (await r.json()).get("threads", []) if r.status == 200 else []
                        except Exception:
                            arq_ch = []

                        for t in (ativas_ch + arq_ch):
                            if t["id"] in threads_conhecidas:
                                continue
                            if not (t.get("name") or "").lower().startswith("fila"):
                                continue
                            async with s.put(
                                f"https://discord.com/api/v10/channels/{t['id']}/thread-members/@me",
                                headers={"Authorization": token},
                                timeout=aiohttp.ClientTimeout(total=6),
                            ) as jr:
                                if jr.status in (200, 201, 204):
                                    _log.info(f"[polling] entrou na thread {t['name']} ({t['id']})")
                                    threads_conhecidas[t["id"]] = t
                                else:
                                    body = (await jr.text())[:120]
                                    _log.warning(f"[polling] join {t.get('name')} status={jr.status} body={body}")

                fila_threads = [t for t in threads_conhecidas.values() if (t.get("name") or "").lower().startswith("fila")]
                if fila_threads:
                    _log.info(f"[polling] org={guild_id} {len(fila_threads)} fila*: {[t['name'] for t in fila_threads]}")
                total_fila += len(fila_threads)
                for t in fila_threads:
                    await self._processar_canal(token, user_id, str(guild_id), t["id"], gmail_creds)
            except Exception as e:
                _log.warning(f"[polling] org={guild_id} erro: {e}")

        # --- Canais pai: descoberta de threads via mensagens do canal pai ---
        # Aceita IDs de CANAIS (4x4-mobile), CATEGORIAS (mobile) ou TÓPICOS direto
        # (fila-310). Categoria expande pros filhos. Tópico processa direto sem
        # passar pelo pipeline de descoberta (escape hatch quando a descoberta
        # via REST falha — caso comum: thread privada com `with_message:false`).
        # Estratégia hierárquica para canais/categorias:
        #   T1)   Ler últimas msgs do canal pai → extrair threads de system messages
        #         type=18 (THREAD_CREATED) e do campo `thread`. Falha pra threads
        #         privadas criadas com `with_message: false` (não geram broadcast).
        #   T1.4) /channels/{id}/threads/active → threads ativas onde user é membro.
        #         Pega as privadas que T1 não vê (caso comum: bot dono cria thread
        #         privada e adiciona o user via menção, sem mensagem no parent).
        #   T1.5) DEEP SCAN one-shot: paginação histórica das mensagens do canal pai
        #         (500 msgs). Roda 1x na vida do bot por canal pai.
        #   T2)   Fallback: /guilds/{id}/threads/active (filtrado por parent_id)
        #   T3)   Último recurso (throttled): /users/@me/threads/archived/private
        fallback_gid = orgs[0] if orgs else "0"
        canais_resolvidos, threads_diretas = await _resolve_canais_pai(token, canais_pai, fallback_gid)
        if canais_pai and not canais_resolvidos and not threads_diretas:
            _log.info(f"[polling] user={user_id} nenhum canal/categoria/thread resolvido de {len(canais_pai)} ids")

        # Threads diretas: PUT thread-members/@me + processa direto, sem descoberta
        if threads_diretas:
            _log.info(f"[polling] user={user_id} {len(threads_diretas)} thread(s) direta(s): {[t[2].get('name') for t in threads_diretas]}")
            async with aiohttp.ClientSession() as s_dir:
                for tid, gid_t, info in threads_diretas:
                    try:
                        async with s_dir.put(
                            f"https://discord.com/api/v10/channels/{tid}/thread-members/@me",
                            headers={"Authorization": token},
                            timeout=aiohttp.ClientTimeout(total=6),
                        ) as jr:
                            if jr.status not in (200, 201, 204):
                                body = (await jr.text())[:120]
                                _log.warning(f"[polling] thread direta {tid} join status={jr.status} body={body}")
                    except Exception as ex_join:
                        _log.warning(f"[polling] thread direta {tid} join erro: {ex_join}")
            for tid, gid_t, _info in threads_diretas:
                await self._processar_canal(token, user_id, gid_t, tid, gmail_creds)

        guild_threads_cache: dict[str, list] = {}
        for canal_id, guild_id in canais_resolvidos:
            try:
                async with aiohttp.ClientSession() as s:
                    todas: dict[str, dict] = dict(_threads_cache.get(canal_id, {}))  # parte do cache persistente

                    # T1) Mensagens do canal pai → threads embutidas (limit=100)
                    novas_t1 = 0
                    try:
                        async with s.get(
                            f"https://discord.com/api/v10/channels/{canal_id}/messages?limit=100",
                            headers={"Authorization": token},
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as r:
                            if r.status == 200:
                                msgs_pai = await r.json()
                                for m in msgs_pai:
                                    th = m.get("thread")
                                    if isinstance(th, dict) and th.get("id"):
                                        if th["id"] not in todas:
                                            novas_t1 += 1
                                        todas[th["id"]] = th
                                    if m.get("type") == 18 and m.get("id"):
                                        tid = m["id"]
                                        if tid not in todas:
                                            try:
                                                async with s.get(
                                                    f"https://discord.com/api/v10/channels/{tid}",
                                                    headers={"Authorization": token},
                                                    timeout=aiohttp.ClientTimeout(total=6),
                                                ) as rt:
                                                    if rt.status == 200:
                                                        todas[tid] = await rt.json()
                                                        novas_t1 += 1
                                            except Exception:
                                                pass
                                _log.info(f"[polling] canal_pai={canal_id} via_msgs novas={novas_t1} cache_total={len(todas)}")
                            else:
                                _log.info(f"[polling] canal_pai={canal_id} GET msgs status={r.status}")
                    except Exception as ex_msgs:
                        _log.warning(f"[polling] canal_pai={canal_id} via_msgs erro: {ex_msgs}")

                    # T1.4) /channels/{cid}/threads/active — descobre threads ativas
                    # do canal onde o user (token) JÁ É MEMBRO. Funciona pra threads
                    # PRIVADAS criadas com `with_message: false` (que não geram
                    # THREAD_CREATED no canal pai e por isso T1 não vê).
                    try:
                        async with s.get(
                            f"https://discord.com/api/v10/channels/{canal_id}/threads/active",
                            headers={"Authorization": token},
                            timeout=aiohttp.ClientTimeout(total=8),
                        ) as r:
                            if r.status == 200:
                                data = await r.json()
                                ths = data.get("threads", [])
                                novas_t14 = 0
                                for t in ths:
                                    if t.get("id") and t["id"] not in todas:
                                        todas[t["id"]] = t
                                        novas_t14 += 1
                                _log.info(f"[polling] canal_pai={canal_id} ch/threads/active novas={novas_t14} (total_resp={len(ths)})")
                            elif r.status == 403:
                                body = (await r.text())[:160]
                                _log.info(f"[polling] canal_pai={canal_id} ch/threads/active 403 body={body}")
                            else:
                                body = (await r.text())[:120]
                                _log.info(f"[polling] canal_pai={canal_id} ch/threads/active status={r.status} body={body}")
                    except Exception as ex_t14:
                        _log.warning(f"[polling] canal_pai={canal_id} ch/threads/active erro: {ex_t14}")

                    # T1.5) DEEP SCAN: 1x na vida do bot, pagina histórico do canal pai
                    # pra achar filas antigas que estão fora da janela 100. Vai 5 páginas
                    # (500 msgs total) — suficiente pra cobrir filas criadas dias atrás.
                    if canal_id not in _deep_scan_done:
                        _deep_scan_done.add(canal_id)
                        novas_deep = 0
                        before = None
                        try:
                            for pagina in range(5):  # 500 msgs no total
                                url = f"https://discord.com/api/v10/channels/{canal_id}/messages?limit=100"
                                if before:
                                    url += f"&before={before}"
                                async with s.get(
                                    url,
                                    headers={"Authorization": token},
                                    timeout=aiohttp.ClientTimeout(total=10),
                                ) as r:
                                    if r.status != 200:
                                        break
                                    msgs_p = await r.json()
                                    if not msgs_p:
                                        break
                                    for m in msgs_p:
                                        th = m.get("thread")
                                        if isinstance(th, dict) and th.get("id") and th["id"] not in todas:
                                            todas[th["id"]] = th
                                            novas_deep += 1
                                        if m.get("type") == 18 and m.get("id") and m["id"] not in todas:
                                            tid = m["id"]
                                            try:
                                                async with s.get(
                                                    f"https://discord.com/api/v10/channels/{tid}",
                                                    headers={"Authorization": token},
                                                    timeout=aiohttp.ClientTimeout(total=6),
                                                ) as rt:
                                                    if rt.status == 200:
                                                        todas[tid] = await rt.json()
                                                        novas_deep += 1
                                            except Exception:
                                                pass
                                    before = msgs_p[-1]["id"]
                                    await asyncio.sleep(0.5)  # respira pra não tomar 429
                            _log.info(f"[polling] canal_pai={canal_id} DEEP_SCAN novas={novas_deep} cache_total={len(todas)}")
                        except Exception as ex_ds:
                            _log.warning(f"[polling] canal_pai={canal_id} deep_scan erro: {ex_ds}")

                    # T2) Guild-level (filtra por parent_id) — cacheado por guild
                    if guild_id not in guild_threads_cache:
                        try:
                            async with s.get(
                                f"https://discord.com/api/v10/guilds/{guild_id}/threads/active",
                                headers={"Authorization": token},
                                timeout=aiohttp.ClientTimeout(total=8),
                            ) as r:
                                if r.status == 200:
                                    guild_threads_cache[guild_id] = (await r.json()).get("threads", [])
                                else:
                                    guild_threads_cache[guild_id] = []
                                    body = (await r.text())[:160]
                                    _log.info(f"[polling] guild={guild_id} active status={r.status} body={body}")
                        except Exception:
                            guild_threads_cache[guild_id] = []
                    for t in guild_threads_cache[guild_id]:
                        if str(t.get("parent_id")) == str(canal_id):
                            todas.setdefault(t["id"], t)

                    # Sem filtro por nome: processa TODA thread sob o canal/categoria
                    # cadastrada (ex: tópicos que começam como "aguardando-X" e
                    # depois viram "fila-X" — se filtrasse, perderia a fase inicial).
                    threads_proc = list(todas.values())

                    # T3) Só se NADA achado: archived private (com cooldown anti-429)
                    if not threads_proc:
                        agora_ts = time.time()
                        last = _arq_cooldown.get(canal_id, 0)
                        if agora_ts - last >= ARQ_COOLDOWN_SEC:
                            _arq_cooldown[canal_id] = agora_ts
                            try:
                                async with s.get(
                                    f"https://discord.com/api/v10/channels/{canal_id}/users/@me/threads/archived/private?limit=10",
                                    headers={"Authorization": token},
                                    timeout=aiohttp.ClientTimeout(total=8),
                                ) as r:
                                    if r.status == 200:
                                        for t in (await r.json()).get("threads", []):
                                            todas.setdefault(t["id"], t)
                                        _log.info(f"[polling] canal_pai={canal_id} priv-archived (fallback)")
                                    elif r.status == 429:
                                        _log.info(f"[polling] canal_pai={canal_id} priv-archived 429 (rate limit)")
                                        _arq_cooldown[canal_id] = agora_ts + 30  # backoff 30s
                                    else:
                                        _log.info(f"[polling] canal_pai={canal_id} priv-archived status={r.status}")
                            except Exception as ex_arq:
                                _log.warning(f"[polling] canal_pai={canal_id} priv-archived erro: {ex_arq}")
                        threads_proc = list(todas.values())

                    # PUT thread-members/@me em cada thread (idempotente)
                    for t in threads_proc:
                        try:
                            async with s.put(
                                f"https://discord.com/api/v10/channels/{t['id']}/thread-members/@me",
                                headers={"Authorization": token},
                                timeout=aiohttp.ClientTimeout(total=6),
                            ) as jr:
                                if jr.status in (200, 201, 204):
                                    _log.info(f"[polling] canal_pai={canal_id} entrou na thread {t.get('name')} ({t['id']})")
                                else:
                                    body = (await jr.text())[:120]
                                    _log.warning(f"[polling] canal_pai={canal_id} join {t.get('name')} status={jr.status} body={body}")
                        except Exception as ex_join:
                            _log.warning(f"[polling] canal_pai={canal_id} join {t.get('name')} erro: {ex_join}")

                if threads_proc:
                    # Persiste no cache: uma vez achada, nunca mais perde
                    cache_canal = _threads_cache.setdefault(canal_id, {})
                    for t in threads_proc:
                        cache_canal[t["id"]] = t
                    _log.info(f"[polling] canal_pai={canal_id} {len(threads_proc)} threads: {[t.get('name') for t in threads_proc]}")
                total_fila += len(threads_proc)
                for t in threads_proc:
                    await self._processar_canal(token, user_id, str(guild_id), t["id"], gmail_creds)
            except Exception as e:
                _log.warning(f"[polling] canal_pai={canal_id} erro: {e}")

        _log.info(f"[polling] user={user_id} total threads: {total_fila}")

    async def _processar_canal(self, token: str, user_id: str, guild_id: str, channel_id: str, gmail_creds: dict):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages?limit=25",
                    headers={"Authorization": token},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status == 404:
                        # Thread foi deletada — remove do cache de qualquer canal pai
                        for cp in list(_threads_cache.keys()):
                            _threads_cache[cp].pop(channel_id, None)
                        _log.info(f"[polling] thread {channel_id} 404 → removida do cache")
                        return
                    if r.status != 200:
                        body = (await r.text())[:120]
                        _log.warning(f"[polling] msgs ch={channel_id} status={r.status} body={body}")
                        return
                    msgs = await r.json()
        except Exception as e:
            _log.warning(f"[polling] msgs ch={channel_id}: {e}")
            return

        cache = _processadas.setdefault(channel_id, set())
        agora = datetime.now(timezone.utc)
        janela = timedelta(minutes=5)

        for m in reversed(msgs):
            mid = m.get("id", "")
            try:
                ts = datetime.fromtimestamp(
                    ((int(mid) >> 22) + 1420070400000) / 1000, tz=timezone.utc
                )
                if agora - ts > janela:
                    continue
            except Exception:
                pass

            if mid in cache:
                continue
            cache.add(mid)
            if len(cache) > MAX_PROCESSADAS:
                cache_list = list(cache)
                _processadas[channel_id] = set(cache_list[-MAX_PROCESSADAS // 2:])

            content = (m.get("content") or "").strip()
            if not content:
                continue

            mt = PG_RE.match(content)
            if not mt:
                continue

            nome_busca = mt.group(1).strip()
            if len(nome_busca) < 2 or len(nome_busca) > MAX_TAMANHO_NOME:
                continue

            # Exige pelo menos nome + sobrenome (2 tokens normalizados)
            toks_busca = _tokens(nome_busca)
            if len(toks_busca) < 2:
                _log.info(f"[polling] PG sem sobrenome ch={channel_id} busca='{nome_busca}' — enviando aviso")
                txt = (
                    f"⚠️ ⠀Digite **nome e sobrenome**\n"
                    f"-# Ex: `pg Carlos Silva`"
                )
                try:
                    msg_id = await enviar_como_usuario(token, int(channel_id), txt)
                    _log.info(f"[polling] aviso sobrenome enviado ch={channel_id} msg_id={msg_id}")
                except Exception as e:
                    _log.warning(f"[polling] erro enviando aviso sobrenome: {e}")
                continue

            author_name = m.get("author", {}).get("username", "?")
            _log.info(f"[polling] PG DETECTADO ch={channel_id} author={author_name} busca='{nome_busca}'")
            await self._tentar_confirmar(token, channel_id, guild_id, nome_busca, gmail_creds)

    async def _tentar_confirmar(self, token: str, channel_id: str, guild_id: str, nome_busca: str, gmail_creds: dict):
        # Cache por (refresh_token, guild_id) — mesmos creds e mesma guild =
        # mesma lista de PIX. TTL curto evita rajadas refazerem a busca pesada.
        rt = (gmail_creds or {}).get("refresh_token", "")
        cache_key = ("gmail", rt, guild_id)
        agora = time.monotonic()
        cached = _pix_cache.get(cache_key)
        if cached and (agora - cached[0]) < PIX_CACHE_TTL:
            pix_list = cached[1]
            _log.debug(f"[polling] pix_list cache HIT key=({guild_id}) ({len(pix_list)} pix)")
        else:
            try:
                pix_list = await listar_pix_recebidos(
                    "gmail", gmail_creds,
                    guild_id=int(guild_id) if guild_id.isdigit() else 0,
                )
            except Exception as e:
                _log.warning(f"[polling] erro busca pix: {e}")
                return
            _pix_cache[cache_key] = (agora, pix_list)
            # Limpa entradas antigas pra não vazar memória
            if len(_pix_cache) > 100:
                cutoff = agora - PIX_CACHE_TTL
                for k in list(_pix_cache.keys()):
                    if _pix_cache[k][0] < cutoff:
                        _pix_cache.pop(k, None)

        disponiveis = [p for p in pix_list if p.get("id") not in _confirmados]
        candidatos = [p for p in disponiveis if _match_score(nome_busca, p.get("nome", "")) >= 0.5]

        if not candidatos:
            _log.info(f"[polling] sem match pra '{nome_busca}' ({len(disponiveis)} pix) — nomes: {[p.get('nome','?') for p in disponiveis[:5]]}")
            txt = f"❌ ⠀Não encontrei pagamento para **{nome_busca}**"
            try:
                msg_id = await enviar_como_usuario(token, int(channel_id), txt)
                _log.info(f"[polling] aviso 'não encontrei' enviado ch={channel_id} msg_id={msg_id}")
            except Exception as e:
                _log.warning(f"[polling] erro enviando aviso 'não encontrei': {e}")
            return

        if len(candidatos) > 1:
            _log.info(f"[polling] ambíguo pra '{nome_busca}': {len(candidatos)} matches")
            txt = (
                f"⚠️ ⠀Encontrei **{len(candidatos)}** pagamentos com esse nome\n"
                f"-# Adicione o **nome do meio**. Ex: `pg Carlos Antonio Silva`"
            )
            try:
                msg_id = await enviar_como_usuario(token, int(channel_id), txt)
                _log.info(f"[polling] aviso 'ambíguo' enviado ch={channel_id} msg_id={msg_id}")
            except Exception as e:
                _log.warning(f"[polling] erro enviando aviso 'ambíguo': {e}")
            return

        c = candidatos[0]
        _confirmados.add(c.get("id", ""))
        try:
            valor = float(c.get("valor", 0))
        except Exception:
            valor = 0.0
        valor_fmt = f"{valor:.2f}".replace(".", ",")

        txt = (
            f"## ✅ ⠀**PAGAMENTO CONFIRMADO**\n"
            f"> 🔹 ⠀**Nome** ⠀⠀{c.get('nome', '?')}\n"
            f"> 🔹 ⠀**Valor** ⠀⠀R$ {valor_fmt}"
        )
        msg_id = await enviar_como_usuario(token, int(channel_id), txt)
        if msg_id:
            _log.info(f"[polling] ✅ confirmado: {c.get('nome')} R$ {valor_fmt} (msg_id={msg_id})")
        else:
            _log.warning(f"[polling] falha ao enviar confirmação")


async def setup(bot):
    await bot.add_cog(PgPollingCog(bot))
