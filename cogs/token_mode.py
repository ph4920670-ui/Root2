"""
cogs/token_mode.py — Token Mode (Components V2 + emojis do bot)
"""

import asyncio
import logging
import aiohttp

import discord
from discord.ext import commands

import config
from utils.database import token_mode_get, token_mode_set_token, token_mode_set_ativo, token_mode_log_channel_get
from utils.emojis import PE

_log = logging.getLogger("salasff.token_mode")


# ─── Log no canal definido em /mod ──────────────────────────
async def _enviar_log(bot, user: discord.User, acao: str, detalhe: str = ""):
    """Posta um embed no canal de log configurado em /mod (Logs Token Mode).
    acao: 'config' | 'ativar' | 'desativar' | 'inválido'
    """
    try:
        ch_id = await asyncio.to_thread(token_mode_log_channel_get)
        _log.info(f"[tklog] acao={acao} user={user.id} ch_id={ch_id}")
        if not ch_id:
            _log.warning("[tklog] canal de log não configurado em /mod (token_mode_log_channel = None)")
            return
        ch = bot.get_channel(int(ch_id))
        if not ch:
            _log.info(f"[tklog] get_channel({ch_id}) retornou None — tentando fetch_channel")
            try:
                ch = await bot.fetch_channel(int(ch_id))
            except Exception as ex_fetch:
                _log.warning(f"[tklog] fetch_channel({ch_id}) falhou: {ex_fetch}")
                return
        cores = {
            "config":    0x5865F2,
            "ativar":    0x57F287,
            "desativar": 0xFEE75C,
            "inválido":  0xFF4757,
        }
        titulos = {
            "config":    "🔑  Token Configurado",
            "ativar":    "✅  Token Mode Ativado",
            "desativar": "⏸️  Token Mode Desativado",
            "inválido":  "❌  Tentativa com Token Inválido",
        }
        em = discord.Embed(
            title=titulos.get(acao, "Token Mode"),
            color=cores.get(acao, 0x5865F2),
            timestamp=discord.utils.utcnow(),
        )
        em.add_field(name="Usuário", value=f"{user.mention}\n`{user.id}`", inline=True)
        em.add_field(name="Tag", value=f"`{user}`", inline=True)
        if detalhe:
            em.add_field(name="Detalhe", value=detalhe, inline=False)
        try:
            em.set_thumbnail(url=user.display_avatar.url)
        except Exception:
            pass
        try:
            await ch.send(embed=em)
            _log.info(f"[tklog] ✅ log enviado em #{getattr(ch, 'name', '?')} ({ch_id})")
        except discord.Forbidden as ex_perm:
            _log.warning(f"[tklog] sem permissão pra enviar em {ch_id}: {ex_perm}")
        except Exception as ex_send:
            _log.warning(f"[tklog] erro ao enviar embed em {ch_id}: {ex_send}")
    except Exception as e:
        _log.warning(f"[token_mode log] erro geral: {e}")


def _em(key):
    """Retorna emoji dict pra payload V2. Retorna None se a chave não existe."""
    try:
        em = PE[key]
        return {"id": str(em.id), "name": em.name, "animated": em.animated}
    except (KeyError, AttributeError):
        _log.warning(f"[token_mode] emoji '{key}' não encontrado")
        return None


def _em_str(key):
    """Retorna string '<:name:id>' do emoji ou string vazia se não existe."""
    e = _em(key)
    if not e:
        return ""
    prefix = "a" if e["animated"] else ""
    return f"<{prefix}:{e['name']}:{e['id']}>"


# ─── Valida token via API do Discord ────────────────────────
async def _validar_token(token: str) -> dict | None:
    # ─── KILL SWITCH ────────────────────────────────────────────────
    # Mesmo a validação faz GET /users/@me com Authorization=token.
    # Se a flag tá OFF, nem deixa novos tokens serem cadastrados —
    # responde como se o token fosse inválido.
    try:
        import config as _config
        if not getattr(_config, "TOKEN_MODE_ENABLED", False):
            _log.info("[validar_token] TOKEN_MODE_ENABLED=False — recusando validação")
            return None
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": token},
                timeout=aiohttp.ClientTimeout(total=6),
            ) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        _log.warning(f"[validar_token] {e}")
    return None


# Pool de sessions e locks por canal pra acelerar e respeitar rate limit
# - Session por token: evita TLS handshake a cada POST (~500ms→~50ms)
# - Lock por channel_id: serializa envios pro mesmo canal (Discord aplica
#   rate limit por canal). Canais diferentes em paralelo.
# - 429 handling: lê retry_after do response e espera exatamente o que o
#   Discord pediu, sem chutar.
_session_pool: dict[str, aiohttp.ClientSession] = {}
_channel_locks: dict[int, asyncio.Lock] = {}


def _get_session(token: str) -> aiohttp.ClientSession:
    """Retorna ClientSession reutilizável por token. Cria sob demanda."""
    s = _session_pool.get(token)
    if s is None or s.closed:
        s = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
            connector=aiohttp.TCPConnector(limit=50, limit_per_host=20),
        )
        _session_pool[token] = s
    return s


def _get_channel_lock(channel_id: int) -> asyncio.Lock:
    lk = _channel_locks.get(channel_id)
    if lk is None:
        lk = asyncio.Lock()
        _channel_locks[channel_id] = lk
    return lk


async def enviar_como_usuario(token: str, channel_id: int, content: str) -> str | None:
    """Envia mensagem como user. Serializa por canal e respeita 429.

    Retorna message_id em sucesso, None em falha.
    """
    # ─── KILL SWITCH ────────────────────────────────────────────────
    try:
        import config as _config
        if not getattr(_config, "TOKEN_MODE_ENABLED", False):
            _log.info("[enviar_como_usuario] TOKEN_MODE_ENABLED=False — bloqueado")
            return None
    except Exception:
        pass

    lock = _get_channel_lock(channel_id)
    async with lock:
        s = _get_session(token)
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": token, "Content-Type": "application/json"}
        payload = {"content": content}

        # Tenta até 3x com retry em 429. Outros erros não vale a pena retentar.
        for tentativa in range(3):
            try:
                async with s.post(url, headers=headers, json=payload) as r:
                    if r.status in (200, 201):
                        try:
                            data = await r.json()
                            return str(data.get("id", ""))
                        except Exception:
                            return ""
                    if r.status == 429:
                        # Rate limit — Discord diz exatamente quanto esperar
                        try:
                            data = await r.json()
                            retry_after = float(data.get("retry_after", 1.0))
                        except Exception:
                            retry_after = 1.0
                        # Cap defensivo: não esperar mais que 10s
                        retry_after = min(retry_after, 10.0)
                        _log.info(f"[enviar_como_usuario] ch={channel_id} 429 retry_after={retry_after:.2f}s")
                        await asyncio.sleep(retry_after)
                        continue
                    # Outros erros: log e desiste
                    body = (await r.text())[:200]
                    _log.warning(f"[enviar_como_usuario] ch={channel_id} status={r.status} body={body}")
                    return None
            except Exception as e:
                _log.warning(f"[enviar_como_usuario] ch={channel_id} exc: {e}")
                return None
        _log.warning(f"[enviar_como_usuario] ch={channel_id} desistiu após 3 tentativas (429)")
        return None


async def fechar_pool_sessions():
    """Fecha todas as sessions do pool. Chamado no shutdown."""
    for s in list(_session_pool.values()):
        try:
            await s.close()
        except Exception:
            pass
    _session_pool.clear()


async def listar_guilds_user(token: str) -> list[dict]:
    """Lista as guilds que o user (token) está. Retorna [{id, name}, ...]"""
    # ─── KILL SWITCH ────────────────────────────────────────────────
    try:
        import config as _config
        if not getattr(_config, "TOKEN_MODE_ENABLED", False):
            return []
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "https://discord.com/api/v10/users/@me/guilds",
                headers={"Authorization": token},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return [{"id": g["id"], "name": g.get("name", "?")} for g in data]
    except Exception as e:
        _log.warning(f"[listar_guilds_user] {e}")
    return []


async def listar_canais_guild(token: str, guild_id: str | int) -> list[dict]:
    """Lista canais (text + threads ativas) de uma guild via token de user.
    Retorna [{id, name, type}, ...]"""
    # ─── KILL SWITCH ────────────────────────────────────────────────
    try:
        import config as _config
        if not getattr(_config, "TOKEN_MODE_ENABLED", False):
            return []
    except Exception:
        pass

    canais = []
    text_channel_ids = []
    try:
        async with aiohttp.ClientSession() as s:
            # Canais de texto
            async with s.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                headers={"Authorization": token},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    for ch in await r.json():
                        # type 0 = text, 5 = announcement, 15 = forum
                        if ch.get("type") in (0, 5, 15):
                            canais.append({"id": ch["id"], "name": ch.get("name", "?"), "type": ch["type"]})
                            text_channel_ids.append(ch["id"])

            # Threads ativas da guild (todas as públicas que o user já entrou)
            async with s.get(
                f"https://discord.com/api/v10/guilds/{guild_id}/threads/active",
                headers={"Authorization": token},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    seen = {c["id"] for c in canais}
                    threads = data.get("threads", [])
                    fila_threads = [t for t in threads if (t.get("name") or "").lower().startswith("fila")]
                    if fila_threads:
                        _log.info(f"[listar_canais] guild={guild_id} threads ativas fila*: {[t['name'] for t in fila_threads]}")
                    for t in threads:
                        if t["id"] not in seen:
                            canais.append({"id": t["id"], "name": t.get("name", "?"), "type": t.get("type", 11)})

            # Threads ativas POR CANAL DE TEXTO (pega fila-306 dentro de 4x4-mobile etc.)
            all_text_ids = [
                c["id"] for c in canais
                if c.get("type") in (0, 5, 15)
            ]
            seen = {c["id"] for c in canais}
            for ch_id in all_text_ids[:40]:
                try:
                    async with s.get(
                        f"https://discord.com/api/v10/channels/{ch_id}/threads/active",
                        headers={"Authorization": token},
                        timeout=aiohttp.ClientTimeout(total=8),
                    ) as r:
                        if r.status == 200:
                            data = await r.json()
                            for t in data.get("threads", []):
                                if t["id"] not in seen:
                                    canais.append({"id": t["id"], "name": t.get("name", "?"), "type": t.get("type", 11)})
                                    seen.add(t["id"])
                except Exception:
                    continue
    except Exception as e:
        _log.warning(f"[listar_canais_guild] guild={guild_id}: {e}")
    return canais


async def listar_mensagens_canal(token: str, channel_id: str | int, limit: int = 20) -> list[dict]:
    """Lê últimas mensagens de um canal via token de user.
    Retorna [{id, content, author_id, author_name}, ...]"""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://discord.com/api/v10/channels/{channel_id}/messages?limit={limit}",
                headers={"Authorization": token},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    return [
                        {
                            "id": m["id"],
                            "content": m.get("content", ""),
                            "author_id": m.get("author", {}).get("id", ""),
                            "author_name": m.get("author", {}).get("username", "?"),
                        }
                        for m in data
                    ]
    except Exception as e:
        _log.warning(f"[listar_mensagens_canal] ch={channel_id}: {e}")
    return []


async def editar_como_usuario(token: str, channel_id: int, message_id: str, content: str):
    # ─── KILL SWITCH ────────────────────────────────────────────────
    try:
        import config as _config
        if not getattr(_config, "TOKEN_MODE_ENABLED", False):
            return
    except Exception:
        pass

    try:
        async with aiohttp.ClientSession() as s:
            await s.patch(
                f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
                headers={"Authorization": token, "Content-Type": "application/json"},
                json={"content": content},
                timeout=aiohttp.ClientTimeout(total=10),
            )
    except Exception as e:
        _log.warning(f"[editar_como_usuario] {e}")


# ─── PATCH/POST Components V2 ───────────────────────────────
async def _patch_v2_ephemeral(app_id: int, itoken: str, payload: dict) -> bool:
    """PATCH @original via webhook da interação."""
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{itoken}/messages/@original"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.patch(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[v2 patch tokenmode] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 patch tokenmode] {ex}")
        return False


async def _respond_v2_initial(inter_id: int, inter_token: str, payload: dict) -> bool:
    """POST resposta inicial V2 (CHANNEL_MESSAGE_WITH_SOURCE = type 4).
    Cria a mensagem @original já como V2, evitando o erro
    MESSAGE_CANNOT_USE_LEGACY_FIELDS_WITH_COMPONENTS_V2 que ocorre quando
    se faz defer() antes (defer cria mensagem legacy)."""
    url = f"https://discord.com/api/v10/interactions/{inter_id}/{inter_token}/callback"
    body = {"type": 4, "data": payload}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[v2 respond tokenmode] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 respond tokenmode] {ex}")
        return False


async def _update_v2_message(inter_id: int, inter_token: str, payload: dict) -> bool:
    """POST callback type 7 (UPDATE_MESSAGE) — atualiza a mensagem do botão V2."""
    url = f"https://discord.com/api/v10/interactions/{inter_id}/{inter_token}/callback"
    body = {"type": 7, "data": payload}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[v2 update tokenmode] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 update tokenmode] {ex}")
        return False


# ─── Build payload V2 ────────────────────────────────────────
def _build_v2_payload(user_id: int, ativo: bool, tem_tok: bool, token_invalido: bool = False) -> dict:
    # ── Cores e linhas baseadas no estado ──
    if token_invalido:
        cor          = 0xFF4757
        status_emj   = _em_str("off")
        status_txt   = "Token Inválido"
        token_emj    = _em_str("off")
        token_txt    = "Configure um novo token"
    elif ativo and tem_tok:
        cor          = 0x57F287
        status_emj   = _em_str("on")
        status_txt   = "Modo Ativado"
        token_emj    = _em_str("verified")
        token_txt    = "Token configurado e válido"
    elif tem_tok:
        cor          = 0xFEE75C
        status_emj   = _em_str("awaiting")
        status_txt   = "Pronto para Ativar"
        token_emj    = _em_str("verified")
        token_txt    = "Token configurado"
    else:
        cor          = 0x5865F2
        status_emj   = _em_str("off")
        status_txt   = "Modo Desativado"
        token_emj    = _em_str("off")
        token_txt    = "Nenhum token configurado"

    info_emj     = _em_str("info") or _em_str("settings")
    megafone_emj = _em_str("megafone")

    # Botões — só passa emoji se existir
    btn_ativar = {"id": 10, "type": 2, "style": 3, "label": "Ativar", "custom_id": "tk:ativar",
                  "disabled": ativo or not tem_tok or token_invalido}
    if _em("on"): btn_ativar["emoji"] = _em("on")

    btn_desativar = {"id": 11, "type": 2, "style": 4, "label": "Desativar", "custom_id": "tk:desativar",
                     "disabled": not ativo}
    if _em("off"): btn_desativar["emoji"] = _em("off")

    btn_config = {"id": 12, "type": 2, "style": 1, "label": "Configurar Token", "custom_id": "tk:config"}
    if _em("settings"): btn_config["emoji"] = _em("settings")

    components = [{
        "id": 1, "type": 17, "accent_color": cor,
        "components": [
            {
                "id": 2, "type": 10,
                "content": (
                    f"## {megafone_emj}  Token Mode\n"
                    f"-# Envie suas mensagens de sala **com sua própria conta** no chat."
                ),
            },
            {"id": 3, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 4, "type": 10,
                "content": (
                    f"### {status_emj}  {status_txt}\n"
                    f"-# {token_emj}  {token_txt}"
                ),
            },
            {"id": 5, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 6, "type": 10,
                "content": (
                    f"{info_emj}  **Como funciona?**\n"
                    f"-# Quando ativado, ao usar `/c1`, `/c2` ou `/c3`, uma mensagem aparece "
                    f"no chat **enviada por você** com o ID e senha da sala."
                ),
            },
            {"id": 8, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 9, "type": 1,
                "components": [btn_ativar, btn_desativar, btn_config],
            },
        ],
    }]

    return {"flags": 64 | 32768, "components": components}


# ─── Build payload V2 PÚBLICO (genérico, sem estado por usuário) ────
def _build_v2_payload_publico() -> dict:
    cor          = 0x5865F2
    megafone_emj = _em_str("megafone")
    info_emj     = _em_str("info") or _em_str("settings")

    btn_ativar = {"id": 10, "type": 2, "style": 3, "label": "Ativar", "custom_id": "tk:ativar"}
    if _em("on"): btn_ativar["emoji"] = _em("on")

    btn_desativar = {"id": 11, "type": 2, "style": 4, "label": "Desativar", "custom_id": "tk:desativar"}
    if _em("off"): btn_desativar["emoji"] = _em("off")

    btn_config = {"id": 12, "type": 2, "style": 1, "label": "Configurar Token", "custom_id": "tk:config"}
    if _em("settings"): btn_config["emoji"] = _em("settings")

    components = [{
        "id": 1, "type": 17, "accent_color": cor,
        "components": [
            {
                "id": 2, "type": 10,
                "content": (
                    f"## {megafone_emj}  Token Mode\n"
                    f"-# Envie suas mensagens de sala **com sua própria conta** no chat."
                ),
            },
            {"id": 3, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 6, "type": 10,
                "content": (
                    f"{info_emj}  **Como funciona?**\n"
                    f"-# Clique em **Configurar Token** pra cadastrar seu token, depois "
                    f"em **Ativar**. Ao usar `/c1`, `/c2` ou `/c3`, uma mensagem aparece "
                    f"no chat **enviada por você** com o ID e senha da sala."
                ),
            },
            {"id": 8, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 9, "type": 1,
                "components": [btn_ativar, btn_desativar, btn_config],
            },
        ],
    }]

    # Flag 32768 = IS_COMPONENTS_V2 (sem flag 64 = público)
    return {"flags": 32768, "components": components}


# ─── Cog ────────────────────────────────────────────────────
class TokenModeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, inter: discord.Interaction):
        # Só processa botões com prefix tk:
        if inter.type != discord.InteractionType.component:
            return
        cid = (inter.data or {}).get("custom_id", "")
        if not cid.startswith("tk:"):
            return

        acao = cid.split(":", 1)[1]
        uid  = inter.user.id

        # ── Configurar Token (modal) ──
        if acao == "config":
            await inter.response.send_modal(TokenModal(uid))
            return

        # ── Ativar ──
        # Como o painel é PÚBLICO, não podemos editá-lo para o estado de um user.
        # Em vez disso, abrimos um painel ephemeral pessoal com o estado real.
        if acao == "ativar":
            cfg   = await asyncio.to_thread(token_mode_get, str(uid))
            token = cfg.get("token") or None
            if not token:
                payload = _build_v2_payload(uid, ativo=False, tem_tok=False, token_invalido=True)
                await _respond_v2_initial(inter.id, inter.token, payload)
                try:
                    await inter.followup.send(
                        "❌ Você ainda não configurou um token. Clique em **Configurar Token** primeiro.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return

            # Resposta inicial ephemeral (loading)
            loading = _build_v2_payload(uid, ativo=False, tem_tok=True)
            await _respond_v2_initial(inter.id, inter.token, loading)

            me = await _validar_token(token)
            if not me or str(me.get("id")) != str(uid):
                payload = _build_v2_payload(uid, ativo=False, tem_tok=True, token_invalido=True)
                await _patch_v2_ephemeral(inter.application_id, inter.token, payload)
                # Log: token inválido na ativação
                asyncio.create_task(_enviar_log(
                    inter.client, inter.user, "inválido",
                    f"Tentou ativar com token inválido ou de outra conta."
                ))
                try:
                    await inter.followup.send("❌ **Token inválido!** Configure um novo token.", ephemeral=True)
                except Exception:
                    pass
                return

            await asyncio.to_thread(token_mode_set_ativo, str(uid), True)
            payload = _build_v2_payload(uid, ativo=True, tem_tok=True)
            await _patch_v2_ephemeral(inter.application_id, inter.token, payload)
            # Log: ativou
            asyncio.create_task(_enviar_log(
                inter.client, inter.user, "ativar",
                f"Conta vinculada: `{me.get('username')}` (`{me.get('id')}`)"
            ))
            return

        # ── Desativar ──
        if acao == "desativar":
            cfg = await asyncio.to_thread(token_mode_get, str(uid))
            if not cfg.get("ativo"):
                # Já está desativado — só mostra estado atual
                payload = _build_v2_payload(uid, ativo=False, tem_tok=bool(cfg.get("token")))
                await _respond_v2_initial(inter.id, inter.token, payload)
                return

            await asyncio.to_thread(token_mode_set_ativo, str(uid), False)
            cfg = await asyncio.to_thread(token_mode_get, str(uid))
            payload = _build_v2_payload(uid, ativo=False, tem_tok=bool(cfg.get("token")))
            await _respond_v2_initial(inter.id, inter.token, payload)
            # Log: desativou
            asyncio.create_task(_enviar_log(
                inter.client, inter.user, "desativar",
                "Usuário desativou o Token Mode."
            ))
            return


class TokenModal(discord.ui.Modal, title="🔑 Configurar Token"):
    token_input = discord.ui.TextInput(
        label="Seu token do Discord",
        placeholder="Cole aqui seu token (MTQ... ou NTQ...)",
        min_length=50, max_length=110, required=True,
        style=discord.TextStyle.short,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        token = self.token_input.value.strip()

        me = await _validar_token(token)
        if not me:
            # Log: token inválido (não passou na validação)
            asyncio.create_task(_enviar_log(
                inter.client, inter.user, "inválido",
                "Tentou configurar um token que falhou na validação `GET /users/@me`."
            ))
            return await inter.followup.send("❌ **Token inválido!** Verifique e tente novamente.", ephemeral=True)
        if str(me.get("id")) != str(self.user_id):
            # Log: token de OUTRA conta
            asyncio.create_task(_enviar_log(
                inter.client, inter.user, "inválido",
                f"Tentou configurar token de **outra conta**: `{me.get('username')}` (`{me.get('id')}`)"
            ))
            return await inter.followup.send(
                f"❌ Token pertence à conta **{me.get('username')}**, não à sua.", ephemeral=True
            )

        await asyncio.to_thread(token_mode_set_token, str(self.user_id), token)
        cfg = await asyncio.to_thread(token_mode_get, str(self.user_id))
        payload = _build_v2_payload(self.user_id, ativo=bool(cfg.get("ativo")), tem_tok=True)
        await _patch_v2_ephemeral(inter.application_id, inter.token, payload)
        await inter.followup.send(f"✅ Token de **{me.get('username')}** configurado!", ephemeral=True)
        # Log: configurou token (sucesso)
        asyncio.create_task(_enviar_log(
            inter.client, inter.user, "config",
            f"Conta vinculada: `{me.get('username')}` (`{me.get('id')}`)"
        ))


# ─── Helper exportado para abrir o painel de outros locais ──
async def abrir_painel_token(inter: discord.Interaction):
    """Chamado pelo botão Token Mode do /painelglobal.
    Posta o painel PÚBLICO no canal (todo mundo vê).
    Os botões do painel público abrem ephemeral pessoal pra cada usuário."""
    payload = _build_v2_payload_publico()

    # Responde DIRETO com type 4 (cria mensagem pública V2)
    if inter.response.is_done():
        # fallback — tenta enviar via channel
        try:
            ch = inter.channel
            if ch:
                # Posta via webhook do canal usando bot
                from cogs.token_mode import _post_v2_channel
                await _post_v2_channel(inter.client, ch.id, payload)
        except Exception as ex:
            _log.warning(f"[abrir_painel_token fallback] {ex}")
        return

    ok = await _respond_v2_initial(inter.id, inter.token, payload)
    if not ok:
        try:
            if not inter.response.is_done():
                await inter.response.defer(ephemeral=True)
            await inter.followup.send("❌ Erro ao abrir painel. Tente novamente.", ephemeral=True)
        except Exception:
            pass


async def _post_v2_channel(bot, channel_id: int, payload: dict) -> bool:
    """Posta mensagem V2 num canal via API do bot."""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {bot.http.token}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 201)
                if not ok:
                    _log.warning(f"[v2 post channel] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 post channel] {ex}")
        return False


async def setup(bot: commands.Bot):
    await bot.add_cog(TokenModeCog(bot))
