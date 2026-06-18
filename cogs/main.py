# cogs/main.py  –  SalasFF Bot v3 — embeds 100% redesenhadas

import asyncio, io, os, json, logging
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import config
_ADMIN_GUILDS = [discord.Object(id=gid) for gid in config.OWNER_GUILD_IDS]
from utils import logs as _logs
from utils.api import api
from utils.imagem import gerar_imagem_sala
from utils.pix import get_preco_por_sala
from utils.emojis import PE, e, BOT, STATS, SETTINGS, INFO, DOT, ON, OFF, GIFT, CART, TOP, MOBILE, PLAY, REFRESH, MONEY, COPIAR, ROLES, CLICK, AWAITING, CLOCK, VISION, RAGE, LOADING, PRESENTE, DOLLAR, CHANNEL, TRASH, RELOADING, URL, SALA_ID, SALA_SENHA
from utils.database import (
    pedidos_por_id, criar_keys, buscar_key, validar_key, resgatar_key,
    consumir_sala_key, reservar_sala_key, reverter_sala_key,
    keys_do_usuario, todas_keys_com_saldo,
    remover_salas_cliente, saldo_total_usuario,
    registrar_sala, atualizar_sala, perfil_usuario,
    adicionar_saldo_usuario, lucro_periodo,
    go_config_get, go_config_set,
    guild_config_get, guild_consumir_sala, guild_reverter_sala,
)

_BR = ZoneInfo("America/Sao_Paulo")
_log = logging.getLogger("salasff")

# ═══════════════════════════════════════════
#  Defer-first helpers — nunca crasha por 404 "Unknown interaction".
#  Se a interação expirou antes do bot responder (event loop travado,
#  API externa lenta, etc), apenas loga warn e segue em frente.
# ═══════════════════════════════════════════
async def _safe_defer(inter, ephemeral=True, thinking=False):
    """Defer com proteção contra 404 (interação expirada) e double-ack."""
    try:
        if inter.response.is_done():
            return True
        await inter.response.defer(ephemeral=ephemeral, thinking=thinking)
        return True
    except discord.NotFound:
        _log.warning(f"[defer] interação {inter.id} expirou antes do defer — event loop lento?")
        return False
    except discord.HTTPException as ex:
        _log.warning(f"[defer] http erro: {ex}")
        return False
    except Exception as ex:
        _log.warning(f"[defer] erro inesperado: {ex}")
        return False

async def _safe_send_modal(inter, modal):
    """Enviar modal com proteção contra 404."""
    try:
        if inter.response.is_done():
            _log.warning(f"[modal] interação {inter.id} já respondida — não dá pra enviar modal")
            return False
        await inter.response.send_modal(modal)
        return True
    except discord.NotFound:
        _log.warning(f"[modal] interação {inter.id} expirou")
        return False
    except Exception as ex:
        _log.warning(f"[modal] erro: {ex}")
        return False

async def _safe_send_msg(inter, **kwargs):
    """send_message com proteção — usa followup se já respondida."""
    try:
        if inter.response.is_done():
            return await inter.followup.send(**kwargs)
        return await inter.response.send_message(**kwargs)
    except discord.NotFound:
        _log.warning(f"[send] interação {inter.id} expirou")
        return None
    except Exception as ex:
        _log.warning(f"[send] erro: {ex}")
        return None

# ── Components V2 helpers ────────────────────────────────
import aiohttp as _aiohttp_v2

# Cache de sessões de sala criada {pid -> {modo, sala, uid}}
_sala_v2_cache: dict = {}

async def _post_v2_channel(channel_id: int, payload: dict) -> bool:
    """POST Components V2 num canal via HTTP."""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
    try:
        async with _aiohttp_v2.ClientSession() as _s:
            async with _s.post(url, headers=headers, json=payload) as r:
                ok = r.status in (200, 201)
                if not ok:
                    _log.warning(f"[v2 post] {r.status} {await r.text()[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 post] {ex}")
        return False

async def _patch_v2_ephemeral(app_id: int, token: str, msg_id: int, payload: dict) -> bool:
    """PATCH Components V2 numa mensagem efêmera via webhook."""
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/{msg_id}"
    try:
        async with _aiohttp_v2.ClientSession() as _s:
            async with _s.patch(url, json=payload) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[v2 patch] {r.status} {await r.text()[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 patch] {ex}")
        return False

def _em(pe_key):
    """Retorna dict de emoji para payload Components V2."""
    em = PE[pe_key]
    return {"id": str(em.id), "name": em.name, "animated": em.animated}

def _load_user_historico(user_id: str, limit: int = 10) -> list:
    """Retorna as últimas `limit` salas criadas pelo usuário."""
    from utils.database import _load
    salas = _load("salas")
    user_salas = [s for s in salas.values() if s.get("user_id") == user_id and s.get("sala_id")]
    user_salas.sort(key=lambda s: s.get("criado_em", ""), reverse=True)
    return user_salas[:limit]

# ═══════════════════════════════════════════
#  Config painel persistido
# ═══════════════════════════════════════════
_CFG_BASE = os.path.join(os.path.dirname(__file__), "..")
def _cfg_dir():
    return _CFG_BASE

_PAINEL_DEF = {
    "titulo": f"{BOT}  SalasFF — Free Fire Custom",
    "linha1": "Crie salas personalizadas no Free Fire em segundos",
    "desc":   f"{DOT} Use seus créditos para criar salas privadas\n{DOT} Resgate keys e comece a jogar",
    "rodape": "SalasFF Bot • salasff.com",
}

def _load_painel():
    path = os.path.join(_cfg_dir(), "painel_config.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return {**_PAINEL_DEF, **json.load(f)}
    except Exception:
        return dict(_PAINEL_DEF)

def _save_painel(data):
    path = os.path.join(_cfg_dir(), "painel_config.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ═══════════════════════════════════════════
#  Helpers globais
# ═══════════════════════════════════════════
def is_admin(uid): return not config.ADMIN_IDS or uid in config.ADMIN_IDS
def modo_info(m): return config.MODOS.get(m, {"nome":"?","emoji":"🎮","canal_id":None,"salaid":""})

def barra(u, t):
    if t == 0: return "`░░░░░░░░░░` 0/0"
    f = int((u / t) * 10)
    return f"`{'█'*f}{'░'*(10-f)}` {u}/{t}"

def modo_e(m):
    return {1: e("jogadores"), 2: PLAY, 3: TOP}.get(m, "🎮")

def _ts():
    return datetime.now(_BR)

# ═══════════════════════════════════════════
#  Embed factory — bonito e consistente
# ═══════════════════════════════════════════
def _emb(titulo="", cor=0x5865F2, desc=""):
    em = discord.Embed(title=titulo, color=cor)
    if desc: em.description = desc
    return em

def _ok(t, d=""): return _emb(f"{ON}  {t}", config.COR_SUCESSO, d)
def _err(t, d=""): return _emb(f"❌  {t}", config.COR_ERRO, d)
def _warn(t, d=""): return _emb(f"{OFF}  {t}", config.COR_AVISO, d)
def _wait(t, d=""): return _emb(f"{REFRESH}  {t}", config.COR_AGUARDO, d)
def _info(t, d=""): return _emb(f"{INFO}  {t}", config.COR_INFO, d)

# ═══════════════════════════════════════════
#  Embed painel principal
# ═══════════════════════════════════════════
def _embed_painel(cfg):
    em = discord.Embed(
        title=cfg["titulo"],
        color=0x2B2D31,
    )
    parts = []
    if cfg.get("linha1"):
        parts.append(cfg["linha1"])
    if cfg.get("desc"):
        parts.append(cfg["desc"])
    if parts:
        em.description = "\n".join(parts)
    if cfg.get("rodape"):
        em.set_footer(text=cfg["rodape"])
    return em

# ═══════════════════════════════════════════
#  Embed sala criada
# ═══════════════════════════════════════════
# ── EMBED ANTIGA (backup) ──
# def _embed_sala(data, sala, modo=0):
#     ia = data.get("inicio_automatico", "")
#     m  = modo_info(modo) if modo else {"nome":"Custom"}
#     em = discord.Embed(color=config.COR_SUCESSO)
#     em.title = f"{BOT}  A Sala Foi Criada!"
#     lines = []
#     if ia: lines.append(f"{ON}  Em ***{ia}*** *a sala será iniciada!*")
#     lines.append("")
#     lines.append(f"{modo_e(modo)}  **Modo:** ***{m['nome']}***")
#     lines.append(f"{STATS}  **ID:**\n> `{sala.get('id','—')}`")
#     lines.append(f"{SETTINGS}  **Senha:**\n> `{sala.get('senha','—')}`")
#     em.description = "\n".join(lines)
#     return em

def _embed_sala(data, sala, modo=0):
    ia = data.get("inicio_automatico", "")
    m  = modo_info(modo) if modo else {"nome":"Custom","emoji":"🎮"}
    em = discord.Embed(color=0x2B2D31)
    em.title = f"**A sala foi criada!**"
    lines = []
    if ia:
        lines.append(f"Em ***{ia}*** a partida será iniciada!")
    lines.append("")
    lines.append(f"{modo_e(modo)} **Modo:** **{m['nome']}**")
    lines.append(f"{SALA_ID} **ID:** `{sala.get('id','—')}`")
    lines.append(f"{SALA_SENHA} **Senha:** `{sala.get('senha','—')}`")
    em.description = "\n".join(lines)
    return em

# ═══════════════════════════════════════════
#  Views
# ═══════════════════════════════════════════

class SalaPrivadaView(discord.ui.View):
    def __init__(self, pid, modo, sala=None, uid=None):
        super().__init__(timeout=600)
        self.pid=pid; self.modo=modo; self.sala=sala or {}; self.uid=uid

        # Se a API retornou sala.link, ativa o botão "Entrar na Sala" abaixo
        # (visível só se houver link). Não removemos o botão fixo, apenas
        # desabilitamos quando não há link.
        if not (sala or {}).get("link"):
            try:
                self.b_link.disabled = True
            except Exception:
                pass
    def _ok(self, i): return not self.uid or i.user.id == self.uid

    @discord.ui.button(label="Copiar ID", style=discord.ButtonStyle.secondary, row=0)
    async def b_copy_id(self, i, b):
        sid = str(self.sala.get("id","—"))
        await i.response.send_message(f"{sid}", ephemeral=True)

    @discord.ui.button(label="Iniciar", emoji=PE["on"], style=discord.ButtonStyle.secondary, row=0)
    async def b_start(self, i, b):
        if not self._ok(i): return await i.response.send_message("❌ Só o criador pode usar.", ephemeral=True)
        await i.response.defer()
        d = await api.iniciar_partida(self.pid)
        if d.get("success"):
            for c in self.children: c.disabled = True
            s = d.get("sala") or {}
            em = _ok("Partida Iniciada!")
            em.add_field(name=f"{INFO}  **Nome**", value=f"> ***{s.get('nome','—')}***", inline=True)
            em.add_field(name=f"{SETTINGS}  **Senha**", value=f"> `{s.get('senha','—')}`", inline=True)
            em.add_field(name=f"{STATS}  **ID**", value=f"> `{s.get('id','—')}`", inline=True)
            await i.edit_original_response(embed=em, view=self, attachments=[])
        else:
            await i.followup.send(embed=_err(d.get("msg","Erro")), ephemeral=True)

    @discord.ui.button(label="Expulsar", emoji=PE["jogadores"], style=discord.ButtonStyle.secondary, row=0)
    async def b_kick(self, i, b):
        if not self._ok(i): return await i.response.send_message("❌ Só o criador pode usar.", ephemeral=True)
        await i.response.send_modal(ExpulsarModal(self.pid))

    @discord.ui.button(label="Status", emoji=PE["vision"], style=discord.ButtonStyle.secondary, row=0)
    async def b_status(self, i, b):
        await i.response.defer(ephemeral=True)
        d = await api.info_sala(self.pid)
        s = d.get("sala") or self.sala
        m = modo_info(self.modo)
        status_map = {
            2: f"{AWAITING} Criando…",
            3: f"{ON} Aguardando",
            4: f"{PLAY} Iniciada",
        }
        status_txt = status_map.get(d.get("status", 0), "—")
        ia = d.get("inicio_automatico", "") or "—"

        sala_id    = s.get("id", "—")
        sala_senha = s.get("senha", "—")
        sala_nome  = s.get("nome", "—")

        em = discord.Embed(
            title=f"{BOT}  Sala — {sala_nome}",
            color=0x2B2D31,
        )

        em.add_field(name=f"{SALA_ID}  ID",      value=f"`{sala_id}`",    inline=True)
        em.add_field(name=f"{SALA_SENHA}  Senha", value=f"`{sala_senha}`", inline=True)
        em.add_field(name=f"{MOBILE}  Modo",      value=m["nome"],         inline=True)
        em.add_field(name=f"{VISION}  Status",    value=status_txt,        inline=True)
        em.add_field(name=f"{CLOCK}  GO",         value=f"`{ia}`",         inline=True)
        em.add_field(name="\u200b", value="\u200b", inline=True)

        # ── Jogadores ──
        equipes = s.get("equipes", [])
        total_jogadores = sum(len(eq.get("jogadores", [])) for eq in equipes)

        if total_jogadores == 0:
            jogadores_txt = "> *Sala vazia — nenhum jogador ainda*"
        else:
            jogadores_lines = []
            for eq in equipes:
                for jg in eq.get("jogadores", []):
                    nick = jg.get("nickname") or jg.get("name") or "?"
                    jid  = jg.get("id") or jg.get("accountId") or "?"
                    jogadores_lines.append(f"> {DOT} **{nick}** — `{jid}`")
            jogadores_txt = "\n".join(jogadores_lines[:20])
            if total_jogadores > 20:
                jogadores_txt += f"\n> *… e mais {total_jogadores - 20} jogadores*"

        em.add_field(
            name=f"{ROLES}  Jogadores na Sala  ({total_jogadores})",
            value=jogadores_txt,
            inline=False,
        )

        await i.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="Copiar ID e Senha", emoji=PE["copiar"], style=discord.ButtonStyle.secondary, row=1)
    async def b_copy(self, i, b):
        sid = str(self.sala.get("id","—"))
        senha = str(self.sala.get("senha","—"))
        await i.response.send_message(f"{sid}\n{senha}", ephemeral=True)

    @discord.ui.button(label="Entrar na Sala", style=discord.ButtonStyle.primary, row=1)
    async def b_link(self, i, b):
        link = str(self.sala.get("link") or "")
        if not link:
            return await i.response.send_message("❌ Link da sala não disponível.", ephemeral=True)
        await i.response.send_message(link, ephemeral=True)


class ExpulsarModal(discord.ui.Modal, title="🚫 Expulsar Jogador"):
    jogador_id = discord.ui.TextInput(label="ID do Jogador (Free Fire)", placeholder="Ex: 1148484871", min_length=5, max_length=20)
    def __init__(self, pid): super().__init__(); self.pid = pid
    async def on_submit(self, i):
        await i.response.defer(thinking=True, ephemeral=True)
        d = await api.expulsar_jogador(self.pid, self.jogador_id.value.strip())
        if d.get("success"):
            await i.followup.send(embed=_ok("Jogador Expulso", f"{STATS} ID: `{self.jogador_id.value.strip()}`"), ephemeral=True)
        else:
            await i.followup.send(embed=_err(d.get("msg","Erro")), ephemeral=True)


class GoEscolhaView(discord.ui.View):
    """View ephemeral com escolha entre GO Adm e GO Player (só prefix)."""
    def __init__(self, pid: str):
        super().__init__(timeout=120)
        self.pid = pid

        self.add_item(discord.ui.Button(
            label="GO Adm",
            emoji=PE["on"],
            style=discord.ButtonStyle.success,
            custom_id=f"sv2:goadm:{pid}",
            row=0,
        ))
        self.add_item(discord.ui.Button(
            label="GO Player",
            emoji=PE["jogadores"],
            style=discord.ButtonStyle.primary,
            custom_id=f"sv2:goplayer:{pid}",
            row=0,
        ))


class CopiarModal(discord.ui.Modal):
    """Modal de 'copiar' — abre com o valor pré-preenchido pro usuário
    selecionar e copiar sem postar nada no chat. Ao fechar, não faz nada."""
    def __init__(self, label: str, valor: str):
        super().__init__(title=f"Copiar {label}")
        self.campo = discord.ui.TextInput(
            label=label,
            default=str(valor),
            style=discord.TextStyle.short,
            required=False,
            max_length=4000,
        )
        self.add_item(self.campo)

    async def on_submit(self, inter):
        # Usuário apertou "Enviar" — só fecha silenciosamente
        try:
            await inter.response.defer(ephemeral=True)
        except Exception:
            pass


class SalaPublicaView(discord.ui.View):
    def __init__(self, pid=""): super().__init__(timeout=None); self.pid = pid
    @discord.ui.button(label="Atualizar Info", emoji=PE["refresh"], style=discord.ButtonStyle.primary, custom_id="sala_pub:atualizar")
    async def b(self, i, btn):
        await i.response.defer()
        d = await api.info_sala(self.pid)
        s = d.get("sala") or {}
        st = {2: f"{AWAITING} *Criando*", 3: f"{ON} *Pronta*", 4: f"{PLAY} *Iniciada*"}
        em = _info("Info da Sala")
        em.add_field(name=f"{INFO}  **Nome**", value=f"> ***{s.get('nome','—')}***", inline=True)
        em.add_field(name=f"{SETTINGS}  **Senha**", value=f"> `{s.get('senha','—')}`", inline=True)
        em.add_field(name=f"{STATS}  **ID**", value=f"> `{s.get('id','—')}`", inline=True)
        em.add_field(name=f"{VISION}  **Status**", value=f"> {st.get(d.get('status',0),'—')}", inline=True)
        await i.edit_original_response(embed=em, view=self)


# ═══════════════════════════════════════════
#  Fluxo de criação de sala (instantâneo)
# ═══════════════════════════════════════════

async def _criar_sala_flow(ctx, modo, go, key_row=None, *, is_prefix=False, key_ja_consumida=False, guild_pagou=False, guild_id=None, public_channel_id=None):
    m = modo_info(modo); salaid = m.get("salaid","")
    if not salaid:
        em = _err("Modo inválido.")
        # Rollback se key já foi consumida
        if key_ja_consumida and key_row:
            await asyncio.to_thread(reverter_sala_key, key_row["id"])
        if guild_pagou and guild_id:
            await asyncio.to_thread(guild_reverter_sala, guild_id)
        if is_prefix:
            try: await ctx.author.send(embed=em)
            except: await ctx.send(embed=em)
        elif ctx.response.is_done(): await ctx.followup.send(embed=em, ephemeral=True)
        else: await ctx.response.send_message(embed=em, ephemeral=True)
        return

    em_wait = discord.Embed(title=f"{REFRESH}  Criando — {modo_e(modo)} {m['nome']}", color=0x2B2D31)
    em_wait.description = f"{DOT} Aguarde, criando sua sala…"

    _uso_v2 = False
    _v2_msg_id = None
    _v2_channel_id = None  # para prefix: edita via channel REST

    if is_prefix:
        author = ctx.author; guild = ctx.guild
        # Tenta V2 direto no canal (prefix não tem token de interação)
        # V2 só funciona em canais de servidor, não em DMs
        if ctx.guild:
            try:
                _txt_wait = (
                    f"<:za_houst1:1483205696077037821> **Criando sua sala...**\n"
                    f"-# Modo **{m['nome']}** — Aguarde um instante"
                )
                _ch_url = f"https://discord.com/api/v10/channels/{ctx.channel.id}/messages"
                _headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
                async with _aiohttp_v2.ClientSession() as _s:
                    async with _s.post(_ch_url, headers=_headers, json={
                        "flags": 32768,
                        "components": [{"id": 1, "type": 17, "accent_color": 0xFFD700, "components": [
                            {"id": 2, "type": 10, "content": _txt_wait},
                        ]}],
                    }) as _r:
                        if _r.status in (200, 201):
                            import json as _json
                            _body = await _r.text()
                            _v2_msg_id = _json.loads(_body).get("id")
                            _v2_channel_id = ctx.channel.id
                            _uso_v2 = bool(_v2_msg_id)
            except Exception as _ex:
                _log.warning(f"[prefix v2 init] {_ex}")
        if not _uso_v2:
            msg = await ctx.send(embed=discord.Embed(title=f"{REFRESH}  Criando — {modo_e(modo)} {m['nome']}", color=0x2B2D31, description=f"{DOT} Aguarde, criando sua sala…"))
        else:
            msg = None
    else:
        author = ctx.user; guild = ctx.guild
        # Garante defer
        if not ctx.response.is_done():
            try:
                await ctx.response.defer(ephemeral=True)
            except Exception:
                pass
        # Tenta enviar "Criando..." como followup V2
        try:
            _txt_wait = (
                f"<:za_houst1:1483205696077037821> **Criando sua sala...**\n"
                f"-# Modo **{m['nome']}** — Aguarde um instante"
            )
            _fw_url = f"https://discord.com/api/v10/webhooks/{ctx.application_id}/{ctx.token}?wait=true"
            async with _aiohttp_v2.ClientSession() as _s:
                async with _s.post(_fw_url, json={
                    "flags": 64 | 32768,
                    "components": [{"id": 1, "type": 17, "accent_color": 0xFFD700, "components": [
                        {"id": 2, "type": 10, "content": _txt_wait},
                    ]}],
                }) as _r:
                    _body = await _r.text()
                    if _r.status in (200, 201):
                        import json as _json
                        _v2_msg_id = _json.loads(_body).get("id")
                        _uso_v2 = bool(_v2_msg_id)
                        _log.info(f"[v2 init] OK msg_id={_v2_msg_id}")
                    else:
                        _log.warning(f"[v2 init fw] {_r.status} {_body[:300]}")
        except Exception as _ex:
            _log.warning(f"[sala v2 init] {_ex}")

        if not _uso_v2:
            if ctx.response.is_done():
                msg = await ctx.followup.send(embed=em_wait, ephemeral=True, wait=True)
            else:
                await ctx.response.send_message(embed=em_wait, ephemeral=True)
                msg = await ctx.original_response()
        else:
            msg = None  # não usado quando _uso_v2=True

    # Paralelo: registra + chama API
    gid = guild_id or (str(guild.id) if guild else None)
    origem = "guild" if guild_pagou else "pessoal"
    # Senha personalizada do usuário (vazio = sem senha)
    try:
        from utils.database import senha_config_get
        _senha_usr = await asyncio.to_thread(senha_config_get, str(author.id))
        _senha_usr = _senha_usr or None  # None faz a API ignorar
    except Exception:
        _senha_usr = None
    # Origem do saldo da key consumida (venda / ranking / indicacao) — define API a usar
    _saldo_origem = None
    if key_row:
        _saldo_origem = key_row.get("origem") or "venda"
    sid_f = asyncio.get_event_loop().run_in_executor(None, registrar_sala, str(author.id), author.display_name, modo, gid, origem)
    api_f = api.criar_sala(salaid, iniciar=go, modo=modo, senha=_senha_usr, saldo_origem=_saldo_origem)
    sid, data = await asyncio.gather(sid_f, api_f)

    pid = data.get("pedidoid")
    if not pid:
        mot = data.get("msg") or "API sem resposta"
        # Rollback: devolve a sala se key já foi consumida
        if key_ja_consumida and key_row:
            await asyncio.to_thread(reverter_sala_key, key_row["id"])
        if guild_pagou and guild_id:
            await asyncio.to_thread(guild_reverter_sala, guild_id)
        _err_txt = f"❌ {mot}\n{OFF} Tente novamente em instantes."
        if _uso_v2 and _v2_msg_id:
            _eu = (f"https://discord.com/api/v10/channels/{_v2_channel_id}/messages/{_v2_msg_id}" if is_prefix
                   else f"https://discord.com/api/v10/webhooks/{ctx.application_id}/{ctx.token}/messages/{_v2_msg_id}")
            _ef = 32768 if is_prefix else 64|32768
            _eh = {"Authorization": f"Bot {config.DISCORD_TOKEN}"} if is_prefix else {}
            async with _aiohttp_v2.ClientSession() as _s:
                await _s.patch(_eu, headers=_eh, json={"flags": _ef, "components": [{"type":10,"content":_err_txt}]})
        elif msg:
            await msg.edit(embed=_err(mot, f"{OFF} Tente novamente em instantes."))
        asyncio.create_task(_logs.log_sala_erro(author, m["nome"], mot))
        return

    if data.get("status") != 3:
        data = await api.aguardar_sala_pronta(pid)
        if data.get("status") != 3:
            # Rollback: devolve a sala se key já foi consumida
            if key_ja_consumida and key_row:
                await asyncio.to_thread(reverter_sala_key, key_row["id"])
            if guild_pagou and guild_id:
                await asyncio.to_thread(guild_reverter_sala, guild_id)
            if _uso_v2 and _v2_msg_id:
                _eu2 = (f"https://discord.com/api/v10/channels/{_v2_channel_id}/messages/{_v2_msg_id}" if is_prefix
                        else f"https://discord.com/api/v10/webhooks/{ctx.application_id}/{ctx.token}/messages/{_v2_msg_id}")
                _ef2 = 32768 if is_prefix else 64|32768
                _eh2 = {"Authorization": f"Bot {config.DISCORD_TOKEN}"} if is_prefix else {}
                async with _aiohttp_v2.ClientSession() as _s:
                    await _s.patch(_eu2, headers=_eh2, json={"flags": _ef2, "components": [{"type":10,"content":f"❌ {data.get('msg','Timeout')}"}]})
            elif msg:
                await msg.edit(embed=_err(data.get("msg","Timeout")))
            asyncio.create_task(_logs.log_sala_erro(author, m["nome"], data.get("msg","Timeout")))
            return

    sala = data.get("sala") or {}

    # ═══ MOSTRA INSTANTÂNEO ═══
    ia  = data.get("inicio_automatico", "")
    m_  = modo_info(modo) if modo else {"nome": "Custom", "emoji": "🎮"}
    sid_str = str(sala.get("id", "—"))
    sen_str = str(sala.get("senha", "—"))

    # Tenta Components V2 (só slash, não prefix)
    _v2_ok = False
    if _uso_v2 and _v2_msg_id:
        try:
            _sala_v2_cache[pid] = {"modo": modo, "sala": sala, "uid": author.id}
            # Emojis customizados da sala criada
            _E_ONLINE   = {"id": "1460537720999772264", "name": "online",  "animated": True}
            _E_VISION   = {"id": "1487110627875885196", "name": "vision",  "animated": False}
            _E_CLOUD    = {"id": "1487690058973843496", "name": "cloud",   "animated": False}
            _E_COPIAR   = {"id": "1445807004634452124", "name": "copiar",  "animated": False}

            # Emoji seta (resolvido via Application após sync)
            _E_SETA   = {"id": str(PE["seta"].id), "name": "rr_seta",       "animated": False}
            _E_MODO1  = {"id": "1455278816325931051", "name": "modo_1",      "animated": False}
            _E_SWORD  = {"id": "1489005986541736057", "name": "swordbattle", "animated": False}

            _header = f"## {e('megafone')} {e('sep224')} A SALA FOI CRIADA!"
            if ia: _header += f"\n-# Em **{ia}** a partida será iniciada!"

            # Linha extra: link de acesso direto (se a API retornou)
            sala_link = sala.get("link") or ""
            link_components = []
            if sala_link:
                link_components.append({"id": 17, "type": 10, "content": f"**Link** {e('seta')} [Entrar na sala]({sala_link})"})

            # Botões — inclui "Entrar" se houver link
            _action_buttons = [
                {"id": 13, "type": 2, "style": 3, "label": "Iniciar",  "custom_id": f"sv2:start:{pid}", "emoji": _E_ONLINE},
                {"id": 14, "type": 2, "style": 2, "label": "Expulsar", "custom_id": f"sv2:kick:{pid}",  "emoji": {"id": "1433858819288469547", "name": "red_vermerro", "animated": False}},
                {"id": 15, "type": 2, "style": 2, "label": "Status",   "custom_id": f"sv2:status:{pid}","emoji": _E_CLOUD},
                {"id": 16, "type": 2, "style": 2, "label": "Copiar ID","custom_id": f"sv2:copy:{pid}",  "emoji": _E_COPIAR},
            ]
            if sala_link:
                # Botão custom — ao clicar, envia o link como mensagem efêmera pro user copiar/abrir
                _action_buttons.insert(0, {"id": 18, "type": 2, "style": 1, "label": "Entrar na Sala", "custom_id": f"sv2:link:{pid}"})

            _payload_sala = {
                "flags": 64 | 32768,
                "components": [{"id": 1, "type": 17, "accent_color": 0x00FF7F, "components": [
                    {"id": 2, "type": 10, "content": _header},
                    {"id": 3, "type": 9,
                     "components": [{"id": 4, "type": 10, "content": f"**Modo** {e('seta')}"}],
                     "accessory": {"id": 5, "type": 2, "style": 2, "label": m_["nome"], "custom_id": f"sv2:modo:{pid}", "disabled": True, "emoji": _E_MODO1}},
                    {"id": 6, "type": 10, "content": f"**ID** {e('seta')} `{sid_str}`"},
                    {"id": 9, "type": 10, "content": f"**Senha** {e('seta')} `{sen_str}`"},
                    *link_components,
                    {"id": 12, "type": 1, "components": _action_buttons},
                ]}],
            }
            # Marca sessão como prefix pra saber se o "Iniciar" mostra escolha GO Adm / GO Player
            if is_prefix and pid in _sala_v2_cache:
                _sala_v2_cache[pid]["is_prefix"] = True
            _patch_url = (f"https://discord.com/api/v10/channels/{_v2_channel_id}/messages/{_v2_msg_id}" if is_prefix
                          else f"https://discord.com/api/v10/webhooks/{ctx.application_id}/{ctx.token}/messages/{_v2_msg_id}")
            _p_headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"} if is_prefix else {}
            _p_flags = 32768 if is_prefix else 64|32768
            _payload_sala["flags"] = _p_flags
            async with _aiohttp_v2.ClientSession() as _s:
                async with _s.patch(_patch_url, headers=_p_headers, json=_payload_sala) as _r:
                    _body = await _r.text()
                    _v2_ok = _r.status in (200, 204)
                    if not _v2_ok:
                        _log.warning(f"[sala v2 patch] status={_r.status} body={_body[:400]}")
        except Exception as _ex:
            _log.warning(f"[sala v2] {_ex}")

    if not _v2_ok:
        em_priv = _embed_sala(data, sala, modo)
        view = SalaPrivadaView(pid, modo, sala, uid=author.id)
        await msg.edit(embed=em_priv, view=view)

    # Background: DB + imagem + canal público
    async def _bg():
        sid_s = str(sala.get("id","—")); sen = str(sala.get("senha","—"))
        mapa = sala.get("nome","BERMUDA") or "BERMUDA"
        link = sala.get("link") or ""
        tasks = [asyncio.get_event_loop().run_in_executor(None, atualizar_sala, sid, pid, sid_s, sen, sala.get("nome",""), link)]
        # Se key NÃO foi consumida antecipadamente, consome agora (compatibilidade com .cs1/.cs2/.cs3)
        if key_row and not key_ja_consumida:
            tasks.append(asyncio.get_event_loop().run_in_executor(None, consumir_sala_key, key_row["id"]))
        await asyncio.gather(*tasks)
        if key_row:
            r = key_row["quantia"] - key_row["salas_usadas"] - (0 if key_ja_consumida else 1)
            asyncio.create_task(_logs.log_key_consumida(author, key_row["code"], max(0,r)))
        from utils.api import api as _api
        api_nome = "API 1 — F" if _api._is_api1() else "API 2 — B"
        asyncio.create_task(_logs.log_sala_criada(author, m["nome"], pid, sala, go, guild, api_nome))
        # Mensagem pública no canal (usada pelo .cs)
        if public_channel_id:
            try:
                go_str = f"{go}" if go else "5"
                _txt_pub = (
                    f"## <:za_houst1:1483205696077037821> SALA CRIADA\n"
                    f"**ID** {e('seta')} `{sid_s}`\n"
                    f"**Senha** {e('seta')} `{sen}`\n"
                    f"-# ⏱️ GO em {go_str} min · {author.mention}"
                )
                _ch_url = f"https://discord.com/api/v10/channels/{public_channel_id}/messages"
                _hdrs = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
                async with _aiohttp_v2.ClientSession() as _s:
                    await _s.post(_ch_url, headers=_hdrs, json={
                        "flags": 32768,
                        "components": [{"id": 1, "type": 17, "accent_color": 0x00FF7F, "components": [
                            {"id": 2, "type": 10, "content": _txt_pub},
                        ]}],
                    })
            except Exception as _ex:
                _log.warning(f"[cs public msg] {_ex}")
        # Imagem desativada
        # try:
        #     img_api = await api.imagem_sala(pid)
        #     img = await asyncio.get_event_loop().run_in_executor(None, gerar_imagem_sala, sid_s, sen, mapa, img_api)
        #     if img:
        #         em_priv.set_image(url="attachment://sala.png")
        #         await msg.edit(embed=em_priv, view=view, attachments=[discord.File(io.BytesIO(img), "sala.png")])
        # except: pass
    asyncio.create_task(_bg())

    # ── Token Mode: envia mensagem pública como o usuário ──
    async def _token_mode_enviar():
        try:
            from utils.database import token_mode_get
            from cogs.token_mode import enviar_como_usuario, editar_como_usuario
            cfg_tok = await asyncio.to_thread(token_mode_get, str(author.id))
            if not cfg_tok.get("ativo") or not cfg_tok.get("token"):
                return
            token = cfg_tok["token"]
            sid_s = str(sala.get("id", "—"))
            sen   = str(sala.get("senha", "—"))
            link  = sala.get("link") or ""

            # Nome e emoji do modo
            modo_info = {
                1: ("Normal",    "⚔️"),
                2: ("Infinito",  "♾️"),
                3: ("Full Capa", "👑"),
            }
            modo_nome, modo_emj = modo_info.get(modo, ("Sala", "🎮"))

            # Hora prevista de início
            from datetime import datetime, timedelta
            hora_ini = (datetime.now() + timedelta(minutes=go)).strftime("%H:%M")

            link_line_ini = f"\n> 🔹 ⠀**Link** ⠀⠀⠀⠀[Entrar na sala]({link})" if link else ""
            txt_ini = (
                f"## 🔸 ⠀**SALA {modo_nome.upper()}**\n"
                f"-# ⏳ ⠀A partida iniciará automaticamente\n"
                f"\n"
                f"> 🔹 ⠀**ID** ⠀⠀⠀⠀⠀⠀`{sid_s}`\n"
                f"> 🔹 ⠀**Senha** ⠀⠀`{sen}`"
                f"{link_line_ini}"
            )

            canal = getattr(ctx, "channel", None)
            if canal is None:
                return
            msg_id = await enviar_como_usuario(token, canal.id, txt_ini)
            if msg_id and go > 0:
                await asyncio.sleep(go * 60)
                link_line_fim = f"\n> 🔹 ⠀**Link** ⠀⠀⠀⠀[Entrar na sala]({link})" if link else ""
                txt_fim = (
                    f"## 🔸 ⠀**SALA INICIADA!**\n"
                    f"-# {modo_emj} ⠀Modo **{modo_nome}** — Boa partida! 🎮\n"
                    f"\n"
                    f"> 🔹 ⠀**ID** ⠀⠀⠀⠀⠀⠀`{sid_s}`\n"
                    f"> 🔹 ⠀**Senha** ⠀⠀`{sen}`"
                    f"{link_line_fim}"
                )
                await editar_como_usuario(token, canal.id, msg_id, txt_fim)
        except Exception as _ex:
            _log.warning(f"[token_mode_enviar] {_ex}")
    asyncio.create_task(_token_mode_enviar())


# ═══════════════════════════════════════════
#  Quick create /c1 /c2 /c3
# ═══════════════════════════════════════════


# ═══════════════════════════════════════════
#  Helper — tenta saldo do servidor antes do pessoal
# ═══════════════════════════════════════════
async def _reservar_sala(inter, modo, uid, display_nome):
    """Tenta consumir do saldo do servidor se:
    - O servidor tiver saldo E
    - cargo_off não estiver ativo E
    - Não houver cargo configurado (qualquer um usa) OU o usuário tiver o cargo configurado
    Retorna (key_row_ou_None, guild_consumiu: bool, guild_id_ou_None)."""
    _log = logging.getLogger("salasff.reserva")
    gid = str(inter.guild.id) if inter.guild else None
    _log.info(f"[RESERVA] uid={uid} modo={modo} guild={gid}")
    if gid:
        cfg = await asyncio.to_thread(guild_config_get, gid)
        cargo_id = cfg.get("cargo_sala_id")
        saldo_guild = cfg.get("saldo", 0)
        cargo_off = cfg.get("cargo_off", False)
        _log.info(f"[RESERVA] guild cfg: saldo={saldo_guild} cargo_id={cargo_id} cargo_off={cargo_off}")
        # Só usa saldo do servidor se: tem saldo, cargo não está OFF, e tem cargo configurado
        if saldo_guild > 0 and not cargo_off and cargo_id:
            tem_cargo = any(r.id == int(cargo_id) for r in getattr(inter.user, "roles", []))
            _log.info(f"[RESERVA] tem_cargo={tem_cargo}")
            if tem_cargo:
                consumido = await asyncio.to_thread(guild_consumir_sala, gid)
                _log.info(f"[RESERVA] guild_consumir={consumido} → saldo restante={cfg.get('saldo',0)-1 if consumido else cfg.get('saldo',0)}")
                if consumido:
                    return None, True, gid

    # fallback: saldo pessoal
    k, restante = await asyncio.to_thread(reservar_sala_key, uid, modo, display_nome)
    _log.info(f"[RESERVA] pessoal: key={'SIM id='+k['id'] if k else 'NENHUMA'} restante={restante}")
    # Se saldo zerou (restante==0 nessa key e sem outras), remove cargo saldo em background
    if k and restante == 0:
        try:
            from cogs.botconfig import remover_cargo_saldo_se_zerou
            asyncio.create_task(remover_cargo_saldo_se_zerou(inter.client, int(uid)))
        except Exception:
            pass
    return k, False, gid

async def _quick(inter, modo):
    if not await _safe_defer(inter, ephemeral=True):
        return  # interação expirou, nada a fazer
    uid = str(inter.user.id)
    k, guild_pagou, gid = await _reservar_sala(inter, modo, uid, inter.user.display_name)
    _log2 = logging.getLogger("salasff.reserva")
    _log2.info(f"[QUICK] modo={modo} guild_pagou={guild_pagou} key={bool(k)} gid={gid}")
    if not guild_pagou and not k:
        try:
            return await inter.followup.send(embed=_err("Sem saldo", f"Sem saldo disponível.\nCompre salas usando o botão **Comprar Salas** no `/c`."), ephemeral=True)
        except Exception:
            return
    go = await asyncio.to_thread(go_config_get, uid)
    if go <= 0:
        go = config.DEFAULT_INICIAR_MINUTOS
    await _criar_sala_flow(inter, modo if (k and k["modo"] != 0) else modo, go,
                           key_row=k, key_ja_consumida=True,
                           guild_pagou=guild_pagou, guild_id=gid)


# ═══════════════════════════════════════════
#  Painel view
# ═══════════════════════════════════════════
async def _modo_btn(inter, modo):
    if not await _safe_defer(inter, ephemeral=True):
        return
    uid = str(inter.user.id)
    k, guild_pagou, gid = await _reservar_sala(inter, modo, uid, inter.user.display_name)
    if not guild_pagou and not k:
        try:
            return await inter.followup.send(embed=_err("Sem saldo", f"Sem saldo disponível.\nUse **Comprar Salas** no `/c` para adquirir."), ephemeral=True)
        except Exception:
            return
    go = await asyncio.to_thread(go_config_get, uid)
    if go <= 0:
        go = config.DEFAULT_INICIAR_MINUTOS
    await _criar_sala_flow(inter, modo if (k and k["modo"] != 0) else modo, go,
                           key_row=k, key_ja_consumida=True,
                           guild_pagou=guild_pagou, guild_id=gid)

class PainelView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Normal", emoji=PE["jogadores"], style=discord.ButtonStyle.secondary, custom_id="painel:criar:1", row=0)
    async def b1(self, i, b): await _modo_btn(i, 1)
    @discord.ui.button(label="Infinito", emoji=PE["play"], style=discord.ButtonStyle.secondary, custom_id="painel:criar:2", row=0)
    async def b2(self, i, b): await _modo_btn(i, 2)
    @discord.ui.button(label="Outros Modos", emoji=PE["top"], style=discord.ButtonStyle.secondary, custom_id="painel:criar:3", row=0)
    async def b3(self, i, b):
        em = _emb(f"{TOP}  Selecione o modo")
        em.description = f"{DOT} Escolha o modo da sala:"
        await i.response.send_message(embed=em, view=C3ModoView(), ephemeral=True)

    @discord.ui.button(label="Meu Saldo", emoji=PE["carteira"], style=discord.ButtonStyle.secondary, custom_id="painel:saldo", row=1)
    async def bsal(self, i, b):
        if not await _safe_defer(i, ephemeral=True):
            return
        d = await asyncio.to_thread(perfil_usuario, str(i.user.id))
        em = _emb(f"{CART}  Carteira — {i.user.display_name}")
        em.set_thumbnail(url=i.user.display_avatar.url)
        em.add_field(
            name="**Salas Disponíveis**",
            value=f"> **{d['saldo']} salas**",
            inline=False,
        )
        gastas = (
            f"> Total de **{d['total']}** salas gastas\n\n"
            f"> {CART}  Hoje: **{d['hoje']}** salas\n\n"
            f"> {CART}  Ontem: **{d['ontem']}** salas\n\n"
            f"> {CART}  3 dias: **{d['3dias']}** salas\n\n"
            f"> {CART}  Semana: **{d['semana']}** salas\n\n"
            f"> {CART}  Mês: **{d['mes']}** salas"
        )
        em.add_field(name="**Salas Gastas**", value=gastas, inline=False)
        try:
            await i.followup.send(embed=em, view=SaldoPainelView(), ephemeral=True)
        except Exception as ex:
            _log.warning(f"[bsal] followup erro: {ex}")


class SaldoPainelView(discord.ui.View):
    """Botões do Meu Saldo: Config GO, Ver Lucro, Bônus."""
    def __init__(self): super().__init__(timeout=300)

    @discord.ui.button(label="Config GO", emoji=PE["settings"], style=discord.ButtonStyle.secondary, row=0)
    async def btn_go(self, inter, btn):
        uid = str(inter.user.id)
        atual = await asyncio.to_thread(go_config_get, uid)
        label = f"{atual} min" if atual > 0 else f"{config.DEFAULT_INICIAR_MINUTOS} min (padrão)"
        em = _info("Tempo de GO", f"{SETTINGS} Tempo atual: **{label}**\n{DOT} Selecione abaixo o tempo para início automático da sala:")
        await inter.response.send_message(embed=em, view=ConfigGoView(), ephemeral=True)

    @discord.ui.button(label="Ver Lucro", emoji=PE["stats"], style=discord.ButtonStyle.primary, row=0)
    async def btn_lucro(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import lucro_config_get, vendas_usuario, stats_usuario_por_guilds
        uid = str(inter.user.id)
        cfg = await asyncio.to_thread(lucro_config_get, uid)
        v   = await asyncio.to_thread(vendas_usuario, uid)
        vps = cfg["valor_por_sala"]
        orgs = cfg.get("orgs", [])
        em = discord.Embed(title=f"{STATS}  Lucro — SalasFF", color=0x2ecc71)
        if vps == 0 and not orgs:
            em.description = (
                f"{SETTINGS} Configure seu lucro clicando em **Alterar Preço**!\n\n"
                f"{DOT} Defina quanto você ganha por sala vendida\n"
                f"{DOT} Clique em **+ Org** se trabalha em mais de uma org"
            )
        else:
            if vps > 0:
                em.add_field(name=f"{MONEY}  Hoje", value=f"> {STATS} **{v['hoje']}** vendidas\n> R$ **{v['hoje']*vps:.2f}**", inline=True)
                em.add_field(name=f"{MONEY}  Ontem", value=f"> {STATS} **{v['ontem']}** vendidas\n> R$ **{v['ontem']*vps:.2f}**", inline=True)
                em.add_field(name=f"{MONEY}  Semana", value=f"> {STATS} **{v['semana']}** vendidas\n> R$ **{v['semana']*vps:.2f}**", inline=True)
                em.add_field(name=f"{DOLLAR}  Total", value=f"> {STATS} **{v['total']}** vendidas\n> R$ **{v['total']*vps:.2f}**", inline=False)
            if orgs:
                guild_ids = [org.get("guild_id", "") for org in orgs]
                org_stats = await asyncio.to_thread(stats_usuario_por_guilds, uid, guild_ids)
                for idx, org in enumerate(orgs):
                    gid = org.get("guild_id", ""); nome = org.get("nome", f"Org {idx+1}"); val = org.get("valor", 0)
                    st = org_stats.get(gid, {"hoje": 0, "ontem": 0, "7dias": 0, "total": 0})
                    em.add_field(name=f"{TOP}  {nome}  (R$ {val:.2f}/sala)", value=(
                        f"> Hoje: **{st['hoje']}** → R$ **{st['hoje']*val:.2f}**\n"
                        f"> Ontem: **{st.get('ontem',0)}** → R$ **{st.get('ontem',0)*val:.2f}**\n"
                        f"> Semana: **{st['7dias']}** → R$ **{st['7dias']*val:.2f}**\n"
                        f"> Total: **{st['total']}** → R$ **{st['total']*val:.2f}**"
                    ), inline=False)
        await inter.followup.send(embed=em, view=LucroSubView(), ephemeral=True)

    @discord.ui.button(label="Bônus", emoji=PE["presente"], style=discord.ButtonStyle.primary, row=0)
    async def btn_bonus(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import bonus_info, get_bonus_config, saldo_total_usuario, bonus_ganho_periodo
        uid = str(inter.user.id)
        b = await asyncio.to_thread(bonus_info, uid)
        saldo = await asyncio.to_thread(saldo_total_usuario, uid)
        ganho = await asyncio.to_thread(bonus_ganho_periodo, uid)
        _br, _bpc = get_bonus_config()
        em = discord.Embed(title=f"{PRESENTE}  Painel de Bônus", color=0xFFD700)
        em.set_thumbnail(url=inter.user.display_avatar.url)
        total = b["total_comprado"]; bonus_disp = b["bonus_disponivel"]; bonus_total = b["bonus_total"]; resgatado = b["bonus_resgatado"]
        if total == 0:
            em.description = (
                f"{OFF} Você ainda não comprou salas.\n\n"
                f"{DOT} A cada **{_br} salas** compradas você ganha **+{_bpc} sala(s) grátis** automáticas!\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
        else:
            em.add_field(name=f"{STATS}  Suas Compras", value=(
                f"> {CART} Total comprado: **{total} salas**\n"
                f"> {PRESENTE} Bônus recebido (automático): **{resgatado} salas**\n"
                f"> {ON} Bônus gerado: **{bonus_total} salas** (+{_bpc} a cada {_br})"
            ), inline=False)
            em.add_field(name=f"{CLOCK}  Bônus Ganho", value=(
                f"> 24h: **+{ganho['dia']} salas**\n"
                f"> Semana: **+{ganho['semana']} salas**\n"
                f"> Total: **+{ganho['total']} salas**"
            ), inline=False)
        em.add_field(name=f"{CART}  Salas Disponíveis", value=f"> Você possui **{saldo} salas** no saldo.", inline=False)
        falta = b["falta_proximo"]; compradas_ciclo = _br - falta
        progresso = int((compradas_ciclo / _br) * 10)
        barra_str = f"`{'█' * progresso}{'░' * (10 - progresso)}` {compradas_ciclo}/{_br}"
        em.add_field(name=f"{GIFT}  Próximo Bônus", value=f"> {barra_str}\n> Compre mais **{falta} salas** para ganhar **+{_bpc} sala(s) grátis**!", inline=False)
        await inter.followup.send(embed=em, view=BonusPainelView(bonus_disp, saldo, total), ephemeral=True)


class TempoGoModoView(discord.ui.View):
    def __init__(self, go): super().__init__(timeout=120); self.go = go
    async def _c(self, i, m):
        await i.response.defer(ephemeral=True)
        uid = str(i.user.id)
        k, guild_pagou, gid = await _reservar_sala(i, m, uid, i.user.display_name)
        if not guild_pagou and not k:
            return await i.followup.send(embed=_err("Sem saldo", "Compre salas usando **Comprar Salas** no `/c`."), ephemeral=True)
        await _criar_sala_flow(i, m if (k and k["modo"] != 0) else m, self.go,
                               key_row=k, key_ja_consumida=True,
                               guild_pagou=guild_pagou, guild_id=gid)
    @discord.ui.button(label="Normal", emoji=PE["jogadores"], style=discord.ButtonStyle.primary, row=0)
    async def n(self, i, b): await self._c(i, 1)
    @discord.ui.button(label="Infinito", emoji=PE["play"], style=discord.ButtonStyle.primary, row=0)
    async def inf(self, i, b): await self._c(i, 2)
    @discord.ui.button(label="Full Capa", emoji=PE["top"], style=discord.ButtonStyle.primary, row=0)
    async def fc(self, i, b): await self._c(i, 3)


# ─── View de seleção de modo do /c3 ──────────────────────────
async def _c3_modo_btn(inter, modo):
    """Cria sala pelo modo escolhido no /c3. Bloqueia modos api1_only se estiver na API 2."""
    m = modo_info(modo)
    if m.get("api1_only"):
        from utils.api import api as _api
        if not _api._is_api1():
            return await inter.response.send_message(
                embed=_err("Modo indisponível", f"{OFF} O modo **{m['nome']}** só funciona na **API 1**."),
                ephemeral=True,
            )
    await _modo_btn(inter, modo)

class C3ModoView(discord.ui.View):
    def __init__(self): super().__init__(timeout=120)

    @discord.ui.button(label="Full Capa", emoji=PE["top"], style=discord.ButtonStyle.primary, row=0)
    async def full_capa(self, i, b): await _c3_modo_btn(i, 3)

    @discord.ui.button(label="F 1500 Ouro", emoji=PE["money"], style=discord.ButtonStyle.primary, row=0)
    async def f1500(self, i, b): await _c3_modo_btn(i, 4)


class TempoGoView(discord.ui.View):
    def __init__(self): super().__init__(timeout=120)
    @discord.ui.select(placeholder="⏱️ Selecione o tempo de GO", options=[discord.SelectOption(label=f"{i} minuto(s)", value=str(i)) for i in range(1, 11)])
    async def s(self, i, sel):
        go = int(sel.values[0])
        await i.response.edit_message(embed=_ok(f"GO: {go} min", f"{SETTINGS} Tempo definido.\nSelecione o modo:"), view=TempoGoModoView(go))


class ResgatarKeyModal(discord.ui.Modal, title="🗝️ Resgatar Key"):
    codigo = discord.ui.TextInput(label="Código da Key", placeholder="XXXX-XXXX-XXXX-XXXX", min_length=19, max_length=19)
    async def on_submit(self, i):
        await i.response.defer(thinking=True, ephemeral=True)
        code = self.codigo.value.strip().upper()
        ok, msg, row = resgatar_key(code, str(i.user.id), i.user.display_name)
        if not ok: return await i.followup.send(embed=_err(msg), ephemeral=True)
        s = row["quantia"] - row["salas_usadas"]
        em = _ok("Key Resgatada!")
        em.description = (
            f"{GIFT} **{s} sala(s)** adicionadas ao seu saldo!\n\n"
            f"{DOT} Use os botões do `/painel` ou os comandos:\n"
            f"> `/c1` {e('jogadores')} Normal\n"
            f"> `/c2` {PLAY} Infinito\n"
            f"> `/c3` {TOP} Full Capa"
        )
        await i.followup.send(embed=em, ephemeral=True)
        asyncio.create_task(_logs.log_key_resgatada(i.user, row["code"], row["quantia"], row["salas_usadas"]))


# ═══════════════════════════════════════════
#  Paginação saldo clientes
# ═══════════════════════════════════════════
_PPG = 20
def _saldo_emb(ords, tot, pg):
    med = [TOP, "🥈", "🥉"] + [DOT]*997
    tc=len(ords); tp=max(1,-(-tc//_PPG)); ini=pg*_PPG; fat=ords[ini:ini+_PPG]
    em = _emb(f"{STATS}  Saldo dos Clientes", desc=f"Atualizado em {_ts().strftime('%d/%m/%Y %H:%M')} (Brasília)")
    a,b=[],[]
    for idx,(uid,d) in enumerate(fat):
        r=ini+idx; l=f"{med[r]} **{d['nome']}**\n> {d['saldo']} salas {DOT} {d['keys']} key(s)"
        (a if idx%2==0 else b).append(l)
    if a: em.add_field(name="━━━━━━━━━━━━", value="\n\n".join(a), inline=True)
    if b: em.add_field(name="━━━━━━━━━━━━", value="\n\n".join(b), inline=True)
    em.add_field(name="\u200b", value=f"**{tc}** clientes {DOT} **{tot}** salas", inline=False)
    return em

class SaldoPagView(discord.ui.View):
    def __init__(self, o, t, p=0):
        super().__init__(timeout=300); self.o=o; self.t=t; self.p=p
        self.tp=max(1,-(-len(o)//_PPG)); self._u()
    def _u(self):
        self.bp.disabled=self.p==0; self.bn.disabled=self.p>=self.tp-1
        self.bi.label=f"📄 {self.p+1}/{self.tp}"
    @discord.ui.button(label="◀️", style=discord.ButtonStyle.secondary)
    async def bp(self,i,b): self.p-=1; self._u(); await i.response.edit_message(embed=_saldo_emb(self.o,self.t,self.p), view=self)
    @discord.ui.button(label="📄", style=discord.ButtonStyle.secondary, disabled=True)
    async def bi(self,i,b): await i.response.defer()
    @discord.ui.button(label="▶️", style=discord.ButtonStyle.secondary)
    async def bn(self,i,b): self.p+=1; self._u(); await i.response.edit_message(embed=_saldo_emb(self.o,self.t,self.p), view=self)


# ═══════════════════════════════════════════
#  Modais editar painel
# ═══════════════════════════════════════════
class EditarPainelModal(discord.ui.Modal, title="✏️ Editar Painel"):
    def __init__(self, cfg):
        super().__init__()
        self.t=discord.ui.TextInput(label="Título", default=cfg["titulo"], max_length=80)
        self.l=discord.ui.TextInput(label="Subtítulo", default=cfg["linha1"], max_length=100, required=False)
        self.d=discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, default=cfg["desc"], max_length=500, required=False)
        self.r=discord.ui.TextInput(label="Rodapé", default=cfg["rodape"], max_length=100, required=False)
        for x in [self.t,self.l,self.d,self.r]: self.add_item(x)
    async def on_submit(self, i):
        c = {"titulo":self.t.value.strip()or _PAINEL_DEF["titulo"],"linha1":self.l.value.strip(),"desc":self.d.value.strip()or _PAINEL_DEF["desc"],"rodape":self.r.value.strip()or _PAINEL_DEF["rodape"]}
        _save_painel(c)
        await i.response.send_message(embeds=[_ok("Painel Atualizado!"), _embed_painel(c)], ephemeral=True)

class EditarComprasModal(discord.ui.Modal, title="✏️ Editar Painel Compras"):
    def __init__(self):
        super().__init__()
        path = os.path.join(_cfg_dir(), "painel_compras_config.json")
        try:
            with open(path, encoding="utf-8") as f: cfg = json.load(f)
        except: cfg = {"titulo":"🛒 Comprar Salas","desc":"Adquira salas de forma rápida e segura.","rodape":"SalasFF Bot"}
        self.t=discord.ui.TextInput(label="Título", default=cfg["titulo"], max_length=80)
        self.d=discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, default=cfg["desc"], max_length=500, required=False)
        self.r=discord.ui.TextInput(label="Rodapé", default=cfg.get("rodape","SalasFF Bot"), max_length=100, required=False)
        for x in [self.t,self.d,self.r]: self.add_item(x)
    async def on_submit(self, i):
        c = {"titulo":self.t.value.strip(),"desc":self.d.value.strip(),"rodape":self.r.value.strip()}
        with open(os.path.join(_cfg_dir(),"painel_compras_config.json"),"w",encoding="utf-8") as f: json.dump(c,f,ensure_ascii=False)
        em = discord.Embed(title=c["titulo"], description=c["desc"], color=0x1abc9c)
        await i.response.send_message(embeds=[_ok("Painel Compras Atualizado!"), em], ephemeral=True)


# ═══════════════════════════════════════════
#  COG
# ═══════════════════════════════════════════
# ═══════════════════════════════════════════
#  CarteiraView — botões do /c
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
#  Meta de Salas
# ═══════════════════════════════════════════

def _meta_payload_v2(user, prog, pid_token: str = "meta"):
    """Monta payload Components V2 (com botões em caixa) para a meta.
    Retorna (payload, cor) ou (None, None) se sem meta."""
    if not prog:
        return None, None

    alvo = prog["alvo"]
    criadas = prog["criadas"]
    falta = prog["falta"]
    pct = prog["pct"]
    dias_rest = prog["dias_restantes"]
    media_at  = prog["media_atual"]
    concluida = prog["concluida"]
    expirou = prog["expirou"]

    blocos_total = 20
    cheios = int(pct / 100 * blocos_total)
    barra = "█" * cheios + "░" * (blocos_total - cheios)

    if concluida:
        cor = 0x00FF7F
        status = f"{e('on')} **Meta Concluída!**"
    elif expirou:
        cor = 0xFF4444
        status = f"{e('off')} **Meta Expirada**"
    elif pct >= 75:
        cor = 0x00D4FF
        status = f"{e('stats')} **Na reta final!**"
    elif pct >= 40:
        cor = 0xFFA500
        status = f"{e('stats')} **Em progresso**"
    else:
        cor = 0x5865F2
        status = f"{e('stats')} **Começando**"

    if dias_rest >= 1:
        tempo_txt = f"{int(dias_rest)} dia(s)"
    else:
        horas = int(dias_rest * 24)
        tempo_txt = f"{horas} hora(s)"

    cab = (
        f"## {e('top')} Meta de Salas — {user.display_name}\n"
        f"{status}\n"
        f"-# Meta de {prog['dias']} dia(s) • Criada em {prog['criado_em'][:10]}"
    )
    progresso_txt = f"**Progresso**\n```{barra}  {pct:.1f}%```"

    # Linha 1: Alvo, Criadas, Faltam (3 botões disabled)
    linha1 = {
        "id": 10, "type": 1, "components": [
            {"id": 11, "type": 2, "style": 2, "label": f"Alvo: {alvo}",       "custom_id": "meta:alvo",    "disabled": True, "emoji": _em("top")},
            {"id": 12, "type": 2, "style": 2, "label": f"Criadas: {criadas}", "custom_id": "meta:criadas", "disabled": True, "emoji": _em("carteira")},
            {"id": 13, "type": 2, "style": 2, "label": f"Faltam: {falta}",    "custom_id": "meta:faltam",  "disabled": True, "emoji": _em("vision")},
        ]
    }

    componentes = [
        {"id": 2, "type": 10, "content": cab},
        {"id": 3, "type": 10, "content": progresso_txt},
        linha1,
    ]

    if not expirou and not concluida:
        # Linha 2: Tempo Restante, Seu Ritmo
        linha2 = {
            "id": 14, "type": 1, "components": [
                {"id": 15, "type": 2, "style": 2, "label": f"Tempo: {tempo_txt}",         "custom_id": "meta:tempo", "disabled": True, "emoji": _em("clock")},
                {"id": 16, "type": 2, "style": 2, "label": f"Seu Ritmo: {int(media_at)}/dia", "custom_id": "meta:ritmo", "disabled": True, "emoji": _em("stats")},
            ]
        }
        componentes.append(linha2)

    # Botões interativos (Meta / Atualizar / Resetar)
    componentes.append({
        "id": 20, "type": 1, "components": [
            {"id": 21, "type": 2, "style": 3, "label": "Meta",        "custom_id": "cw:meta_config", "emoji": _em("top")},
            {"id": 22, "type": 2, "style": 2, "label": "Atualizar",   "custom_id": "cw:meta",        "emoji": _em("refresh")},
            {"id": 23, "type": 2, "style": 4, "label": "Resetar",     "custom_id": "cw:meta_reset",  "emoji": _em("off")},
        ]
    })

    payload = {
        "flags": 64 | 32768,
        "components": [{"id": 1, "type": 17, "accent_color": cor, "components": componentes}],
    }
    return payload, cor


def _meta_embed(user, prog):
    """Monta a embed bonita de meta do usuário."""
    if not prog:
        em = discord.Embed(
            title=f"{TOP}  Meta de Salas",
            color=0x5865F2,
            description=(
                f"{DOT} Você ainda **não tem uma meta ativa**.\n"
                f"{DOT} Clique em **Meta** abaixo para configurar quantas salas você quer\n"
                f"    criar e em quantos dias alcançar esse objetivo."
            ),
        )
        em.set_thumbnail(url=user.display_avatar.url)
        em.set_footer(text=f"{user.display_name} • Sem meta ativa")
        return em

    alvo = prog["alvo"]
    criadas = prog["criadas"]
    falta = prog["falta"]
    pct = prog["pct"]
    dias_rest = prog["dias_restantes"]
    media_nec = prog["media_necessaria"]
    media_at  = prog["media_atual"]
    concluida = prog["concluida"]
    expirou = prog["expirou"]

    # Barra de progresso
    blocos_total = 20
    cheios = int(pct / 100 * blocos_total)
    barra = "█" * cheios + "░" * (blocos_total - cheios)

    if concluida:
        cor = 0x00FF7F
        status = f"{ON} **Meta Concluída!**"
    elif expirou:
        cor = 0xFF4444
        status = f"{OFF} **Meta Expirada**"
    elif pct >= 75:
        cor = 0x00D4FF
        status = f"{STATS} **Na reta final!**"
    elif pct >= 40:
        cor = 0xFFA500
        status = f"{STATS} **Em progresso**"
    else:
        cor = 0x5865F2
        status = f"{STATS} **Começando**"

    em = discord.Embed(title=f"{TOP}  Meta de Salas — {user.display_name}", color=cor)
    em.set_thumbnail(url=user.display_avatar.url)

    em.description = (
        f"{status}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    em.add_field(
        name=f"{STATS}  Progresso",
        value=f"```{barra}  {pct:.1f}%```",
        inline=False,
    )
    em.add_field(name=f"{TOP}  Alvo",      value=f"`{alvo} salas`", inline=True)
    em.add_field(name=f"{CART}  Criadas",  value=f"`{criadas} salas`", inline=True)
    em.add_field(name=f"{DOT}  Faltam",    value=f"`{falta} salas`", inline=True)

    if not expirou and not concluida:
        if dias_rest >= 1:
            tempo_txt = f"{int(dias_rest)} dia(s)"
        else:
            horas = int(dias_rest * 24)
            tempo_txt = f"{horas} hora(s)"
        em.add_field(name=f"{CLOCK}  Tempo Restante", value=f"`{tempo_txt}`", inline=True)
        em.add_field(name=f"{DOT}  Seu Ritmo",        value=f"`{int(media_at)} salas/dia`", inline=True)

    em.set_footer(text=f"Meta de {prog['dias']} dia(s) • Criada em {prog['criado_em'][:10]}")
    return em


class MetaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Meta", emoji=PE["top"], style=discord.ButtonStyle.success, row=0)
    async def btn_meta(self, inter, btn):
        from utils.database import meta_get
        atual = await asyncio.to_thread(meta_get, str(inter.user.id))
        await inter.response.send_modal(MetaConfigModal(atual))

    @discord.ui.button(label="Atualizar", emoji=PE["refresh"], style=discord.ButtonStyle.secondary, row=0)
    async def btn_refresh(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import meta_progresso
        prog = await asyncio.to_thread(meta_progresso, str(inter.user.id))
        em = _meta_embed(inter.user, prog)
        try:
            await inter.edit_original_response(embed=em, view=self)
        except Exception:
            await inter.followup.send(embed=em, view=self, ephemeral=True)

    @discord.ui.button(label="Resetar Meta", emoji=PE["off"], style=discord.ButtonStyle.danger, row=0)
    async def btn_reset(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import meta_delete, meta_get
        if not await asyncio.to_thread(meta_get, str(inter.user.id)):
            return await inter.followup.send(embed=_err("Sem meta", f"{DOT} Você não tem meta ativa."), ephemeral=True)
        await asyncio.to_thread(meta_delete, str(inter.user.id))
        em = _ok("Meta Removida")
        em.description = f"{DOT} Sua meta foi apagada. Configure uma nova quando quiser."
        await inter.followup.send(embed=em, ephemeral=True)


class MetaConfigModal(discord.ui.Modal, title="🎯 Configurar Meta de Salas"):
    alvo = discord.ui.TextInput(
        label="Quantas salas você quer criar?",
        placeholder="Ex: 100",
        min_length=1, max_length=6,
    )
    dias = discord.ui.TextInput(
        label="Em quantos dias? (ex: 7)",
        placeholder="Ex: 7",
        min_length=1, max_length=4,
    )

    def __init__(self, atual=None):
        super().__init__()
        if atual:
            self.alvo.default = str(atual.get("alvo", ""))
            self.dias.default = str(atual.get("dias", ""))

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        try:
            alvo = int(self.alvo.value.strip())
            dias = int(self.dias.value.strip())
            if alvo < 1 or dias < 1: raise ValueError
        except ValueError:
            return await inter.followup.send(
                embed=_err("Valores inválidos", f"{DOT} Use números inteiros positivos."),
                ephemeral=True,
            )
        if dias > 365:
            return await inter.followup.send(
                embed=_err("Prazo muito grande", f"{DOT} Máximo: **365 dias**."),
                ephemeral=True,
            )

        from utils.database import meta_set, meta_progresso
        await asyncio.to_thread(meta_set, str(inter.user.id), alvo, dias)
        prog = await asyncio.to_thread(meta_progresso, str(inter.user.id))
        em = _meta_embed(inter.user, prog)
        em.description = f"{ON} **Meta configurada com sucesso!**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        await inter.followup.send(embed=em, view=MetaView(), ephemeral=True)


# ═══════════════════════════════════════════
#  CarteiraView — botões do /c
# ═══════════════════════════════════════════

# ═══════════════════════════════════════════
#  Tutorial Bônus — embed única explicativa
# ═══════════════════════════════════════════
def _tutorial_bonus_v2_payload() -> dict:
    """Gera payload Components V2 do tutorial — embed única, sem navegação."""
    texto = (
        f"{e('adduser')}  **1. Adicione o bot aos seus aplicativos**\n"
        f"-# Clique no perfil do bot e em **Adicionar aos Apps**. Depois disso você pode usar os comandos em qualquer servidor ou DM.\n\n"
        f"{e('carteira')}  **2. Use o comando `/c`**\n"
        f"-# Esse é o comando da sua carteira — mostra seu saldo, histórico e opções.\n\n"
        f"{e('settings')}  **3. Clique em Outros**\n"
        f"-# Dentro da carteira, toque no botão **Outros** para abrir mais opções.\n\n"
        f"{e('presente')}  **4. Clique em Bônus**\n"
        f"-# Vai aparecer seu painel de bônus com quanto você já acumulou e quantas salas grátis estão disponíveis.\n\n"
        f"{e('otherdollar')}  **5. Resgate suas salas grátis**\n"
        f"-# Clique em **Resgatar** e suas salas bônus vão direto pro seu saldo. Prontas pra usar em `/c1` `/c2` `/c3`."
    )

    return {
        "components": [{"id": 1, "type": 17, "accent_color": 0xFFD700, "components": [
            {"id": 2, "type": 10, "content": f"-# {e('bot')}  Equipe F Applications"},
            {"id": 3, "type": 10, "content": f"## {e('presente')}  Como Pegar Seu Bônus"},
            {"id": 4, "type": 14, "divider": True, "spacing": 1},
            {"id": 5, "type": 10, "content": texto},
            {"id": 6, "type": 14, "divider": True, "spacing": 1},
            {"id": 7, "type": 10, "content": (
                f"{e('click')}  **Dica:** a cada **10 salas compradas** você ganha **+2 salas grátis** automaticamente."
            )},
            {"id": 8, "type": 10, "content": "-# F Applications • Tutorial de bônus"},
        ]}],
        "flags": 32768,
    }


def _tutorial_bonus_embed() -> discord.Embed:
    """Fallback clássico caso Components V2 falhe."""
    em = discord.Embed(
        title=f"{PRESENTE}  Como Pegar Seu Bônus",
        color=0xFFD700,
    )
    em.description = (
        f"-# {PE['bot']}  Equipe F Applications\n\n"
        f"{ADDUSER}  **1. Adicione o bot aos seus aplicativos**\n"
        f"-# Clique no perfil do bot e em **Adicionar aos Apps**. Depois disso você pode usar os comandos em qualquer servidor ou DM.\n\n"
        f"{CART}  **2. Use o comando `/c`**\n"
        f"-# Esse é o comando da sua carteira — mostra seu saldo, histórico e opções.\n\n"
        f"{SETTINGS}  **3. Clique em Outros**\n"
        f"-# Dentro da carteira, toque no botão **Outros** para abrir mais opções.\n\n"
        f"{PRESENTE}  **4. Clique em Bônus**\n"
        f"-# Vai aparecer seu painel de bônus com quanto você já acumulou e quantas salas grátis estão disponíveis.\n\n"
        f"{DOLLAR}  **5. Resgate suas salas grátis**\n"
        f"-# Clique em **Resgatar** e suas salas bônus vão direto pro seu saldo. Prontas pra usar em `/c1` `/c2` `/c3`.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{CLICK}  **Dica:** a cada **10 salas compradas** você ganha **+2 salas grátis** automaticamente."
    )
    em.set_footer(text="F Applications • Tutorial de bônus")
    return em


class CarteiraView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Comprar Salas", emoji=PE["carteira"], style=discord.ButtonStyle.success, row=0)
    async def comprar(self, inter, btn):
        from cogs.comprar import ComprarModal
        gid = str(inter.guild.id) if inter.guild else None
        await inter.response.send_modal(ComprarModal(gid))

    @discord.ui.button(label="Ver Lucro", emoji=PE["stats"], style=discord.ButtonStyle.primary, row=0)
    async def lucro(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import lucro_config_get, vendas_usuario, stats_usuario_por_guilds
        uid = str(inter.user.id)
        cfg = await asyncio.to_thread(lucro_config_get, uid)
        v   = await asyncio.to_thread(vendas_usuario, uid)
        vps = cfg["valor_por_sala"]
        orgs = cfg.get("orgs", [])

        em = discord.Embed(
            title=f"{STATS}  Lucro — SalasFF",
            color=0x2ecc71,
        )

        if vps == 0 and not orgs:
            em.description = (
                f"{SETTINGS} Configure seu lucro clicando em **Alterar Preço**!\n\n"
                f"{DOT} Defina quanto você ganha por sala vendida\n"
                f"{DOT} Clique em **+ Org** se trabalha em mais de uma org"
            )
        else:
            if vps > 0:
                em.add_field(
                    name=f"{MONEY}  Hoje",
                    value=f"> {STATS} **{v['hoje']}** vendidas\n> R$ **{v['hoje']*vps:.2f}**",
                    inline=True,
                )
                em.add_field(
                    name=f"{MONEY}  Ontem",
                    value=f"> {STATS} **{v['ontem']}** vendidas\n> R$ **{v['ontem']*vps:.2f}**",
                    inline=True,
                )
                em.add_field(
                    name=f"{MONEY}  Semana",
                    value=f"> {STATS} **{v['semana']}** vendidas\n> R$ **{v['semana']*vps:.2f}**",
                    inline=True,
                )
                em.add_field(
                    name=f"{DOLLAR}  Total",
                    value=f"> {STATS} **{v['total']}** vendidas\n> R$ **{v['total']*vps:.2f}**",
                    inline=False,
                )

            if orgs:
                guild_ids = [org.get("guild_id", "") for org in orgs]
                org_stats = await asyncio.to_thread(stats_usuario_por_guilds, uid, guild_ids)

                for idx, org in enumerate(orgs):
                    gid = org.get("guild_id", "")
                    nome = org.get("nome", f"Org {idx+1}")
                    val = org.get("valor", 0)
                    st = org_stats.get(gid, {"hoje": 0, "ontem": 0, "7dias": 0, "total": 0})

                    em.add_field(
                        name=f"{TOP}  {nome}  (R$ {val:.2f}/sala)",
                        value=(
                            f"> Hoje: **{st['hoje']}** → R$ **{st['hoje']*val:.2f}**\n"
                            f"> Ontem: **{st.get('ontem',0)}** → R$ **{st.get('ontem',0)*val:.2f}**\n"
                            f"> Semana: **{st['7dias']}** → R$ **{st['7dias']*val:.2f}**\n"
                            f"> Total: **{st['total']}** → R$ **{st['total']*val:.2f}**"
                        ),
                        inline=False,
                    )

        await inter.followup.send(embed=em, view=LucroSubView(), ephemeral=True)

    @discord.ui.button(label="Config GO", emoji=PE["settings"], style=discord.ButtonStyle.secondary, row=0)
    async def btn_go(self, inter, btn):
        uid = str(inter.user.id)
        atual = await asyncio.to_thread(go_config_get, uid)
        label = f"{atual} min" if atual > 0 else f"{config.DEFAULT_INICIAR_MINUTOS} min (padrão)"
        em = _info("Tempo de GO", f"{SETTINGS} Tempo atual: **{label}**\n{DOT} Selecione abaixo o tempo para início automático da sala:")
        await inter.response.send_message(embed=em, view=ConfigGoView(), ephemeral=True)

    @discord.ui.button(label="Bônus", emoji=PE["presente"], style=discord.ButtonStyle.primary, row=1)
    async def btn_bonus(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import bonus_info, get_bonus_config, saldo_total_usuario, bonus_ganho_periodo
        uid = str(inter.user.id)
        b = await asyncio.to_thread(bonus_info, uid)
        saldo = await asyncio.to_thread(saldo_total_usuario, uid)
        ganho = await asyncio.to_thread(bonus_ganho_periodo, uid)
        _br, _bpc = get_bonus_config()

        em = discord.Embed(
            title=f"{PRESENTE}  Painel de Bônus",
            color=0xFFD700,
        )
        em.set_thumbnail(url=inter.user.display_avatar.url)

        total = b["total_comprado"]
        bonus_disp = b["bonus_disponivel"]
        bonus_total = b["bonus_total"]
        resgatado = b["bonus_resgatado"]

        if total == 0:
            em.description = (
                f"{OFF} Você ainda não comprou salas.\n\n"
                f"{DOT} A cada **{_br} salas** compradas você ganha **+{_bpc} sala(s) grátis** automáticas!\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
        else:
            em.add_field(
                name=f"{STATS}  Suas Compras",
                value=(
                    f"> {CART} Total comprado: **{total} salas**\n"
                    f"> {PRESENTE} Bônus recebido (automático): **{resgatado} salas**\n"
                    f"> {ON} Bônus gerado: **{bonus_total} salas** (+{_bpc} a cada {_br})"
                ),
                inline=False,
            )
            em.add_field(
                name=f"{CLOCK}  Bônus Ganho",
                value=(
                    f"> 24h: **+{ganho['dia']} salas**\n"
                    f"> Semana: **+{ganho['semana']} salas**\n"
                    f"> Total: **+{ganho['total']} salas**"
                ),
                inline=False,
            )

        # ── Salas disponíveis ──
        em.add_field(
            name=f"{CART}  Salas Disponíveis",
            value=f"> Você possui **{saldo} salas** no saldo.",
            inline=False,
        )

        # ── Progresso pro próximo bônus ──
        falta = b["falta_proximo"]
        compradas_ciclo = _br - falta
        progresso = int((compradas_ciclo / _br) * 10)
        barra = f"`{'█' * progresso}{'░' * (10 - progresso)}` {compradas_ciclo}/{_br}"
        em.add_field(
            name=f"{GIFT}  Próximo Bônus",
            value=f"> {barra}\n> Compre mais **{falta} salas** para ganhar **+{_bpc} sala(s) grátis**!",
            inline=False,
        )

        await inter.followup.send(embed=em, view=BonusPainelView(bonus_disp, saldo, total), ephemeral=True)


class BonusPainelView(discord.ui.View):
    """Painel de bônus com Select Menu para Resgatar Bônus e ver Salas Disponíveis + botão resetar."""
    def __init__(self, bonus_disp: int, saldo: int, total_comprado: int):
        super().__init__(timeout=300)
        self.bonus_disp = bonus_disp
        self.saldo = saldo
        self.total_comprado = total_comprado

    @discord.ui.select(
        placeholder=f"Selecione uma opção...",
        options=[
            discord.SelectOption(
                label="Resgatar Bônus",
                description="Resgate suas salas bônus acumuladas",
                emoji=PE["presente"],
                value="resgatar",
            ),
            discord.SelectOption(
                label="Salas Disponíveis",
                description="Veja quantas salas você tem para usar",
                emoji=PE["carteira"],
                value="salas",
            ),
        ],
        row=0,
    )
    async def select_bonus(self, inter, select):
        choice = select.values[0]

        if choice == "resgatar":
            await inter.response.defer(ephemeral=True)
            from utils.database import bonus_resgatar, bonus_info
            uid = str(inter.user.id)

            b = await asyncio.to_thread(bonus_info, uid)
            if b["bonus_disponivel"] <= 0:
                em = discord.Embed(
                    title=f"{OFF}  Sem Bônus Disponível",
                    color=0xFF4444,
                )
                em.description = (
                    f"{DOT} Você não tem bônus para resgatar no momento.\n\n"
                    f"{PRESENTE} Continue comprando salas para acumular bônus!\n"
                    f"{CART} Total comprado: **{b['total_comprado']} salas**\n"
                    f"{GIFT} Faltam **{b['falta_proximo']} salas** para o próximo bônus!"
                )
                return await inter.followup.send(embed=em, ephemeral=True)

            ok, salas, msg = await asyncio.to_thread(bonus_resgatar, uid, inter.user.display_name)

            if not ok:
                em = _err("Sem Bônus", msg)
                return await inter.followup.send(embed=em, ephemeral=True)

            em = discord.Embed(
                title=f"{ON}  Bônus Resgatado!",
                color=0x00FF7F,
            )
            em.set_thumbnail(url=inter.user.display_avatar.url)
            em.description = (
                f"{PRESENTE} **{salas} sala(s) bônus** adicionadas ao seu saldo!\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{DOT} Use `/c1`, `/c2`, `/c3` para criar salas\n"
                f"{DOT} Continue comprando para acumular mais bônus!"
            )
            await inter.followup.send(embed=em, ephemeral=True)
            asyncio.create_task(_logs.log_bonus_resgatado(inter.user, salas))
            asyncio.create_task(_logs.log_pub_bonus(
                str(inter.user.id), inter.user.display_name, salas,
                avatar_url=inter.user.display_avatar.url,
            ))

        elif choice == "salas":
            await inter.response.defer(ephemeral=True)
            from utils.database import saldo_total_usuario, perfil_usuario, bonus_info
            uid = str(inter.user.id)
            saldo = await asyncio.to_thread(saldo_total_usuario, uid)
            d = await asyncio.to_thread(perfil_usuario, uid)
            b = await asyncio.to_thread(bonus_info, uid)

            em = discord.Embed(
                title=f"{CART}  Salas Disponíveis",
                color=0x5865F2,
            )
            em.set_thumbnail(url=inter.user.display_avatar.url)
            em.add_field(
                name=f"{PRESENTE}  Bônus",
                value=(
                    f"> Total comprado: **{b['total_comprado']} salas**\n"
                    f"> Bônus disponível: **{b['bonus_disponivel']} sala(s)**\n"
                    f"> Próximo bônus em: **{b['falta_proximo']} salas**"
                ),
                inline=False,
            )
            await inter.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="Resetar Faixa", emoji=PE["reloading"], style=discord.ButtonStyle.danger, row=1)
    async def btn_resetar(self, inter, btn):
        """Mostra aviso de confirmação antes de resetar a faixa."""
        await inter.response.defer(ephemeral=True)
        from utils.database import bonus_info
        uid = str(inter.user.id)
        b = await asyncio.to_thread(bonus_info, uid)

        if b["total_comprado"] == 0:
            em = _err("Nada para Resetar", f"{DOT} Você não tem dados de bônus para resetar.")
            return await inter.followup.send(embed=em, ephemeral=True)

        em = discord.Embed(
            title=f"{RAGE}  Resetar Faixa de Bônus",
            color=0xFF4444,
        )
        em.description = (
            f"**⚠️ ATENÇÃO — Esta ação é irreversível!**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{STATS} Seus dados atuais:\n"
            f"> {CART} Total comprado: **{b['total_comprado']} salas**\n"
            f"> {PRESENTE} Bônus resgatado: **{b['bonus_resgatado']} salas**\n"
            f"> {ON} Bônus gerado: **{b['bonus_total']} salas**\n\n"
            f"{RAGE} Ao confirmar:\n"
            f"> {TRASH} Seu contador de compras volta a **0**\n"
            f"> {TRASH} Seu bônus resgatado volta a **0**\n\n"
            f"{DOT} Tem certeza que deseja **resetar**?"
        )

        if b["bonus_disponivel"] > 0:
            em.add_field(
                name=f"{PRESENTE}  Bônus Pendente!",
                value=f"> Você tem **{b['bonus_disponivel']} sala(s)** para resgatar!\n> **Resgate antes de resetar!**",
                inline=False,
            )

        await inter.followup.send(embed=em, view=BonusResetConfirmView(), ephemeral=True)


class BonusResetConfirmView(discord.ui.View):
    """Confirmação de reset da faixa de bônus."""
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Confirmar Reset", emoji=PE["othertrash"], style=discord.ButtonStyle.danger, row=0)
    async def confirmar(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import bonus_resetar
        uid = str(inter.user.id)
        ok, msg = await asyncio.to_thread(bonus_resetar, uid)

        if not ok:
            em = _err("Não foi possível resetar", msg)
            return await inter.followup.send(embed=em, ephemeral=True)

        # Desabilita botões
        for item in self.children:
            item.disabled = True

        em = discord.Embed(
            title=f"{ON}  Faixa Resetada!",
            color=0x00FF7F,
        )
        em.description = (
            f"{RELOADING} Sua faixa de bônus foi resetada com sucesso!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{DOT} Seu contador voltou a **0 salas**\n"
            f"{DOT} Compre mais salas para acumular bônus novamente!\n"
            f"{PRESENTE} Próxima faixa: **100+ salas → +5%**"
        )
        await inter.edit_original_response(embed=em, view=self)

    @discord.ui.button(label="Cancelar", emoji=PE["off"], style=discord.ButtonStyle.secondary, row=0)
    async def cancelar(self, inter, btn):
        for item in self.children:
            item.disabled = True
        em = discord.Embed(
            title=f"{ON}  Reset Cancelado",
            description=f"{DOT} Sua faixa de bônus foi mantida.",
            color=0x5865F2,
        )
        await inter.response.edit_message(embed=em, view=self)


# ═══════════════════════════════════════════
#  /adm — Painel unificado de admin
# ═══════════════════════════════════════════

class AdmSelectView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.select(
        placeholder="Selecione uma opção...",
        options=[
            discord.SelectOption(label="Perfil de Usuário", description="Ver perfil detalhado de um usuário", emoji=PE["vision"], value="perfil"),
            discord.SelectOption(label="Saldo Clientes", description="Todos os clientes com saldo", emoji=PE["carteira"], value="saldocliente"),
            discord.SelectOption(label="Remover Salas", description="Remove salas de um cliente", emoji=PE["othertrash"], value="removersalas"),
            discord.SelectOption(label="Gerar Keys", description="Gera keys de salas", emoji=PE["pix"], value="gerasala"),
            discord.SelectOption(label="Verificar Compras", description="Compras de um usuário", emoji=PE["stats"], value="verifica"),
            discord.SelectOption(label="Verificar Key", description="Info de uma key específica", emoji=PE["click"], value="verificakey"),
            discord.SelectOption(label="Estatísticas", description="Estatísticas globais do bot", emoji=PE["channel"], value="saldototal"),
            discord.SelectOption(label="Lucro", description="Lucro estimado por período", emoji=PE["carteira"], value="lucro"),
            discord.SelectOption(label="Logs Públicos", description="Configura canais de log", emoji=PE["settings"], value="logs"),
            discord.SelectOption(label="Simular Compra", description="Simula pagamento de teste", emoji=PE["on"], value="simular"),
            discord.SelectOption(label="Backup", description="Backup manual dos dados", emoji=PE["cloud"], value="backup"),
            discord.SelectOption(label="Bot Config", description="Configurações do bot", emoji=PE["settings"], value="botconfig"),
        ],
        row=0,
    )
    async def adm_select(self, inter, sel):
        choice = sel.values[0]

        if choice == "perfil":
            em = _info("Perfil de Usuário", f"{DOT} Envie no chat o **@usuário** que deseja consultar.\n{SETTINGS} Aguardando... *(30s)*")
            await inter.response.send_message(embed=em, ephemeral=True)
            def check(m): return m.author.id == inter.user.id and m.channel.id == inter.channel.id
            try:
                msg = await inter.client.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                return await inter.followup.send(embed=_err("Tempo esgotado."), ephemeral=True)
            try: await msg.delete()
            except: pass
            import re
            match = re.search(r'(\d{15,21})', msg.content)
            if not match:
                return await inter.followup.send(embed=_err("Usuário inválido."), ephemeral=True)
            try:
                usuario = await inter.client.fetch_user(int(match.group(1)))
            except:
                return await inter.followup.send(embed=_err("Usuário não encontrado."), ephemeral=True)
            uid = str(usuario.id)
            d = await asyncio.to_thread(perfil_usuario, uid)
            rows = await asyncio.to_thread(pedidos_por_id, uid)
            pedidos = [dict(r) for r in rows] if rows else []
            pagos = [p for p in pedidos if p.get("status") == "pago"]
            em = discord.Embed(title=f"{INFO}  Perfil de {usuario.display_name}", color=0x5865F2)
            em.set_thumbnail(url=usuario.display_avatar.url)
            em.add_field(name=f"{CART}  Saldo Disponível", value=f"> **{d['saldo']}** sala(s)", inline=False)
            em.add_field(name=f"{STATS}  Salas Gastas", value=f"> Hoje: **{d['hoje']}** • Ontem: **{d['ontem']}** • 7d: **{d['semana']}** • Total: **{d['total']}**", inline=False)
            total_gasto = sum(float(p.get("valor") or 0) for p in pagos)
            total_compradas = sum(p.get("quantia", 0) for p in pagos)
            em.add_field(name=f"{MONEY}  Compras", value=f"> **{len(pagos)}** compras • **{total_compradas}** salas • **R$ {total_gasto:.2f}**", inline=False)
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "saldocliente":
            await inter.response.defer(ephemeral=True)
            keys = await asyncio.to_thread(todas_keys_com_saldo)
            if not keys:
                return await inter.followup.send(embed=_info("Saldo Clientes", f"{OFF} Nenhum cliente."), ephemeral=True)
            cl = {}
            for k in keys:
                uid = k["dono_id"]; s = k["quantia"] - k["salas_usadas"]
                if uid not in cl: cl[uid] = {"nome": k["dono_nome"] or "?", "saldo": 0}
                cl[uid]["saldo"] += s
            o = sorted(cl.items(), key=lambda x: x[1]["saldo"], reverse=True)
            t = sum(v["saldo"] for v in cl.values())
            linhas = [f"> {DOT} **{v['nome']}** — **{v['saldo']}** salas" for _, v in o[:20]]
            em = _emb(f"{CART}  Saldo Clientes", 0x5865F2)
            em.description = f"Total: **{t} salas** em **{len(cl)} clientes**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" + "\n".join(linhas)
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "removersalas":
            em = _info("Remover Salas", f"{DOT} Envie no chat: **@usuário quantidade**\n{DOT} Ex: `@bruno 50`\n{SETTINGS} Aguardando... *(30s)*")
            await inter.response.send_message(embed=em, ephemeral=True)
            def check(m): return m.author.id == inter.user.id and m.channel.id == inter.channel.id
            try:
                msg = await inter.client.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                return await inter.followup.send(embed=_err("Tempo esgotado."), ephemeral=True)
            try: await msg.delete()
            except: pass
            import re
            match = re.match(r'<?@?!?(\d{15,21})>?\s+(\d+)', msg.content.strip())
            if not match:
                return await inter.followup.send(embed=_err("Formato inválido", f"{DOT} Use: `@usuario quantidade`"), ephemeral=True)
            uid, qtd = match.group(1), int(match.group(2))
            rm, sf = await asyncio.to_thread(remover_salas_cliente, uid, qtd)
            if rm == 0:
                return await inter.followup.send(embed=_err("Sem saldo."), ephemeral=True)
            em = _ok("Salas Removidas")
            em.description = f"{TRASH} **{rm}** salas removidas de <@{uid}>\n{DOT} Saldo restante: **{sf}**"
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "gerasala":
            em = _info("Gerar Keys", f"{DOT} Envie no chat: **salas_por_key quantidade_keys**\n{DOT} Ex: `100 5` (5 keys de 100 salas)\n{SETTINGS} Aguardando... *(30s)*")
            await inter.response.send_message(embed=em, ephemeral=True)
            def check(m): return m.author.id == inter.user.id and m.channel.id == inter.channel.id
            try:
                msg = await inter.client.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                return await inter.followup.send(embed=_err("Tempo esgotado."), ephemeral=True)
            try: await msg.delete()
            except: pass
            import re
            match = re.match(r'(\d+)\s+(\d+)', msg.content.strip())
            if not match:
                return await inter.followup.send(embed=_err("Formato inválido", f"{DOT} Use: `salas_por_key quantidade`"), ephemeral=True)
            quantia, nkeys = int(match.group(1)), int(match.group(2))
            cr = await asyncio.to_thread(criar_keys, quantia, 0, nkeys, str(inter.user.id))
            em = _emb(f"{GIFT}  {len(cr)} Key(s) Gerada(s)", 0xA855F7)
            em.description = f"{DOT} **Salas/key:** {quantia} • **Keys:** {len(cr)}"
            em.add_field(name=f"{GIFT} Códigos", value="\n".join(f"`{k['code']}`" for k in cr[:25]), inline=False)
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "verifica":
            em = _info("Verificar Compras", f"{DOT} Envie no chat o **@usuário**.\n{SETTINGS} Aguardando... *(30s)*")
            await inter.response.send_message(embed=em, ephemeral=True)
            def check(m): return m.author.id == inter.user.id and m.channel.id == inter.channel.id
            try:
                msg = await inter.client.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                return await inter.followup.send(embed=_err("Tempo esgotado."), ephemeral=True)
            try: await msg.delete()
            except: pass
            import re
            match = re.search(r'(\d{15,21})', msg.content)
            if not match:
                return await inter.followup.send(embed=_err("Usuário inválido."), ephemeral=True)
            try:
                usuario = await inter.client.fetch_user(int(match.group(1)))
            except:
                return await inter.followup.send(embed=_err("Não encontrado."), ephemeral=True)
            rows = await asyncio.to_thread(pedidos_por_id, str(usuario.id))
            pds = [dict(r) for r in rows] if rows else []
            if not pds:
                return await inter.followup.send(embed=_info("Compras", f"{OFF} Nenhuma compra de {usuario.mention}."), ephemeral=True)
            pagos = [p for p in pds if p.get("status") == "pago"]
            total = sum(float(p.get("valor") or 0) for p in pagos)
            em = _emb(f"{STATS}  Compras — {usuario.display_name}", 0x2ecc71)
            em.description = f"**{len(pds)}** compra(s) • **{len(pagos)}** pagas • **R$ {total:.2f}**"
            for p in sorted(pagos, key=lambda x: x.get("pago_em", ""), reverse=True)[:10]:
                dt = (p.get("pago_em") or "")[:16].replace("T", " ")
                em.add_field(name=f"{ON} {dt}", value=f"**{p.get('quantia','?')} salas** — R$ {float(p.get('valor') or 0):.2f}", inline=True)
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "verificakey":
            em = _info("Verificar Key", f"{DOT} Envie no chat o **código da key**.\n{SETTINGS} Aguardando... *(30s)*")
            await inter.response.send_message(embed=em, ephemeral=True)
            def check(m): return m.author.id == inter.user.id and m.channel.id == inter.channel.id
            try:
                msg = await inter.client.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                return await inter.followup.send(embed=_err("Tempo esgotado."), ephemeral=True)
            try: await msg.delete()
            except: pass
            row = await asyncio.to_thread(buscar_key, msg.content.strip().upper())
            if not row:
                return await inter.followup.send(embed=_err(f"Key não encontrada."), ephemeral=True)
            s = row["quantia"] - row["salas_usadas"]
            em = _emb(f"{GIFT}  Info Key", 0xA855F7)
            em.add_field(name="Key", value=f"`{row['code']}`", inline=False)
            em.add_field(name="Dono", value=row.get("dono_nome", "—"), inline=True)
            em.add_field(name="Saldo", value=f"**{s}**", inline=True)
            em.add_field(name="Total", value=f"{row['quantia']}", inline=True)
            em.add_field(name="Usadas", value=f"{row['salas_usadas']}", inline=True)
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "saldototal":
            await inter.response.defer(ephemeral=True)
            from utils.database import stats_globais, top_compradores
            s = await asyncio.to_thread(stats_globais)
            tp = await asyncio.to_thread(top_compradores)
            em = _emb(f"{STATS}  Estatísticas Globais", 0x5865F2)
            em.add_field(name=f"{ON} Salas", value=f"> Hoje: **{s['salas_hoje']}** • 7d: **{s['salas_7d']}** • Total: **{s['salas_tot']}**", inline=False)
            em.add_field(name=f"{CART} Vendas", value=f"> Hoje: **{s['vendas_hoje']}** • 7d: **{s['vendas_7d']}** • Total: **{s['vendas_tot']}**", inline=False)
            em.add_field(name=f"{MONEY} Receita", value=f"> Hoje: **R$ {s['receita_hoje']:.2f}** • 7d: **R$ {s['receita_7d']:.2f}** • Total: **R$ {s['receita_tot']:.2f}**", inline=False)
            if tp:
                md = [TOP, "🥈", "🥉", "4️⃣", "5️⃣"]
                em.add_field(name=f"{TOP} Top Compradores", value="\n".join(f"{md[j]} **{r['user_nome']}** — {r['total_salas']} salas" for j, r in enumerate(tp[:5])), inline=False)
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "lucro":
            await inter.response.defer(ephemeral=True)
            d = await asyncio.to_thread(lucro_periodo)
            lps = 0.03
            em = _emb(f"{MONEY}  Lucro", 0x2ecc71)
            for lb, vs, cs in [("Hoje", d["vendidas_hoje"], d["criadas_hoje"]), ("7d", d["vendidas_7d"], d["criadas_7d"])]:
                em.add_field(name=lb, value=f"**{vs}** vendidas • **{cs}** criadas\n**R$ {round((vs+cs)*lps,2):.2f}**", inline=True)
            total = d["vendidas_tot"] + d["criadas_tot"]
            em.add_field(name="Total", value=f"**{d['vendidas_tot']}** vendidas • **{d['criadas_tot']}** criadas\n**R$ {round(total*lps,2):.2f}**", inline=False)
            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "logs":
            from cogs.botconfig import carregar_cfg
            cfg = carregar_cfg()
            compras_id = cfg.get("canal_compras_pub_id")
            bonus_id = cfg.get("canal_bonus_pub_id")
            compras_txt = f"<#{compras_id}>" if compras_id else f"{OFF} *Não definido*"
            bonus_txt = f"<#{bonus_id}>" if bonus_id else f"{OFF} *Não definido*"
            em = _emb(f"{SETTINGS}  Canais de Log")
            em.add_field(name=f"{CART} Compras", value=f"> {compras_txt}", inline=True)
            em.add_field(name=f"{GIFT} Bônus", value=f"> {bonus_txt}", inline=True)
            await inter.response.send_message(embed=em, view=LogsPubView(), ephemeral=True)

        elif choice == "simular":
            em = _info("Simular Compra", f"{DOT} Envie no chat a **quantidade de salas**.\n{DOT} Ex: `100`\n{SETTINGS} Aguardando... *(30s)*")
            await inter.response.send_message(embed=em, ephemeral=True)
            def check(m): return m.author.id == inter.user.id and m.channel.id == inter.channel.id
            try:
                msg = await inter.client.wait_for("message", check=check, timeout=30)
            except asyncio.TimeoutError:
                return await inter.followup.send(embed=_err("Tempo esgotado."), ephemeral=True)
            try: await msg.delete()
            except: pass
            try:
                qtd = int(msg.content.strip())
                if qtd < 1: raise ValueError
            except:
                return await inter.followup.send(embed=_err("Quantidade inválida."), ephemeral=True)
            from utils.pix import get_preco_por_sala_guild
            gid = str(inter.guild.id) if inter.guild else None
            preco = get_preco_por_sala_guild(gid)
            valor = round(qtd * preco, 2)
            from cogs.comprar import SimularAprovarView
            em = _emb(f"{MONEY}  Simulação de Compra", 0xA855F7)
            em.description = f"{DOT} **Salas:** {qtd}\n{DOT} **Valor:** R$ {valor:.2f}\n\n{SETTINGS} Clique em **Aprovar** para creditar."
            await inter.followup.send(embed=em, view=SimularAprovarView(str(inter.user.id), inter.user.display_name, qtd, valor), ephemeral=True)

        elif choice == "backup":
            await inter.response.defer(ephemeral=True)
            cog = inter.client.get_cog("ComprarCog")
            if cog:
                await cog._backup("manual /adm")
                await inter.followup.send(embed=_ok("Backup enviado!"), ephemeral=True)
            else:
                await inter.followup.send(embed=_err("ComprarCog não encontrado."), ephemeral=True)

        elif choice == "botconfig":
            from cogs.botconfig import BotConfigView, carregar_cfg, _painel_emb
            cfg = carregar_cfg()
            await inter.response.send_message(embed=_painel_emb(cfg, inter.guild), view=BotConfigView(), ephemeral=True)


class ConfigGoView(discord.ui.View):
    """Select de 1-10 min para configurar o tempo de GO padrão."""
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        placeholder="⏱️ Selecione o tempo de GO",
        options=[discord.SelectOption(label=f"{i} minuto(s)", value=str(i)) for i in range(1, 11)],
    )
    async def select_go(self, inter, sel):
        minutos = int(sel.values[0])
        uid = str(inter.user.id)
        await asyncio.to_thread(go_config_set, uid, minutos)
        em = _ok(
            f"GO configurado: {minutos} min",
            f"{SETTINGS} Todas as suas salas agora iniciam automaticamente em **{minutos} minuto(s)**.\n\n"
            f"{DOT} Isso vale para `/c1`, `/c2`, `/c3` e os botões do painel.",
        )
        await inter.response.edit_message(embed=em, view=VoltarCarteiraView(inter.user))


class VoltarCarteiraView(discord.ui.View):
    """Botão para voltar à carteira após configurar o GO."""
    def __init__(self, user):
        super().__init__(timeout=120)
        self.user = user

    @discord.ui.button(label="Ver Carteira", emoji=PE["carteira"], style=discord.ButtonStyle.primary)
    async def btn_voltar(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        d = await asyncio.to_thread(perfil_usuario, str(self.user.id))
        go_atual = await asyncio.to_thread(go_config_get, str(self.user.id))
        go_label = f"{go_atual} minuto(s)" if go_atual > 0 else f"{config.DEFAULT_INICIAR_MINUTOS} minuto(s) (padrão)"
        from utils.pix import get_preco_por_sala_guild
        _gid = str(inter.guild.id) if inter.guild else None
        preco = get_preco_por_sala_guild(_gid)

        em = discord.Embed(
            title=f"{INFO}  Carteira de\n{self.user.mention}",
            description="Dados da carteira do usuário.",
            color=0x5865F2,
        )
        em.set_thumbnail(url=self.user.display_avatar.url)
        em.add_field(name="**Salas Disponíveis**", value=f"Total de salas que o usuário possui.\n\n> **{d['saldo']} salas**", inline=False)
        gastas = (
            f"Total de salas criadas pelo usuário.\n\n"
            f"> Total de **{d['total']}** salas gastas\n\n"
            f"> {CART}  Hoje: **{d['hoje']}** salas\n\n"
            f"> {CART}  Ontem: **{d['ontem']}** salas\n\n"
            f"> {CART}  3 dias: **{d['3dias']}** salas\n\n"
            f"> {CART}  Semana: **{d['semana']}** salas\n\n"
            f"> {CART}  Mês: **{d['mes']}** salas"
        )
        em.add_field(name="**Salas Gastas**", value=gastas, inline=False)
        em.add_field(name="**Comprar Salas**", value=f"Realize a compra de mais salas automaticamente.\n*(R$ {preco:.2f} por sala — mínimo 15 salas)*", inline=False)
        em.add_field(name="**Comandos Rápidos**", value=f"`/c1` {e('jogadores')} Normal  •  `/c2` {PLAY} Infinito  •  `/c3` {TOP} Full Capa", inline=False)
        em.add_field(name="**Tempo de GO**", value=f"Início automático configurado para **{go_label}**.\nClique em **Config GO** para alterar.", inline=False)
        await inter.edit_original_response(embed=em, view=CarteiraView())


class LucroSlashView(discord.ui.View):
    """View do /lucro com botão alterar preço."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Alterar Preço", emoji=PE["emoji70"], style=discord.ButtonStyle.success, row=0)
    async def btn_alterar(self, inter, btn):
        await inter.response.send_modal(AlterarPrecoLucroModal())


class AlterarPrecoLucroModal(discord.ui.Modal, title="💰 Alterar Preço por Sala"):
    preco = discord.ui.TextInput(
        label="Quanto você ganha por sala (R$)",
        placeholder="Ex: 0.03",
        min_length=1, max_length=10,
    )

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        try:
            v = float(self.preco.value.strip().replace(",", "."))
            if v < 0: raise ValueError
        except ValueError:
            return await inter.followup.send(embed=_err("Valor inválido.", f"{DOT} Use ponto ou vírgula. Ex: `0.03`"), ephemeral=True)
        from utils.database import lucro_config_set_valor
        uid = str(inter.user.id)
        await asyncio.to_thread(lucro_config_set_valor, uid, v)
        em = _ok(f"Preço Atualizado!", f"{MONEY} Novo valor: **R$ {v:.4f}** por sala.")
        await inter.followup.send(embed=em, ephemeral=True)


class ConfigSenhaModal(discord.ui.Modal, title="🔒 Senha das Salas"):
    senha = discord.ui.TextInput(
        label="Senha (1 a 99) — deixe em branco p/ remover",
        placeholder="Ex: 42",
        required=False,
        min_length=0,
        max_length=2,
    )

    def __init__(self, atual: str = ""):
        super().__init__()
        if atual:
            self.senha.default = atual

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        from utils.database import senha_config_set
        s = (self.senha.value or "").strip()

        # Vazio = remover
        if not s:
            uid = str(inter.user.id)
            await asyncio.to_thread(senha_config_set, uid, "")
            em = _ok("Senha Removida",
                     f"{DOT} Suas próximas salas serão criadas **sem senha** (públicas).")
            return await inter.followup.send(embed=em, ephemeral=True)

        # Validar: só números, 1 ou 2 dígitos (aceita 07, 08, 99 etc)
        if not s.isdigit():
            em = _err("Senha Inválida",
                      f"{DOT} A senha deve ser apenas **números** (sem letras ou símbolos).")
            return await inter.followup.send(embed=em, ephemeral=True)
        if len(s) > 2:
            em = _err("Senha Muito Longa",
                      f"{DOT} A senha deve ter no máximo **2 dígitos**.")
            return await inter.followup.send(embed=em, ephemeral=True)
        if int(s) == 0:
            em = _err("Senha Inválida",
                      f"{DOT} A senha não pode ser **0** ou **00**.")
            return await inter.followup.send(embed=em, ephemeral=True)

        # Salva como digitou (07 fica 07)
        uid = str(inter.user.id)
        await asyncio.to_thread(senha_config_set, uid, s)
        em = _ok("Senha Atualizada!",
                 f"{SETTINGS} Suas próximas salas serão criadas com a senha: **`{s}`**\n"
                 f"-# Vale para as duas APIs (1 e 2).")
        await inter.followup.send(embed=em, ephemeral=True)


class LucroSubView(discord.ui.View):
    """Sub-botões que aparecem dentro da tela de lucro."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Alterar Preço", emoji=PE["emoji70"], style=discord.ButtonStyle.success, row=0)
    async def btn_alterar_preco(self, inter, btn):
        await inter.response.send_modal(AlterarPrecoLucroModal())

    @discord.ui.button(label="Config Lucro", emoji=PE["settings"], style=discord.ButtonStyle.secondary, row=0)
    async def btn_config(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import lucro_config_get
        uid = str(inter.user.id)
        cfg = await asyncio.to_thread(lucro_config_get, uid)
        vps = cfg["valor_por_sala"]

        em = discord.Embed(
            title=f"{SETTINGS}  Config Lucro",
            color=0x5865F2,
        )
        if vps > 0:
            em.description = (
                f"{ON} Lucro configurado!\n\n"
                f"{MONEY} Valor atual: **R$ {vps:.2f}** por sala\n\n"
                f"{DOT} Clique em **Alterar Valor** para mudar"
            )
        else:
            em.description = (
                f"{OFF} Lucro não configurado.\n\n"
                f"{DOT} Clique em **Alterar Valor** para definir\n"
                f"quanto você ganha por sala criada."
            )
        await inter.followup.send(embed=em, view=ConfigLucroView(), ephemeral=True)

    @discord.ui.button(label="+ Org", emoji=PE["top"], style=discord.ButtonStyle.primary, row=0)
    async def btn_orgs(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import lucro_config_get
        uid = str(inter.user.id)
        cfg = await asyncio.to_thread(lucro_config_get, uid)
        orgs = cfg.get("orgs", [])

        em = discord.Embed(
            title=f"{TOP}  Suas Orgs",
            color=0x5865F2,
        )

        if not orgs:
            em.description = (
                f"{OFF} Nenhuma org cadastrada.\n\n"
                f"{DOT} Clique em **Adicionar Org** se você trabalha em mais de uma org.\n"
                f"{DOT} Configure o nome, ID do servidor e quanto ganha por sala."
            )
        else:
            for idx, org in enumerate(orgs):
                em.add_field(
                    name=f"{TOP}  {org['nome']}",
                    value=(
                        f"{DOT} Servidor: `{org['guild_id']}`\n"
                        f"{MONEY} R$ {org['valor']:.2f} por sala"
                    ),
                    inline=False,
                )

        await inter.followup.send(embed=em, view=OrgMenuView(), ephemeral=True)

    @discord.ui.button(label="Remover Org", emoji=PE["off"], style=discord.ButtonStyle.danger, row=0)
    async def btn_remove_org(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from utils.database import lucro_config_get
        uid = str(inter.user.id)
        cfg = await asyncio.to_thread(lucro_config_get, uid)
        orgs = cfg.get("orgs", [])

        if not orgs:
            return await inter.followup.send(embed=_err("Sem orgs", f"{OFF} Você não tem nenhuma org cadastrada."), ephemeral=True)

        em = _warn("Remover Org", f"{DOT} Selecione abaixo a org que deseja remover:")
        await inter.followup.send(embed=em, view=RemoverOrgSelectView(orgs), ephemeral=True)


class ConfigLucroView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)

    @discord.ui.button(label="Alterar Valor", emoji=PE["settings"], style=discord.ButtonStyle.primary, row=0)
    async def alterar(self, inter, btn):
        await inter.response.send_modal(ConfigLucroModal())


class OrgMenuView(discord.ui.View):
    def __init__(self): super().__init__(timeout=300)

    @discord.ui.button(label="Adicionar Org", emoji=PE["top"], style=discord.ButtonStyle.success, row=0)
    async def add(self, inter, btn):
        await inter.response.send_modal(AddOrgModal())


class RemoverOrgSelectView(discord.ui.View):
    """Select para escolher qual org remover."""
    def __init__(self, orgs):
        super().__init__(timeout=120)
        options = []
        for idx, org in enumerate(orgs[:25]):
            options.append(discord.SelectOption(
                label=org["nome"][:100],
                description=f"R$ {org['valor']:.2f}/sala • {org['guild_id']}",
                value=str(idx),
            ))
        self.select_org.options = options

    @discord.ui.select(placeholder="Selecione a org para remover")
    async def select_org(self, inter, sel):
        idx = int(sel.values[0])
        from utils.database import lucro_config_get, lucro_config_remove_org
        uid = str(inter.user.id)
        cfg = await asyncio.to_thread(lucro_config_get, uid)
        orgs = cfg.get("orgs", [])
        if idx >= len(orgs):
            return await inter.response.edit_message(embed=_err("Org não encontrada."), view=None)
        nome = orgs[idx]["nome"]
        await asyncio.to_thread(lucro_config_remove_org, uid, idx)
        em = _ok(f"Org \"{nome}\" Removida!", f"{OFF} A org **{nome}** foi removida do seu lucro.")
        await inter.response.edit_message(embed=em, view=None)


class ConfigLucroModal(discord.ui.Modal, title="💰 Config Lucro"):
    valor = discord.ui.TextInput(
        label="Quanto você ganha por sala (centavos)",
        placeholder="Ex: 40 (= R$ 0,40 por sala)",
        min_length=1, max_length=10,
    )

    async def on_submit(self, inter):
        from utils.database import lucro_config_set_valor
        try:
            centavos = float(self.valor.value.strip().replace(",", "."))
            # Se colocou em centavos (ex: 40), converte pra reais
            if centavos >= 1:
                reais = centavos / 100
            else:
                reais = centavos
        except ValueError:
            return await inter.response.send_message(embed=_err("Valor inválido! Use números."), ephemeral=True)

        await asyncio.to_thread(lucro_config_set_valor, str(inter.user.id), reais)
        em = _ok("Lucro Configurado!")
        em.description = f"{MONEY} Você ganha **R$ {reais:.2f}** por sala criada.\n\nUse **Seu Lucro** no `/c` para ver suas estatísticas."
        await inter.response.send_message(embed=em, ephemeral=True)


class AddOrgModal(discord.ui.Modal, title="🏢 Adicionar Org"):
    nome = discord.ui.TextInput(label="Nome da Org", placeholder="Ex: Santa, Neymar", max_length=50)
    guild = discord.ui.TextInput(label="ID do Servidor", placeholder="Ex: 1394392595739971644", max_length=25)
    valor = discord.ui.TextInput(label="Centavos por sala nessa org", placeholder="Ex: 40 (= R$ 0,40)", max_length=10)

    async def on_submit(self, inter):
        from utils.database import lucro_config_add_org
        try:
            centavos = float(self.valor.value.strip().replace(",", "."))
            reais = centavos / 100 if centavos >= 1 else centavos
        except ValueError:
            return await inter.response.send_message(embed=_err("Valor inválido!"), ephemeral=True)

        await asyncio.to_thread(
            lucro_config_add_org,
            str(inter.user.id),
            self.nome.value.strip(),
            self.guild.value.strip(),
            reais,
        )
        em = _ok(f"Org \"{self.nome.value.strip()}\" Adicionada!")
        em.description = (
            f"{TOP} **{self.nome.value.strip()}**\n"
            f"{DOT} Servidor: `{self.guild.value.strip()}`\n"
            f"{MONEY} R$ {reais:.2f} por sala\n\n"
            f"Use **Seu Lucro** no `/c` para ver as estatísticas."
        )
        await inter.response.send_message(embed=em, ephemeral=True)


# ═══════════════════════════════════════════
#  Views do /logs — canais públicos
# ═══════════════════════════════════════════

class LogsPubView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Setar Compras", emoji=PE["carteira"], style=discord.ButtonStyle.success, row=0)
    async def btn_compras(self, inter, btn):
        em = _info("Selecione o Canal de Compras", f"{CART} Escolha abaixo o canal onde serão enviados os logs de compras PIX:")
        await inter.response.send_message(embed=em, view=LogsComprasChannelView(), ephemeral=True)

    @discord.ui.button(label="Setar Bônus", emoji=PE["presente"], style=discord.ButtonStyle.primary, row=0)
    async def btn_bonus(self, inter, btn):
        em = _info("Selecione o Canal de Bônus", f"{GIFT} Escolha abaixo o canal onde serão enviados os logs de bônus resgatados:")
        await inter.response.send_message(embed=em, view=LogsBonusChannelView(), ephemeral=True)

    @discord.ui.button(label="Setar Avaliações", emoji="⭐", style=discord.ButtonStyle.secondary, row=1)
    async def btn_avaliacao(self, inter, btn):
        em = _info("Selecione o Canal de Avaliações", "⭐ Escolha abaixo o canal onde aparecerão as avaliações dos clientes após o pagamento:")
        await inter.response.send_message(embed=em, view=LogsAvaliacaoChannelView(), ephemeral=True)

    @discord.ui.button(label="Setar Sugestões", emoji="💡", style=discord.ButtonStyle.secondary, row=1)
    async def btn_sugestao(self, inter, btn):
        em = _info("Selecione o Canal de Sugestões", "💡 Escolha abaixo o canal onde aparecerão as sugestões enviadas pelos clientes:")
        await inter.response.send_message(embed=em, view=LogsSugestaoChannelView(), ephemeral=True)

    @discord.ui.button(label="Setar Log Salas Teste", emoji="🎁", style=discord.ButtonStyle.secondary, row=2)
    async def btn_gratis(self, inter, btn):
        em = _info("Selecione o Canal de Log — Salas Teste", "🎁 Escolha abaixo o canal onde aparecerão os logs de quem resgatou as salas grátis:")
        await inter.response.send_message(embed=em, view=LogsGratisChannelView(), ephemeral=True)


class LogsComprasChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de compras",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1,
    )
    async def channel_select(self, inter, sel):
        canal = sel.values[0]
        from cogs.botconfig import carregar_cfg, salvar_cfg
        cfg = carregar_cfg()
        cfg["canal_compras_pub_id"] = canal.id
        salvar_cfg(cfg)
        em = _ok("Canal de Compras Definido!")
        em.description = (
            f"{CART} Logs de compras serão enviados em {canal.mention}\n\n"
            f"{DOT} Quando alguém comprar salas via PIX, o embed será postado lá."
        )
        await inter.response.edit_message(embed=em, view=None)


class LogsBonusChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de bônus",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1,
    )
    async def channel_select(self, inter, sel):
        canal = sel.values[0]
        from cogs.botconfig import carregar_cfg, salvar_cfg
        cfg = carregar_cfg()
        cfg["canal_bonus_pub_id"] = canal.id
        salvar_cfg(cfg)
        em = _ok("Canal de Bônus Definido!")
        em.description = (
            f"{GIFT} Logs de bônus serão enviados em {canal.mention}\n\n"
            f"{DOT} Quando alguém resgatar bônus, o embed será postado lá."
        )
        await inter.response.edit_message(embed=em, view=None)


class LogsAvaliacaoChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de avaliações",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1,
    )
    async def channel_select(self, inter, sel):
        canal = sel.values[0]
        from cogs.botconfig import carregar_cfg, salvar_cfg
        cfg = carregar_cfg()
        cfg["canal_avaliacao_pub_id"] = canal.id
        salvar_cfg(cfg)
        em = _ok("Canal de Avaliações Definido!")
        em.description = (
            f"⭐ Avaliações dos clientes serão enviadas em {canal.mention}\n\n"
            f"{DOT} Após cada pagamento aprovado, o cliente recebe no DM um botão para avaliar.\n"
            f"{DOT} A avaliação (1-5 estrelas + comentário) aparecerá neste canal."
        )
        await inter.response.edit_message(embed=em, view=None)


class LogsSugestaoChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de sugestões",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1,
    )
    async def channel_select(self, inter, sel):
        canal = sel.values[0]
        from cogs.botconfig import carregar_cfg, salvar_cfg
        cfg = carregar_cfg()
        cfg["canal_sugestao_pub_id"] = canal.id
        salvar_cfg(cfg)
        em = _ok("Canal de Sugestões Definido!")
        em.description = (
            f"💡 Sugestões dos clientes serão enviadas em {canal.mention}\n\n"
            f"{DOT} Após cada pagamento aprovado, o cliente recebe no DM um botão para enviar sugestão.\n"
            f"{DOT} Cada sugestão aparecerá neste canal para análise."
        )
        await inter.response.edit_message(embed=em, view=None)


class LogsGratisChannelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de log — salas teste",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1,
    )
    async def channel_select(self, inter, sel):
        canal = sel.values[0]
        from cogs.botconfig import carregar_cfg, salvar_cfg
        cfg = carregar_cfg()
        cfg["canal_gratis_log_id"] = canal.id
        salvar_cfg(cfg)
        em = _ok("Canal de Log — Salas Teste Definido!")
        em.description = (
            f"🎁 Logs de resgate de salas grátis serão enviados em {canal.mention}\n\n"
            f"{DOT} Toda vez que alguém resgatar o Painel Grátis, o log aparecerá aqui."
        )
        await inter.response.edit_message(embed=em, view=None)



# ═══════════════════════════════════════════
#  +f — Views e Modals
# ═══════════════════════════════════════════

class AddSaldoUsuarioModal(discord.ui.Modal, title="➕ Adicionar Saldo — Usuário"):
    usuario_input = discord.ui.TextInput(
        label="Usuário (nome, @menção ou ID)",
        placeholder="Ex: Armored ou 123456789012345678",
        min_length=1,
        max_length=100,
    )
    quantia_input = discord.ui.TextInput(
        label="Quantidade de salas",
        placeholder="Ex: 50",
        min_length=1,
        max_length=6,
    )

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)

        # Valida quantia
        try:
            qtd = int(self.quantia_input.value.strip())
            if qtd <= 0: raise ValueError
        except ValueError:
            em = discord.Embed(title="❌  Quantidade inválida!", color=config.COR_ERRO)
            em.description = "> Digite um número inteiro positivo."
            return await inter.followup.send(embed=em, ephemeral=True)

        # Resolve o usuário: tenta por ID, depois por nome no guild
        raw = self.usuario_input.value.strip().lstrip("<@!").rstrip(">")
        member = None

        # Tenta por ID
        if raw.isdigit():
            member = inter.guild.get_member(int(raw))
            if not member:
                try:
                    member = await inter.guild.fetch_member(int(raw))
                except Exception:
                    pass

        # Tenta por nome (display_name ou username)
        if not member:
            raw_lower = raw.lower()
            for m in inter.guild.members:
                if raw_lower in m.display_name.lower() or raw_lower in m.name.lower():
                    member = m
                    break

        if not member:
            em = discord.Embed(title="❌  Usuário não encontrado!", color=config.COR_ERRO)
            em.description = (
                f"> Não achei **{self.usuario_input.value}** neste servidor.\n"
                f"> Tente usar o **ID** numérico do usuário."
            )
            return await inter.followup.send(embed=em, ephemeral=True)

        # Adiciona o saldo e faz flush imediato no MongoDB
        from utils.database import adicionar_saldo_usuario, flush_all
        code = await asyncio.to_thread(adicionar_saldo_usuario, str(member.id), member.display_name, qtd)
        await asyncio.to_thread(flush_all)  # persiste imediatamente

        em = discord.Embed(title=f"{ON}  Saldo Adicionado!", color=config.COR_SUCESSO)
        em.add_field(name=f"{INFO}  Usuário",    value=f"> {member.mention} (`{member.id}`)", inline=False)
        em.add_field(name=f"{CART}  Salas",      value=f"> **{qtd}** salas adicionadas",      inline=True)
        em.add_field(name=f"{GIFT}  Key",         value=f"> `{code}`",                         inline=True)
        em.set_thumbnail(url=member.display_avatar.url)
        await inter.followup.send(embed=em, ephemeral=True)

        asyncio.create_task(_logs.log_voltasaldo(inter.user, member, qtd, code))

        # DM pro usuário
        _uid, _nome, _bot, _qtd = member.id, member.display_name, inter.client, qtd
        async def _dm():
            import os
            try:
                user = await _bot.fetch_user(_uid)
                em_dm = discord.Embed(title=f"{ON}  Salas Adicionadas!", color=0x00FF7F)
                em_dm.description = (
                    f"Olá **{_nome}**! Suas salas foram adicionadas.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                em_dm.add_field(name=f"{CART}  Saldo Adicionado", value=f"> **{_qtd} sala(s)** já estão no seu saldo!", inline=False)
                em_dm.add_field(name=f"{STATS}  Como Usar", value=f"> `/c1` Normal\n> `/c2` Infinito\n> `/c3` Full Capa\n> `/c` Carteira", inline=False)
                from cogs.comprar import AvaliacaoSugestaoView
                await user.send(embed=em_dm, view=AvaliacaoSugestaoView())
            except discord.Forbidden:
                pass
            except Exception as ex:
                import logging as _lg
                _lg.getLogger("salasff").warning(f"[AddSaldo DM] {_uid}: {ex}")
        asyncio.create_task(_dm())


class PainelFView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        self.guild = guild

    @discord.ui.select(
        placeholder="Selecione uma opção...",
        options=[
            discord.SelectOption(
                label="Saldo Clientes",
                description="Veja clientes com saldo ativo neste servidor",
                emoji="👥",
                value="saldo_clientes",
            ),
            discord.SelectOption(
                label="Adicionar Saldo Usuário",
                description="Adiciona salas na conta de um usuário",
                emoji="➕",
                value="add_saldo_usuario",
            ),
            discord.SelectOption(
                label="Valor da Sala",
                description="Defina o preço por sala deste servidor",
                emoji="💰",
                value="valor_sala",
            ),
            discord.SelectOption(
                label="Alterar Prefixo",
                description="Mude o prefixo dos comandos neste servidor",
                emoji="⚙️",
                value="alterar_prefixo",
            ),
        ],
    )
    async def select_callback(self, inter, sel):
        if inter.user.id not in config.ADMIN_IDS:
            return
        choice = sel.values[0]

        if choice == "saldo_clientes":
            await self._saldo_clientes(inter)
        elif choice == "add_saldo_usuario":
            await inter.response.send_modal(AddSaldoUsuarioModal())
        elif choice == "valor_sala":
            await self._valor_sala(inter)
        elif choice == "alterar_prefixo":
            await self._alterar_prefixo(inter)

    async def _alterar_prefixo(self, inter):
        gid = str(self.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)
        prefixo_atual = cfg.get("prefixo", "+")

        em = discord.Embed(
            title=f"⚙️  Alterar Prefixo — {self.guild.name}",
            color=0x5865F2,
            
        )
        em.description = (
            f"Prefixo atual: **`{prefixo_atual}`**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{DOT} Comandos atuais:\n"
            f"> `{prefixo_atual}c` `{prefixo_atual}c1` `{prefixo_atual}c2` `{prefixo_atual}c3`\n\n"
            f"{SETTINGS} Selecione o novo prefixo abaixo."
        )
        if self.guild.icon:
            em.set_thumbnail(url=self.guild.icon.url)
        await inter.response.send_message(embed=em, view=PainelFPrefixoView(gid, self.guild), ephemeral=True)

    async def _saldo_clientes(self, inter):
        await inter.response.defer(ephemeral=True)
        from utils.database import clientes_por_guild
        gid = str(self.guild.id)
        clientes = await asyncio.to_thread(clientes_por_guild, gid)

        total_clientes = len(clientes)
        total_salas = sum(c["salas_usadas"] for c in clientes.values())

        top = sorted(clientes.items(), key=lambda x: x[1]["salas_usadas"], reverse=True)[:15]
        lista = ""
        for idx, (uid, info) in enumerate(top, 1):
            lista += f"> **{idx}.** {info['nome']} — **{info['salas_usadas']}** salas usadas\n"
        if not lista:
            lista = "> Nenhum cliente usou saldo deste servidor ainda."

        em = discord.Embed(
            title=f"👥  Clientes do Servidor",
            color=0x5865F2,
            
        )
        em.description = (
            f"Clientes que criaram salas pelo saldo deste servidor.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{DOT} Clientes: **{total_clientes}**\n"
            f"{CART} Total de salas criadas: **{total_salas}**"
        )
        em.add_field(
            name=f"{STATS}  Ranking",
            value=lista,
            inline=False,
        )
        if self.guild.icon:
            em.set_thumbnail(url=self.guild.icon.url)
        await inter.followup.send(embed=em, ephemeral=True)

    async def _valor_sala(self, inter):
        gid = str(self.guild.id)
        from utils.pix import get_preco_por_sala_guild, get_preco_por_sala
        preco_srv = get_preco_por_sala_guild(gid)
        preco_global = get_preco_por_sala()

        cfg = await asyncio.to_thread(guild_config_get, gid)
        tem_preco_custom = cfg.get("preco_sala") is not None

        em = discord.Embed(
            title=f"💰  Valor da Sala — {self.guild.name}",
            color=0x5865F2,
            
        )
        if tem_preco_custom:
            em.description = (
                f"Este servidor tem preço **personalizado**.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{MONEY} Preço atual: **R$ {preco_srv:.2f}**/sala\n"
                f"{DOT} Preço global: R$ {preco_global:.2f}/sala"
            )
        else:
            em.description = (
                f"Este servidor usa o preço **global**.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{MONEY} Preço atual: **R$ {preco_srv:.2f}**/sala"
            )
        if self.guild.icon:
            em.set_thumbnail(url=self.guild.icon.url)
        await inter.response.send_message(embed=em, view=PainelFPrecoView(gid), ephemeral=True)


class PainelFPrecoModal(discord.ui.Modal, title="Alterar Valor da Sala"):
    centavos = discord.ui.TextInput(
        label="Valor em centavos por sala",
        placeholder="Ex: 9  (= R$ 0,09)   15  (= R$ 0,15)",
        min_length=1,
        max_length=6,
    )

    def __init__(self, guild_id):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, inter):
        from utils.database import guild_config_set
        try:
            cts = int(self.centavos.value.strip())
            if cts <= 0:
                raise ValueError
            novo = round(cts / 100, 2)
        except ValueError:
            return await inter.response.send_message(
                embed=_err("Valor inválido", f"{DOT} Digite só números inteiros. Ex: `9` = R$ 0,09"),
                ephemeral=True,
            )

        try:
            await inter.response.defer(ephemeral=True)
            await asyncio.to_thread(guild_config_set, self.guild_id, {"preco_sala": novo})

            guild_obj = inter.client.get_guild(int(self.guild_id)) if str(self.guild_id).isdigit() else None
            nome = guild_obj.name if guild_obj else str(self.guild_id)

            em = _emb(f"{ON}  Preço Atualizado!", config.COR_SUCESSO)
            em.description = (
                f"Servidor: **{nome}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{MONEY} Novo preço: **R$ {novo:.2f}**/sala\n"
                f"{DOT} 100 salas = R$ {novo * 100:.2f}\n"
                f"{DOT} 300 salas = R$ {novo * 300:.2f}"
            )
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as _ex:
            _log.error(f"[PainelFPrecoModal] {_ex}", exc_info=True)
            try:
                await inter.followup.send(embed=_err("Erro ao salvar", f"`{_ex}`"), ephemeral=True)
            except Exception:
                pass


class PainelFPrecoView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=120)
        self.guild_id = guild_id

    @discord.ui.button(label="Alterar Preço", emoji="💰", style=discord.ButtonStyle.primary, row=0)
    async def btn_alterar(self, inter, btn):
        if inter.user.id not in config.ADMIN_IDS:
            return
        await inter.response.send_modal(PainelFPrecoModal(self.guild_id))


class PainelFPrefixoView(discord.ui.View):
    def __init__(self, guild_id, guild):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.guild = guild

    @discord.ui.select(
        placeholder="Selecione o novo prefixo...",
        options=[
            discord.SelectOption(label="+", description="Comandos: +c, +c1, +c2, +c3", value="+"),
            discord.SelectOption(label="!", description="Comandos: !c, !c1, !c2, !c3", value="!"),
            discord.SelectOption(label=".", description="Comandos: .c, .c1, .c2, .c3", value="."),
            discord.SelectOption(label="-", description="Comandos: -c, -c1, -c2, -c3", value="-"),
        ],
        row=0,
    )
    async def select_prefixo(self, inter, sel):
        if inter.user.id not in config.ADMIN_IDS:
            return
        novo = sel.values[0]
        from utils.database import guild_config_set
        await asyncio.to_thread(guild_config_set, self.guild_id, {"prefixo": novo})

        nome = self.guild.name if self.guild else self.guild_id

        em = _emb(f"{ON}  Prefixo Alterado!", config.COR_SUCESSO)
        em.description = (
            f"Servidor: **{nome}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{SETTINGS} Novo prefixo: **`{novo}`**\n\n"
            f"{CART} Comandos atualizados:\n"
            f"> `{novo}c` — Carteira\n"
            f"> `{novo}c1` — Criar sala Normal\n"
            f"> `{novo}c2` — Criar sala Infinito\n"
            f"> `{novo}c3` — Criar sala Full Capa"
        )
        await inter.response.edit_message(embed=em, view=None)


# ═══════════════════════════════════════════
#  InfConfig — Select + Modal DM
# ═══════════════════════════════════════════

class DmModal(discord.ui.Modal, title="Enviar DM para Clientes"):
    saldo_minimo = discord.ui.TextInput(
        label="Saldo mínimo de salas",
        placeholder="Ex: 1",
        required=True,
        max_length=6,
    )
    mensagem = discord.ui.TextInput(
        label="Mensagem",
        style=discord.TextStyle.paragraph,
        placeholder="Digite a mensagem que será enviada na DM...",
        required=True,
        max_length=1800,
    )

    async def on_submit(self, i: discord.Interaction):
        try:
            saldo_min = int(self.saldo_minimo.value.strip())
        except ValueError:
            return await i.response.send_message(embed=_err("Saldo inválido.", "Digite um número inteiro."), ephemeral=True)

        await i.response.defer(ephemeral=True)

        all_keys = await asyncio.to_thread(todas_keys_com_saldo)
        clientes = {}
        for k in all_keys:
            uid = k["dono_id"]
            if uid not in clientes:
                clientes[uid] = {"nome": k.get("dono_nome") or uid, "saldo": 0}
            clientes[uid]["saldo"] += k["quantia"] - k["salas_usadas"]

        alvos = {uid: info for uid, info in clientes.items() if info["saldo"] >= saldo_min}
        if not alvos:
            return await i.followup.send(
                embed=_err("Nenhum cliente", f"{DOT} Nenhum cliente com **{saldo_min}+** salas de saldo."),
                ephemeral=True,
            )

        top = sorted(alvos.items(), key=lambda x: x[1]["saldo"], reverse=True)[:10]
        lista = ""
        for idx, (uid, info) in enumerate(top, 1):
            lista += f"> **{idx}.** {info['nome']} — **{info['saldo']}** salas\n"
        if len(alvos) > 10:
            lista += f"> ... e mais **{len(alvos) - 10}** clientes\n"

        em = _emb(f"{INFO}  Confirmar Envio de DM", 0xFFA500)
        em.description = (
            f"Enviar mensagem para **{len(alvos)}** clientes com **{saldo_min}+** salas.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{CART} **Mensagem:**\n> {self.mensagem.value}\n"
        )
        em.add_field(name="👥  Destinatários", value=lista, inline=False)
        await i.followup.send(
            embed=em,
            view=DmConfirmView(alvos, self.mensagem.value, saldo_min, i.user.id),
            ephemeral=True,
        )


def _build_users_token_emb(bot, users: list, page: int = 0, per_page: int = 15) -> discord.Embed:
    total = len(users)
    ativos = sum(1 for u in users if u["ativo"])
    inativos = total - ativos

    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    start = page * per_page
    chunk = users[start:start + per_page]

    em = discord.Embed(
        title="🔑  Usuários com Token Mode",
        color=0x5865F2,
    )
    em.description = (
        f"Total: **{total}** usuários\n"
        f"✅ Ativos: **{ativos}**  •  ⏸️ Inativos: **{inativos}**\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )

    linhas = []
    for idx, u in enumerate(chunk, start=start + 1):
        uid = u["user_id"]
        try:
            user_obj = bot.get_user(int(uid))
        except Exception:
            user_obj = None
        nome = user_obj.name if user_obj else f"`{uid}`"
        status = "✅" if u["ativo"] else "⏸️"
        linhas.append(f"{idx}. {status} <@{uid}>  •  `{nome}`")

    if linhas:
        em.add_field(name="\u200b", value="\n".join(linhas), inline=False)

    em.set_footer(text=f"Página {page + 1}/{pages}  •  ✅ Ativo  ⏸️ Token salvo mas desativado")
    return em


class UsersTokenPagView(discord.ui.View):
    def __init__(self, bot, users: list):
        super().__init__(timeout=300)
        self.bot = bot
        self.users = users
        self.page = 0
        self.per_page = 15
        self.pages = max(1, (len(users) + self.per_page - 1) // self.per_page)
        self._update_buttons()

    def _update_buttons(self):
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.pages - 1

    @discord.ui.button(label="Anterior", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, inter: discord.Interaction, btn):
        self.page = max(0, self.page - 1)
        self._update_buttons()
        em = _build_users_token_emb(self.bot, self.users, self.page, self.per_page)
        await inter.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="Próxima", emoji="▶️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_next(self, inter: discord.Interaction, btn):
        self.page = min(self.pages - 1, self.page + 1)
        self._update_buttons()
        em = _build_users_token_emb(self.bot, self.users, self.page, self.per_page)
        await inter.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="Atualizar", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
    async def btn_refresh(self, inter: discord.Interaction, btn):
        from utils.database import token_mode_listar_todos
        await inter.response.defer()
        users = await asyncio.to_thread(token_mode_listar_todos)
        users.sort(key=lambda u: (not u["ativo"], u["user_id"]))
        self.users = users
        self.pages = max(1, (len(users) + self.per_page - 1) // self.per_page)
        self.page = min(self.page, self.pages - 1)
        self._update_buttons()
        em = _build_users_token_emb(self.bot, self.users, self.page, self.per_page)
        try:
            await inter.edit_original_response(embed=em, view=self)
        except Exception:
            await inter.followup.send(embed=em, view=self, ephemeral=True)


async def _build_lucro_painel(user_id: int):
    """Monta embed + view do painel de Lucro (usado em /infconfig → Lucro)."""
    from utils.database import lucro_resumo
    from cogs.botconfig import carregar_cfg

    uid = str(user_id)
    resumo = await asyncio.to_thread(lucro_resumo, uid)
    cfg_global = await asyncio.to_thread(carregar_cfg)
    preco_venda = float(cfg_global.get("preco_por_sala", 0.09) or 0.09)
    valor_compra = resumo["valor_compra_por_sala"]
    margem_unit = preco_venda - valor_compra
    margem_pct = (margem_unit / preco_venda * 100) if preco_venda > 0 else 0

    em = discord.Embed(
        title=f"💵  Lucro & Finanças",
        color=0x2ECC71,
    )

    # Cabeçalho com config atual
    em.description = (
        f"```ansi\n"
        f"\u001b[1;31m💸 Compro:\u001b[0m  R$ {valor_compra:.3f} / sala\n"
        f"\u001b[1;32m🏷️  Vendo:\u001b[0m   R$ {preco_venda:.3f} / sala\n"
        f"\u001b[1;36m📈 Margem:\u001b[0m  R$ {margem_unit:.3f} ({margem_pct:.1f}%)\n"
        f"```"
    )

    def _bloco(p):
        return (
            f"```\n"
            f"📦 Vendidas:    {p['salas']:>8}\n"
            f"🎁 Bônus dado:  {p['bonus']:>8}\n"
            f"💰 Receita:    R${p['receita']:>9.2f}\n"
            f"💸 Custo:      R${p['custo']:>9.2f}\n"
            f"❌ Perda bônus:R${p['perda_bonus']:>9.2f}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✨ Lucro:      R${p['lucro']:>9.2f}\n"
            f"```"
        )

    em.add_field(name="📅 ・ Hoje",          value=_bloco(resumo["hoje"]),   inline=True)
    em.add_field(name="🕘 ・ Ontem",         value=_bloco(resumo["ontem"]),  inline=True)
    em.add_field(name="\u200b",              value="\u200b",                 inline=True)
    em.add_field(name="📊 ・ Semana (7d)",   value=_bloco(resumo["semana"]), inline=True)
    em.add_field(name="🏆 ・ Total Geral",   value=_bloco(resumo["total"]),  inline=True)
    em.add_field(name="\u200b",              value="\u200b",                 inline=True)

    em.set_footer(text=f"💡 Use os botões abaixo pra ajustar valores  •  {_ts().strftime('%d/%m/%Y %H:%M')}")

    return em, LucroPainelView(user_id)


class LucroPainelView(discord.ui.View):
    """Botões pra editar compra/venda e atualizar."""
    def __init__(self, user_id: int):
        super().__init__(timeout=600)
        self.user_id = int(user_id)

    @discord.ui.button(label="Valor de Compra", emoji="💸", style=discord.ButtonStyle.danger, row=0)
    async def btn_compra(self, inter: discord.Interaction, btn):
        if inter.user.id != self.user_id:
            return await inter.response.send_message("❌ Apenas quem abriu pode usar.", ephemeral=True)
        await inter.response.send_modal(LucroValorCompraModal(self))

    @discord.ui.button(label="Preço de Venda", emoji="🏷️", style=discord.ButtonStyle.success, row=0)
    async def btn_venda(self, inter: discord.Interaction, btn):
        if inter.user.id != self.user_id:
            return await inter.response.send_message("❌ Apenas quem abriu pode usar.", ephemeral=True)
        await inter.response.send_modal(LucroPrecoVendaModal(self))

    @discord.ui.button(label="Atualizar", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
    async def btn_refresh(self, inter: discord.Interaction, btn):
        if inter.user.id != self.user_id:
            return await inter.response.send_message("❌ Apenas quem abriu pode usar.", ephemeral=True)
        await inter.response.defer()
        em, view = await _build_lucro_painel(self.user_id)
        try:
            await inter.edit_original_response(embed=em, view=view)
        except Exception:
            await inter.followup.send(embed=em, view=view, ephemeral=True)


class LucroValorCompraModal(discord.ui.Modal, title="💸 Valor de Compra por Sala"):
    valor = discord.ui.TextInput(
        label="Quanto você paga por sala (R$)",
        placeholder="Ex: 0.030",
        min_length=1, max_length=10,
        required=True,
    )

    def __init__(self, parent_view: "LucroPainelView"):
        super().__init__()
        self.parent_view = parent_view
        try:
            from utils.database import lucro_config_get
            cfg = lucro_config_get(str(parent_view.user_id))
            v = float(cfg.get("valor_por_sala", 0) or 0)
            if v > 0:
                self.valor.default = f"{v:.4f}".rstrip("0").rstrip(".")
        except Exception:
            pass

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer()
        try:
            v = float(self.valor.value.strip().replace(",", "."))
            if v < 0: raise ValueError
        except ValueError:
            return await inter.followup.send(
                embed=_err("Valor inválido", "Use ponto ou vírgula. Ex: `0.030`"),
                ephemeral=True,
            )
        from utils.database import lucro_config_set_valor
        await asyncio.to_thread(lucro_config_set_valor, str(self.parent_view.user_id), v)

        # Refresh do painel
        em, view = await _build_lucro_painel(self.parent_view.user_id)
        try:
            await inter.edit_original_response(embed=em, view=view)
        except Exception:
            await inter.followup.send(embed=em, view=view, ephemeral=True)


class LucroPrecoVendaModal(discord.ui.Modal, title="🏷️ Preço de Venda por Sala"):
    preco = discord.ui.TextInput(
        label="Preço cobrado do cliente (R$)",
        placeholder="Ex: 0.09",
        min_length=1, max_length=10,
        required=True,
    )

    def __init__(self, parent_view: "LucroPainelView"):
        super().__init__()
        self.parent_view = parent_view
        try:
            from cogs.botconfig import carregar_cfg
            cfg = carregar_cfg()
            v = float(cfg.get("preco_por_sala", 0.09) or 0.09)
            self.preco.default = f"{v:.4f}".rstrip("0").rstrip(".")
        except Exception:
            pass

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer()
        try:
            v = float(self.preco.value.strip().replace(",", "."))
            if v <= 0: raise ValueError
        except ValueError:
            return await inter.followup.send(
                embed=_err("Preço inválido", "Use ponto ou vírgula. Ex: `0.09`"),
                ephemeral=True,
            )
        from cogs.botconfig import carregar_cfg, salvar_cfg
        cfg = await asyncio.to_thread(carregar_cfg)
        cfg["preco_por_sala"] = round(v, 4)
        await asyncio.to_thread(salvar_cfg, cfg)

        em, view = await _build_lucro_painel(self.parent_view.user_id)
        try:
            await inter.edit_original_response(embed=em, view=view)
        except Exception:
            await inter.followup.send(embed=em, view=view, ephemeral=True)


class InfConfigSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Saldo Clientes",    description="Todos os clientes com saldo",  emoji="💰", value="saldocliente"),
            discord.SelectOption(label="Estatísticas",      description="Estatísticas globais do bot",   emoji="📊", value="saldototal"),
            discord.SelectOption(label="Lucro",             description="Receita, custo, bônus e lucro líquido", emoji="💵", value="lucro"),
            discord.SelectOption(label="Usuários Token",    description="Quem configurou Token Mode",    emoji="🔑", value="users_token"),
            discord.SelectOption(label="Enviar DM",         description="Enviar DM para clientes",       emoji="✉️", value="dm"),
        ]
        super().__init__(placeholder="Selecione uma opção...", options=options)

    async def callback(self, i: discord.Interaction):
        choice = self.values[0]

        if choice == "saldocliente":
            await i.response.defer(ephemeral=True)
            keys = await asyncio.to_thread(todas_keys_com_saldo)
            if not keys:
                return await i.followup.send(embed=_info("Saldo Clientes", f"{OFF} Nenhum cliente."), ephemeral=True)
            cl = {}
            for k in keys:
                uid = k["dono_id"]; s = k["quantia"] - k["salas_usadas"]
                if uid not in cl: cl[uid] = {"nome": k["dono_nome"] or "?", "saldo": 0, "keys": 0}
                cl[uid]["saldo"] += s; cl[uid]["keys"] += 1
            o = sorted(cl.items(), key=lambda x: x[1]["saldo"], reverse=True)
            t = sum(v["saldo"] for v in cl.values())
            await i.followup.send(embed=_saldo_emb(o, t, 0), view=SaldoPagView(o, t), ephemeral=True)
            asyncio.create_task(_logs.log_saldoclientes(i.user, len(cl), t, o))

        elif choice == "saldototal":
            await i.response.defer(ephemeral=True)
            from utils.database import stats_globais, ultimas_compras, top_compradores, stats_por_guild
            s = await asyncio.to_thread(stats_globais)
            ul = await asyncio.to_thread(ultimas_compras, 5)
            tp = await asyncio.to_thread(top_compradores)
            por_guild = await asyncio.to_thread(stats_por_guild, 10)
            em = _emb(f"{STATS}  Estatísticas Globais", desc=_ts().strftime("%d/%m/%Y %H:%M (Brasília)"))
            em.add_field(name=f"{ON} Salas Criadas", value=f"> Hoje: **{s['salas_hoje']}**\n> Ontem: **{s['salas_ontem']}**\n> 7d: **{s['salas_7d']}**\n> Total: **{s['salas_tot']}**", inline=True)
            em.add_field(name=f"{CART} Vendas PIX",  value=f"> Hoje: **{s['vendas_hoje']}**\n> Ontem: **{s['vendas_ontem']}**\n> 7d: **{s['vendas_7d']}**\n> Total: **{s['vendas_tot']}**", inline=True)
            em.add_field(name=f"{MONEY} Receita",    value=f"> Hoje: **R$ {s['receita_hoje']:.2f}**\n> Ontem: **R$ {s['receita_ontem']:.2f}**\n> 7d: **R$ {s['receita_7d']:.2f}**\n> Total: **R$ {s['receita_tot']:.2f}**", inline=True)
            em.add_field(name=f"{STATS} Qtd Vendas", value=f"> Hoje: **{s['pedidos_hoje']}**\n> Ontem: **{s['pedidos_ontem']}**\n> 7d: **{s['pedidos_7d']}**\n> Total: **{s['pedidos_tot']}**", inline=False)
            if por_guild:
                linhas = []
                for g in por_guild[:10]:
                    linhas.append(
                        f"{DOT} **{g['guild_nome']}** — {g['salas_tot']} salas • "
                        f"R$ {g['receita_tot']:.2f} • {g['pedidos_tot']} pedidos"
                    )
                em.add_field(name=f"📍 Vendas por Servidor", value="\n".join(linhas), inline=False)
            if ul: em.add_field(name=f"{STATS} Últimas Compras", value="\n".join(f"{DOT} **{r['user_nome']}** — {r['quantia']} salas — R$ {float(r['valor']):.2f}" + (f" • _{r.get('guild_nome')}_" if r.get('guild_nome') else "") for r in ul[:5]), inline=False)
            if tp:
                md = [TOP, "🥈", "🥉", "4️⃣", "5️⃣"]
                em.add_field(name=f"{TOP} Top Compradores", value="\n".join(f"{md[j]} **{r['user_nome']}** — {r['total_salas']} salas" for j, r in enumerate(tp[:5])), inline=False)
            await i.followup.send(embed=em, ephemeral=True)
            asyncio.create_task(_logs.log_saldototal(i.user, s))

        elif choice == "dm":
            await i.response.send_modal(DmModal())

        elif choice == "lucro":
            await i.response.defer(ephemeral=True)
            em, view = await _build_lucro_painel(i.user.id)
            await i.followup.send(embed=em, view=view, ephemeral=True)

        elif choice == "users_token":
            await i.response.defer(ephemeral=True)
            from utils.database import token_mode_listar_todos
            users = await asyncio.to_thread(token_mode_listar_todos)
            if not users:
                em = _info("🔑  Usuários Token", f"{OFF} Nenhum usuário com token configurado.")
                return await i.followup.send(embed=em, ephemeral=True)

            # Ordena: ativos primeiro, depois alfabético
            users.sort(key=lambda u: (not u["ativo"], u["user_id"]))
            em = _build_users_token_emb(i.client, users, page=0)
            await i.followup.send(embed=em, view=UsersTokenPagView(i.client, users), ephemeral=True)


class InfConfigView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(InfConfigSelect())


class DmConfirmView(discord.ui.View):
    def __init__(self, alvos: dict, mensagem: str, saldo_min: int, admin_id: int):
        super().__init__(timeout=120)
        self.alvos = alvos
        self.mensagem = mensagem
        self.saldo_min = saldo_min
        self.admin_id = admin_id

    @discord.ui.button(label="Confirmar Envio", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def btn_confirmar(self, inter, btn):
        if inter.user.id != self.admin_id:
            return
        await inter.response.defer(ephemeral=True)

        # Desabilita botões
        for item in self.children:
            item.disabled = True
        await inter.edit_original_response(view=self)

        total = len(self.alvos)
        enviados = 0
        erros = 0

        # Envia embed de status inicial
        def _status_embed(enviados, erros, total, finalizado=False):
            progresso = int((enviados + erros) / total * 20) if total > 0 else 0
            barra = "█" * progresso + "░" * (20 - progresso)
            pct = int((enviados + erros) / total * 100) if total > 0 else 0

            if finalizado:
                em = discord.Embed(title="✅  Envio Concluído!", color=0x00FF7F)
            else:
                em = discord.Embed(title="📤  Enviando DMs...", color=0xFFD700)

            em.description = (
                f"Mensagem para clientes com **{self.saldo_min}+** salas.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"```{barra} {pct}%```\n"
                f"✅ Enviados: **{enviados}**\n"
                f"❌ Erros: **{erros}**\n"
                f"⏳ Restantes: **{total - enviados - erros}**\n"
                f"📊 Total: **{total}**"
            )
            return em

        status_msg = await inter.followup.send(
            embed=_status_embed(0, 0, total),
            ephemeral=True,
        )

        # Envia DMs com atualização a cada 5 envios
        for uid, info in self.alvos.items():
            try:
                user = inter.client.get_user(int(uid))
                if not user:
                    user = await inter.client.fetch_user(int(uid))
                em = discord.Embed(
                    title="📩  Mensagem do SalasFF Bot",
                    color=0x5865F2,
                )
                em.description = self.mensagem
                await user.send(embed=em)
                enviados += 1
                await asyncio.sleep(1)
            except Exception:
                erros += 1

            # Atualiza status a cada 5 envios
            if (enviados + erros) % 5 == 0 or (enviados + erros) == total:
                try:
                    await status_msg.edit(embed=_status_embed(enviados, erros, total))
                except Exception:
                    pass

        # Status final
        try:
            await status_msg.edit(embed=_status_embed(enviados, erros, total, finalizado=True))
        except Exception:
            pass

    @discord.ui.button(label="Cancelar", emoji="❌", style=discord.ButtonStyle.danger, row=0)
    async def btn_cancelar(self, inter, btn):
        if inter.user.id != self.admin_id:
            return
        for item in self.children:
            item.disabled = True
        em = discord.Embed(title="❌  Envio Cancelado", color=0xFF4444)
        em.description = "Nenhuma DM foi enviada."
        await inter.response.edit_message(embed=em, view=self)


class MainCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    # /c — Carteira
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.command(name="c", description="Veja sua carteira e saldo.")
    async def cmd_c(self, inter):
        if not await _safe_defer(inter, ephemeral=True):
            return
        alvo = inter.user
        uid = str(alvo.id)
        d = await asyncio.to_thread(perfil_usuario, uid)
        from utils.pix import get_preco_por_sala_guild
        _gid = str(inter.guild.id) if inter.guild else None
        preco = get_preco_por_sala_guild(_gid)
        go_atual = await asyncio.to_thread(go_config_get, uid)
        go_label = f"{go_atual} min" if go_atual > 0 else f"{config.DEFAULT_INICIAR_MINUTOS} min (padrão)"

        def _sep(i): return {"id": i, "type": 14, "divider": True, "spacing": 1}
        def _sec(i, txt, btn): return {"id": i, "type": 9, "components": [{"id": i+1, "type": 10, "content": txt}], "accessory": btn}
        def _btn(i, label, cid, style=2, emoji=None, disabled=False):
            b = {"id": i, "type": 2, "style": style, "label": label, "custom_id": cid}
            if emoji: b["emoji"] = emoji
            if disabled: b["disabled"] = True
            return b

        payload = {
            "flags": 64 | 32768,
            "components": [{"id": 1, "type": 17, "components": [
                {"id": 2, "type": 10, "content": f"## {e('store')} Perfil de {alvo.mention}"},
                _sec(3,  f"**Comprar Salas**\nR$ {preco:.2f} por sala — mínimo 30 salas",
                         _btn(5,  "Comprar Salas", "cw:comprar", style=3, emoji=_em("carteira"))),
                _sec(6,  "**Outros**\nVer lucro, config GO, bônus e histórico.",
                         _btn(8,  "Outros",        "cw:outros",  style=1, emoji=_em("settings"))),
                _sep(9),
                _sec(10, "**Salas Disponíveis**\nTotal de salas que o usuário possui.",
                         _btn(12, f"{d['saldo']} salas",               "cw:saldo",  disabled=True, emoji=_em("cloud"))),
                _sec(13, "**Salas Gastas**\nTotal de salas criadas pelo usuário.",
                         _btn(15, f"Total de {d['total']} salas gastas","cw:gastas", disabled=True, emoji=_em("vision"))),
                {"id": 16, "type": 1, "components": [
                    {"id": 17, "type": 2, "style": 2, "label": f"Hoje: {d['hoje']}",    "custom_id": "cw:hoje",   "disabled": True, "emoji": _em("calendario")},
                    {"id": 18, "type": 2, "style": 2, "label": f"Ontem: {d['ontem']}",  "custom_id": "cw:ontem",  "disabled": True, "emoji": _em("calendario")},
                    {"id": 19, "type": 2, "style": 2, "label": f"3 dias: {d['3dias']}", "custom_id": "cw:3dias",  "disabled": True, "emoji": _em("calendario")},
                ]},
                {"id": 20, "type": 1, "components": [
                    {"id": 21, "type": 2, "style": 2, "label": f"Semana: {d['semana']}","custom_id": "cw:semana", "disabled": True, "emoji": _em("calendario")},
                    {"id": 22, "type": 2, "style": 2, "label": f"Mês: {d['mes']}",      "custom_id": "cw:mes",    "disabled": True, "emoji": _em("calendario")},
                ]},
            ]}],
        }

        _fw_url = f"https://discord.com/api/v10/webhooks/{inter.application_id}/{inter.token}?wait=true"
        _v2_ok = False
        try:
            async with _aiohttp_v2.ClientSession() as _s:
                async with _s.post(_fw_url, json=payload) as _r:
                    _v2_ok = _r.status in (200, 201)
                    if not _v2_ok:
                        _log.warning(f"[cw v2] {_r.status} {await _r.text()[:300]}")
        except Exception as _ex:
            _log.warning(f"[cw v2] {_ex}")

        if not _v2_ok:
            try:
                em = discord.Embed(title=f"{INFO}  Carteira de\n{alvo.mention}", color=0x5865F2)
                em.set_thumbnail(url=alvo.display_avatar.url)
                em.add_field(name="**Salas Disponíveis**", value=f"> **{d['saldo']} salas**", inline=False)
                em.add_field(name="**Salas Gastas**", value=f"> Hoje: **{d['hoje']}** · 7d: **{d['semana']}** · Total: **{d['total']}**", inline=False)
                em.add_field(name="**Comprar Salas**", value=f"R$ {preco:.2f}/sala — mín. 30", inline=False)
                await inter.followup.send(embed=em, view=CarteiraView(), ephemeral=True)
            except Exception as _fe:
                _log.error(f"[cw fallback] {_fe}")

    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.command(name="c1", description="Cria sala Normal.")
    async def c1(self, i): await _quick(i, 1)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.command(name="c2", description="Cria sala Infinito.")
    async def c2(self, i): await _quick(i, 2)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.command(name="c3", description="Outros Modos")
    async def c3(self, i):
        em = _emb(f"{TOP}  Selecione o modo")
        em.description = f"{DOT} Escolha o modo da sala:"
        await i.response.send_message(embed=em, view=C3ModoView(), ephemeral=True)

    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    # /usuarioconfig — Perfil + Volta Saldo + Remover Salas
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="usuarioconfig", description="[ADMIN] Gerenciar perfil e saldo de um usuário.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_usuarioconfig(self, i):
        if not is_admin(i.user.id):
            return await i.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        em = _emb(f"{ROLES}  Selecionar Usuário")
        em.description = "Selecione o usuário que deseja gerenciar:"
        await i.response.send_message(embed=em, view=UsuarioSelectView(self.bot), ephemeral=True)


    # prefix +f (painel rápido do servidor — só admin)
    @commands.command(name="f")
    async def prefix_f(self, ctx):
        """Painel rápido do servidor atual (+f). Só ADMIN."""
        if ctx.author.id not in config.ADMIN_IDS:
            return
        if not ctx.guild:
            return await ctx.send(embed=_err("Use em um servidor."))

        # Apaga a mensagem do usuário
        try:
            await ctx.message.delete()
        except Exception:
            pass

        try:
            gid = str(ctx.guild.id)
            cfg = await asyncio.to_thread(guild_config_get, gid)
            prefixo = cfg.get("prefixo", "+")
            saldo_srv = cfg.get("saldo", 0)
            from utils.pix import get_preco_por_sala_guild
            preco = get_preco_por_sala_guild(gid)

            em = discord.Embed(
                title=f"{SETTINGS}  Painel Admin",
                color=0x5865F2,
                
            )
            em.description = (
                f"Gerencie o servidor **{ctx.guild.name}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{DOT} Saldo do servidor: **{saldo_srv}** salas\n"
                f"{MONEY} Valor atual: **R$ {preco:.2f}**/sala\n"
                f"{SETTINGS} Prefixo: `{prefixo}` — `{prefixo}c` `{prefixo}c1` `{prefixo}c2` `{prefixo}c3`"
            )
            if ctx.guild.icon:
                em.set_thumbnail(url=ctx.guild.icon.url)
            await ctx.author.send(embed=em, view=PainelFView(ctx.guild))
        except Exception as exc:
            _log.error(f"[+f] Erro: {exc}", exc_info=exc)
            await ctx.author.send(embed=_err("Erro no painel", f"```{exc}```"))


    # prefix +c (carteira), +c1, +c2, +c3
    @commands.command(name="c")
    async def prefix_c(self, ctx):
        """Mostra a carteira via prefix (+c)."""
        alvo = ctx.author
        d = await asyncio.to_thread(perfil_usuario, str(alvo.id))
        from utils.pix import get_preco_por_sala_guild
        _gid = str(ctx.guild.id) if ctx.guild else None
        preco = get_preco_por_sala_guild(_gid)
        go_atual = await asyncio.to_thread(go_config_get, str(alvo.id))

        p = "+"
        if ctx.guild:
            _gcfg = await asyncio.to_thread(guild_config_get, str(ctx.guild.id))
            p = _gcfg.get("prefixo", "+")

        def _sep(i): return {"id": i, "type": 14, "divider": True, "spacing": 1}
        def _sec(i, txt, btn): return {"id": i, "type": 9, "components": [{"id": i+1, "type": 10, "content": txt}], "accessory": btn}
        def _btn(i, label, cid, style=2, emoji=None, disabled=False):
            b = {"id": i, "type": 2, "style": style, "label": label, "custom_id": cid}
            if emoji: b["emoji"] = emoji
            if disabled: b["disabled"] = True
            return b

        payload = {
            "flags": 32768,
            "components": [{"id": 1, "type": 17, "components": [
                {"id": 2, "type": 10, "content": f"## {e('store')} Carteira de {alvo.mention}"},
                _sec(3,  f"**Comprar Salas**\nR$ {preco:.2f} por sala — mínimo 30 salas",
                         _btn(5,  "Comprar Salas", "cw:comprar", style=3, emoji=_em("carteira"))),
                _sec(6,  "**Outros**\nVer lucro, config GO, bônus e histórico.",
                         _btn(8,  "Outros",        "cw:outros",  style=1, emoji=_em("settings"))),
                _sep(9),
                _sec(10, "**Salas Disponíveis**\nTotal de salas que o usuário possui.",
                         _btn(12, f"{d['saldo']} salas",                "cw:saldo",  disabled=True, emoji=_em("cloud"))),
                _sec(13, "**Salas Gastas**\nTotal de salas criadas pelo usuário.",
                         _btn(15, f"Total de {d['total']} salas gastas", "cw:gastas", disabled=True, emoji=_em("vision"))),
                {"id": 16, "type": 1, "components": [
                    {"id": 17, "type": 2, "style": 2, "label": f"Hoje: {d['hoje']}",    "custom_id": "cw:hoje",   "disabled": True, "emoji": _em("calendario")},
                    {"id": 18, "type": 2, "style": 2, "label": f"Ontem: {d['ontem']}",  "custom_id": "cw:ontem",  "disabled": True, "emoji": _em("calendario")},
                    {"id": 19, "type": 2, "style": 2, "label": f"3 dias: {d['3dias']}", "custom_id": "cw:3dias",  "disabled": True, "emoji": _em("calendario")},
                ]},
                {"id": 20, "type": 1, "components": [
                    {"id": 21, "type": 2, "style": 2, "label": f"Semana: {d['semana']}","custom_id": "cw:semana", "disabled": True, "emoji": _em("calendario")},
                    {"id": 22, "type": 2, "style": 2, "label": f"Mês: {d['mes']}",      "custom_id": "cw:mes",    "disabled": True, "emoji": _em("calendario")},
                ]},
            ]}],
        }

        _ch_url = f"https://discord.com/api/v10/channels/{ctx.channel.id}/messages"
        _headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
        _v2_ok = False
        # V2 só funciona em canais de servidor, não em DMs
        if ctx.guild:
            try:
                async with _aiohttp_v2.ClientSession() as _s:
                    async with _s.post(_ch_url, headers=_headers, json=payload) as _r:
                        _body_txt = await _r.text()
                        _v2_ok = _r.status in (200, 201)
                        if not _v2_ok:
                            _log.warning(f"[prefix_c v2] status={_r.status} body={_body_txt[:400]}")
                        else:
                            _log.info(f"[prefix_c v2] OK guild={ctx.guild.id}")
            except Exception as _ex:
                _log.error(f"[prefix_c v2] exception: {_ex}", exc_info=_ex)

        if not _v2_ok:
            try:
                em = discord.Embed(title=f"{INFO}  Carteira de {alvo.mention}", color=0x5865F2)
                em.set_thumbnail(url=alvo.display_avatar.url)
                em.add_field(name="**Salas Disponíveis**", value=f"> **{d['saldo']} salas**", inline=False)
                em.add_field(name="**Salas Gastas**", value=f"> Hoje: **{d['hoje']}** · 7d: **{d['semana']}** · Total: **{d['total']}**", inline=False)
                em.add_field(name="**Comprar Salas**", value=f"R$ {preco:.2f}/sala — mín. 30", inline=False)
                em.add_field(name="**Comandos Rápidos**", value=f"`{p}c1` Normal  •  `{p}c2` Infinito  •  `{p}c3` Full Capa", inline=False)
                await ctx.send(embed=em, view=CarteiraView())
            except Exception as _ex2:
                _log.error(f"[prefix_c fallback] {_ex2}", exc_info=_ex2)

    async def _pf(self, ctx, m, go):
        uid = str(ctx.author.id)
        if go is None:
            go = await asyncio.to_thread(go_config_get, uid)
            if go <= 0:
                go = config.DEFAULT_INICIAR_MINUTOS

        # tenta saldo do servidor primeiro (mesmo para prefix)
        gid = str(ctx.guild.id) if ctx.guild else None
        guild_pagou = False
        k = None
        if gid:
            cfg = await asyncio.to_thread(guild_config_get, gid)
            cargo_id = cfg.get("cargo_sala_id")
            saldo_guild = cfg.get("saldo", 0)
            cargo_off = cfg.get("cargo_off", False)
            # Só usa saldo do servidor se: tem saldo, cargo não está OFF, e tem cargo configurado
            if saldo_guild > 0 and not cargo_off and cargo_id:
                tem_cargo = any(r.id == int(cargo_id) for r in getattr(ctx.author, "roles", []))
                if tem_cargo:
                    consumido = await asyncio.to_thread(guild_consumir_sala, gid)
                    if consumido:
                        guild_pagou = True

        if not guild_pagou:
            k, restante = await asyncio.to_thread(reservar_sala_key, uid, m, ctx.author.display_name)
            if not k:
                return await ctx.author.send(embed=_err("Sem saldo", "Compre salas usando **Comprar Salas** no `/c`."))

        await _criar_sala_flow(ctx, m if (k and k["modo"] != 0) else m, max(1, min(10, go)),
                               key_row=k, is_prefix=True, key_ja_consumida=True,
                               guild_pagou=guild_pagou, guild_id=gid)
    @commands.command(name="c1")
    async def prefix_c1(self, ctx, go:int=None): await self._pf(ctx,1,go)
    @commands.command(name="c2")
    async def prefix_c2(self, ctx, go:int=None): await self._pf(ctx,2,go)
    @commands.command(name="c3")
    async def prefix_c3(self, ctx, go:int=None):
        em = _emb(f"{TOP}  Selecione o modo")
        em.description = f"{DOT} Escolha o modo da sala:"
        await ctx.send(embed=em, view=C3ModoView())

    @commands.command(name="mp")
    async def prefix_mp(self, ctx):
        """Envia a mensagem promo agora em todos os canais configurados como Promo V2."""
        if not is_admin(ctx.author.id):
            return
        from cogs.botconfig import (
            get_msg_auto, _build_promo_v2_payload, _post_promo_v2, update_canal_cfg,
        )
        ma = await asyncio.to_thread(get_msg_auto)
        canais_promo = [c for c in ma.get("canais", []) if c.get("tipo") == "promo"]
        if not canais_promo:
            return await ctx.send(embed=_err(
                "Nenhum canal Promo V2 configurado.",
                "Vá em `/botconfig` → Msg Automática → Configurar Canal → Promo V2 / Texto.",
            ))
        cog_bc = ctx.bot.get_cog("BotConfigCog")
        enviados = 0
        for c in canais_promo:
            cid = int(c["canal_id"])
            try:
                payload  = await asyncio.to_thread(
                    _build_promo_v2_payload, c.get("mensagem", ""), c.get("mencionar_everyone", True)
                )
                nova_id  = await _post_promo_v2(cid, payload)
                if nova_id:
                    await asyncio.to_thread(update_canal_cfg, cid, ultima_msg_id=nova_id)
                    if cog_bc and c.get("ativo"):
                        cog_bc._restart_canal(cid, skip_first_send=True)
                    enviados += 1
            except Exception as _ex:
                _log.warning(f"[+mp] erro em {cid}: {_ex}")
        if enviados:
            await ctx.message.add_reaction("✅")
        else:
            await ctx.send(embed=_err("Falha ao enviar em todos os canais.", "Veja os logs."))

    @commands.command(name="aa")
    async def prefix_aa(self, ctx, horario: str = None, centavos: str = None):
        """Ativa promo V2 neste canal até HH:MM, a cada 30 min.
        +aa HH:MM       → promo normal
        +aa HH:MM PRECO → mega promo a X centavos
        +aa off         → encerra"""
        if not is_admin(ctx.author.id):
            return

        from cogs.botconfig import (
            carregar_cfg, salvar_cfg,
            get_promo_ativa, _post_promo_v2, _em as _bc_em,
        )
        from utils.database import botconfig_save
        from utils.pix import get_preco_por_sala

        cog_bc = ctx.bot.get_cog("BotConfigCog")

        # +aa off → encerra
        if (horario or "").lower() in ("off", "desligar", "fim", "0"):
            promo = await asyncio.to_thread(get_promo_ativa)
            cfg   = await asyncio.to_thread(carregar_cfg)
            preco_original = (promo or {}).get("preco_original_centavos") if promo else None
            preco_centavos_era = (promo or {}).get("preco_centavos") if promo else None
            cfg.pop("mega_promo", None)
            if preco_centavos_era is not None and preco_original:
                cfg["preco_por_sala"] = round(preco_original / 100, 4)
            await asyncio.to_thread(salvar_cfg, cfg)
            await asyncio.to_thread(botconfig_save, cfg)
            if cog_bc:
                cog_bc._stop_aa_loop()
            try:
                await ctx.message.delete()
            except Exception:
                pass
            txt = f"{DOLLAR} Preço voltou para **{preco_original} centavos**." if preco_centavos_era and preco_original else ""
            try:
                await ctx.author.send(embed=_ok("Promoção encerrada!", txt))
            except Exception:
                pass
            return

        # Valida horário
        if not horario:
            try:
                await ctx.message.delete()
            except Exception:
                pass
            return await ctx.send(embed=_err(
                "Uso correto",
                f"`+aa HH:MM` — promo normal até o horário\n"
                f"`+aa HH:MM CENTAVOS` — mega promo a X centavos\n"
                f"`+aa off` — encerra",
            ), delete_after=15)
        try:
            h, m = [int(x) for x in horario.split(":")]
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError
        except Exception:
            return await ctx.send(embed=_err("Horário inválido.", "Use `HH:MM`, ex: `23:30`"), delete_after=10)

        # Valida centavos (opcional)
        cts = None
        if centavos is not None:
            try:
                cts = int(centavos)
                if cts <= 0 or cts > 9999:
                    raise ValueError
            except Exception:
                return await ctx.send(embed=_err("Centavos inválido.", "Ex: `3` = R$ 0,03/sala"), delete_after=10)

        cfg = await asyncio.to_thread(carregar_cfg)
        preco_atual_cts = round(get_preco_por_sala() * 100)

        if cts is not None:
            cfg["preco_por_sala"] = round(cts / 100, 4)
            cfg["mega_promo"] = {
                "ate_hora": f"{h:02d}:{m:02d}",
                "preco_centavos": cts,
                "preco_original_centavos": preco_atual_cts,
                "canal_id": ctx.channel.id,
            }
        else:
            cfg["mega_promo"] = {
                "ate_hora": f"{h:02d}:{m:02d}",
                "preco_centavos": None,
                "preco_original_centavos": preco_atual_cts,
                "canal_id": ctx.channel.id,
            }

        await asyncio.to_thread(salvar_cfg, cfg)
        await asyncio.to_thread(botconfig_save, cfg)

        # Envia promo V2 agora neste canal (payload inline — evita bug de content vazio)
        ate_hora_str = f"{h:02d}:{m:02d}"
        if cts is not None:
            _inner = [
                {"id": 1, "type": 10, "content": f"## {_bc_em('rage')} MEGA PROMOÇÃO — SALAS A {cts} CENTAVOS!"},
                {"id": 2, "type": 10, "content": (
                    f"{_bc_em('awaiting')} **Promoção válida somente até às {ate_hora_str} BRT!**\n"
                    "-# Após encerrar, o preço volta ao normal. Não perca!"
                )},
                {"id": 3, "type": 14, "divider": True, "spacing": 1},
                {"id": 4, "type": 10, "content": (
                    f"{_bc_em('otherdollar')} **PREÇO ESPECIAL:** R$ {cts/100:.2f}/sala\n"
                    f"{_bc_em('clockcheck')} **Encerra às:** {ate_hora_str} BRT"
                )},
                {"id": 5, "type": 10, "content": "-# @everyone"},
            ]
            _accent = 0xED4245
        else:
            _preco_r = preco_atual_cts / 100
            _inner = [
                {"id": 1, "type": 10, "content": f"## {_bc_em('swordbattle')} COMPRE SALAS AGORA"},
                {"id": 2, "type": 10, "content": (
                    f"{_bc_em('awaiting')} **Promoção válida somente até às {ate_hora_str} BRT!**\n"
                    "-# Não perca!"
                )},
                {"id": 3, "type": 14, "divider": True, "spacing": 1},
                {"id": 4, "type": 10, "content": (
                    f"{_bc_em('otherdollar')} **Preço:** R$ {_preco_r:.2f}/sala\n"
                    f"{_bc_em('clockcheck')} **Encerra às:** {ate_hora_str} BRT"
                )},
                {"id": 5, "type": 10, "content": "-# @everyone"},
            ]
            _accent = 0xFFD700
        _payload_aa = {
            "flags": 32768,
            "components": [{"id": 0, "type": 17, "accent_color": _accent, "components": _inner}],
            "allowed_mentions": {"parse": ["everyone"]},
        }
        try:
            await _post_promo_v2(ctx.channel.id, _payload_aa)
        except Exception as _ex:
            _log.warning(f"[+aa] erro enviando promo inicial: {_ex}")

        # Inicia loop de 30 em 30 min
        if cog_bc:
            cog_bc._start_aa_loop(ctx.channel.id)

        # Remove o comando do canal (sem deixar rastro)
        try:
            await ctx.message.delete()
        except Exception:
            pass

        # Confirmação só via DM pro admin (canal fica limpo com apenas a V2)
        if cts is not None:
            titulo = f"{PRESENTE}  Mega Promoção ATIVADA!"
            desc = f"{DOLLAR} Preço: **{cts} centavos** (R$ {cts/100:.2f}/sala)\n{CLOCK} Encerra às: **{h:02d}:{m:02d} BRT**\n-# Use `+aa off` para encerrar antes."
        else:
            titulo = f"{PRESENTE}  Promoção ATIVADA!"
            desc = f"{CLOCK} Promos neste canal até **{h:02d}:{m:02d} BRT** — a cada 30 min.\n-# Use `+aa off` para encerrar antes."
        em = _emb(titulo, config.COR_SUCESSO)
        em.description = desc
        try:
            await ctx.author.send(embed=em)
        except Exception:
            pass  # DM fechada — sem confirmação

    @commands.command(name="painel")
    async def prefix_painel(self, ctx):

        from cogs.botconfig import carregar_cfg
        from utils.pix import get_preco_por_sala_guild
        gid = str(ctx.guild.id) if ctx.guild else None
        preco = get_preco_por_sala_guild(gid)
        cfg = await asyncio.to_thread(carregar_cfg)
        titulo    = cfg.get("titulo_painel_compra", "F Applications - Compre Aqui")
        sub_titulo = cfg.get("subtitulo_painel_compra", "")
        sub_desc   = cfg.get("subdesc_painel_compra", "")
        sub_rodape = cfg.get("subrodape_painel_compra", "")

        containers = [{
            "id": 1, "type": 17,
            "components": [
                {"id": 2, "type": 10, "content": f"## {titulo}\n\n> **Valor:** R$ {preco:.2f} por sala"},
                {"id": 3, "type": 1, "components": [
                    {"id": 4, "type": 2, "style": 2, "label": "Meu Perfil",    "custom_id": "comprar:perfil"},
                    {"id": 5, "type": 2, "style": 3, "label": "Comprar Salas", "custom_id": "comprar:comprar"},
                ]},
            ],
        }]
        if sub_titulo or sub_desc or sub_rodape:
            sub_parts = []
            if sub_titulo: sub_parts.append(f"## {sub_titulo}")
            if sub_desc:   sub_parts.append(sub_desc)
            if sub_rodape: sub_parts.append(f"-# {sub_rodape}")
            containers.append({"id": 10, "type": 17, "accent_color": 0x2B2D31,
                "components": [{"id": 11, "type": 10, "content": "\n\n".join(sub_parts)}]})

        url = f"https://discord.com/api/v10/channels/{ctx.channel.id}/messages"
        hdrs = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
        ok = False
        try:
            async with _aiohttp_v2.ClientSession() as _s:
                async with _s.post(url, headers=hdrs, json={"flags": 32768, "components": containers}) as r:
                    ok = r.status in (200, 201)
        except Exception as ex:
            _log.error(f"[prefix_painel] {ex}")
        if not ok:
            from cogs.comprar import PainelComprarView
            em = discord.Embed(color=0x2B2D31)
            em.description = f"## {titulo}\n\n> **Valor:** R$ {preco:.2f} por sala"
            await ctx.send(embed=em, view=PainelComprarView())
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @commands.command(name="cs")
    async def prefix_cs(self, ctx):
        uid = ctx.author.id
        _ch_url = f"https://discord.com/api/v10/channels/{ctx.channel.id}/messages"
        _hdrs = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
        async with _aiohttp_v2.ClientSession() as _s:
            await _s.post(_ch_url, headers=_hdrs, json={
                "flags": 32768,
                "components": [{"id": 1, "type": 17, "accent_color": 0x5865F2, "components": [
                    {"id": 2, "type": 10, "content": f"## <:za_houst1:1483205696077037821> Criar Sala\n{ctx.author.mention} escolha o modo:"},
                    {"id": 3, "type": 1, "components": [
                        {"id": 4, "type": 2, "style": 2, "label": "Normal",      "custom_id": f"cs:modo:{uid}:1"},
                        {"id": 5, "type": 2, "style": 2, "label": "Infinito",    "custom_id": f"cs:modo:{uid}:2"},
                        {"id": 6, "type": 2, "style": 2, "label": "Outros Modos","custom_id": f"cs:modo:{uid}:3"},
                    ]},
                ]}],
            })



class UsuarioSelectView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot

    @discord.ui.select(cls=discord.ui.UserSelect, placeholder="Selecione um usuário...", min_values=1, max_values=1)
    async def sel(self, inter, s):
        usuario = s.values[0]
        uid = str(usuario.id)
        d = await asyncio.to_thread(perfil_usuario, uid)
        rows = await asyncio.to_thread(pedidos_por_id, uid)
        pedidos = [dict(r) for r in rows] if rows else []
        pagos = [p for p in pedidos if p.get("status") == "pago"]

        em = discord.Embed(
            title=f"{INFO}  Perfil de {usuario.display_name}",
            color=0x5865F2,
        )
        em.set_thumbnail(url=usuario.display_avatar.url)

        em.add_field(
            name=f"{CART}  Saldo Disponível",
            value=f"> **{d['saldo']}** sala(s)",
            inline=False,
        )

        em.add_field(
            name=f"{STATS}  Salas Gastas",
            value=(
                f"> Hoje: **{d['hoje']}** salas\n"
                f"> Ontem: **{d['ontem']}** salas\n"
                f"> 3 dias: **{d['3dias']}** salas\n"
                f"> 7 dias: **{d['semana']}** salas\n"
                f"> Total: **{d['total']}** salas"
            ),
            inline=False,
        )

        total_gasto = sum(float(p.get("valor") or 0) for p in pagos)
        total_salas_compradas = sum(p.get("quantia", 0) for p in pagos)
        em.add_field(
            name=f"{MONEY}  Resumo de Compras",
            value=(
                f"> {DOT} **{len(pagos)}** compras pagas\n"
                f"> {DOT} **{total_salas_compradas}** salas compradas\n"
                f"> {DOT} **R$ {total_gasto:.2f}** gasto total"
            ),
            inline=False,
        )

        if pagos:
            ultimas = sorted(pagos, key=lambda p: p.get("pago_em") or "", reverse=True)[:5]
            linhas = []
            for p in ultimas:
                dt = (p.get("pago_em") or p.get("criado_em") or "")[:16].replace("T", " ")
                linhas.append(f"> {ON} `{dt}` — **{p.get('quantia','?')} salas** — R$ {float(p.get('valor') or 0):.2f}")
                # Detalhes do PIX (apenas se foram capturados)
                np = (p.get("nome_pagador") or "").strip()
                e2e = (p.get("endtoend") or p.get("txid") or "").strip()
                if np:
                    linhas.append(f"> ⠀⠀{DOT} Pagador: **{np}**")
                if e2e:
                    linhas.append(f"> ⠀⠀{DOT} ID: `{e2e}`")
            em.add_field(name=f"{DOT}  Últimas 5 Compras", value="\n".join(linhas), inline=False)
        else:
            em.add_field(name=f"{DOT}  Compras", value=f"> {OFF} Nenhuma compra registrada.", inline=False)

        await inter.response.send_message(embed=em, view=UsuarioConfigView(self.bot, usuario), ephemeral=True)


class UsuarioConfigView(discord.ui.View):
    def __init__(self, bot, usuario):
        super().__init__(timeout=120)
        self.bot = bot
        self.usuario = usuario

    @discord.ui.button(label="Volta Saldo", emoji=PE["carteira"], style=discord.ButtonStyle.success, row=0)
    async def btn_volta(self, inter, btn):
        await inter.response.send_modal(_ModalVoltaSaldo(self.bot, self.usuario))

    @discord.ui.button(label="Remover Salas", emoji=PE["othertrash"], style=discord.ButtonStyle.danger, row=0)
    async def btn_remover(self, inter, btn):
        await inter.response.send_modal(_ModalRemoverSalas(self.bot, self.usuario))


class _ModalVoltaSaldo(discord.ui.Modal, title="💰 Volta Saldo"):
    qtd = discord.ui.TextInput(label="Quantidade de salas", placeholder="Ex: 50", min_length=1, max_length=5)

    def __init__(self, bot, usuario):
        super().__init__()
        self._bot = bot
        self._usuario = usuario

    async def on_submit(self, inter):
        try:
            quantia = int(self.qtd.value.strip())
            if quantia < 1: raise ValueError
        except ValueError:
            return await inter.response.send_message(embed=discord.Embed(description=f"{OFF}  Valor inválido.", color=config.COR_ERRO), ephemeral=True)

        usuario = self._usuario
        code = await asyncio.to_thread(adicionar_saldo_usuario, str(usuario.id), usuario.display_name, quantia)
        from utils.database import flush_all as _flush_all
        await asyncio.to_thread(_flush_all)

        em = discord.Embed(title=f"{ON}  Saldo Restaurado", color=config.COR_SUCESSO)
        em.add_field(name=f"{INFO} Usuário", value=usuario.mention, inline=True)
        em.add_field(name=f"{CART} Salas", value=f"**{quantia}**", inline=True)
        em.add_field(name=f"{GIFT} Key", value=f"`{code}`", inline=False)
        await inter.response.send_message(embed=em, ephemeral=True)
        asyncio.create_task(_logs.log_voltasaldo(inter.user, usuario, quantia, code))

        # DM
        _uid = usuario.id
        _nome = usuario.display_name
        _bot = self._bot
        async def _dm():
            try:
                user = await _bot.fetch_user(_uid)
                em_dm = discord.Embed(title=f"{ON}  Salas Adicionadas!", color=0x00FF7F)
                em_dm.description = (
                    f"Olá **{_nome}**! Suas salas foram adicionadas.\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                )
                em_dm.add_field(name=f"{CART}  Saldo Adicionado", value=f"> **{quantia} sala(s)** já estão no seu saldo!", inline=False)
                em_dm.add_field(name=f"{STATS}  Como Usar", value=f"> `/c1` Normal  `/c2` Infinito  `/c3` Full Capa\n> `/c` Carteira", inline=False)
                from cogs.comprar import AvaliacaoSugestaoView
                await user.send(embed=em_dm, view=AvaliacaoSugestaoView())
            except: pass
        asyncio.create_task(_dm())


class _ModalRemoverSalas(discord.ui.Modal, title="🗑️ Remover Salas"):
    qtd = discord.ui.TextInput(label="Quantidade de salas", placeholder="Ex: 10", min_length=1, max_length=5)

    def __init__(self, bot, usuario):
        super().__init__()
        self._bot = bot
        self._usuario = usuario

    async def on_submit(self, inter):
        try:
            quantia = int(self.qtd.value.strip())
            if quantia < 1: raise ValueError
        except ValueError:
            return await inter.response.send_message(embed=discord.Embed(description=f"{OFF}  Valor inválido.", color=config.COR_ERRO), ephemeral=True)

        usuario = self._usuario
        rm, sf = await asyncio.to_thread(remover_salas_cliente, str(usuario.id), quantia)
        if rm == 0:
            return await inter.response.send_message(embed=discord.Embed(description=f"{OFF}  Usuário sem saldo.", color=config.COR_ERRO), ephemeral=True)

        em = discord.Embed(title=f"{ON}  Salas Removidas", color=config.COR_SUCESSO)
        em.add_field(name=f"{INFO} Cliente", value=usuario.mention, inline=True)
        em.add_field(name=f"{STATS} Removidas", value=f"**{rm}**", inline=True)
        em.add_field(name=f"{CART} Saldo final", value=f"**{sf}**", inline=True)
        if rm < quantia:
            em.description = f"⚠️ Só havia {rm} (pedido: {quantia})."
        await inter.response.send_message(embed=em, ephemeral=True)
        asyncio.create_task(_logs.log_removersalas(inter.user, usuario, rm, sf))


class AdminToolsCog(commands.Cog):
    """Comandos admin que estavam presos dentro de _ModalRemoverSalas por um
    erro de indentação (a classe Modal nunca era fechada), o que impedia o
    discord.py de registrá-los como slash commands: gerasala, verifica,
    verificakey, lucro, editarpainel, modos, debugkeys, adm, painelglobal."""

    def __init__(self, bot):
        self.bot = bot

    # /gerasala
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="gerasala", description="[ADMIN] Gera keys.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.describe(quantia="Salas/key", keys="Qtd keys")
    @app_commands.choices(quantia=[app_commands.Choice(name=str(q), value=q) for q in config.QUANTIAS_DISPONIVEIS], keys=[app_commands.Choice(name=f"{n} key{'s' if n>1 else ''}", value=n) for n in [1,5,10,20,50]])
    async def cmd_gerasala(self, i, quantia:int, keys:int):
        await i.response.defer(ephemeral=True)
        if not is_admin(i.user.id): return await i.followup.send(embed=_err("Sem permissão."), ephemeral=True)
        cr = await asyncio.to_thread(criar_keys, quantia, 0, keys, str(i.user.id))
        em = _emb(f"{GIFT}  {len(cr)} Key(s) Gerada(s)", 0xA855F7)
        em.description = f"{DOT} **Salas/key:** {quantia}\n{DOT} **Keys:** {len(cr)}\n\n{SETTINGS} Envie aos clientes:"
        for chunk in [cr[j:j+10] for j in range(0,len(cr),10)]:
            em.add_field(name=f"{GIFT} Códigos", value="\n".join(f"`{k['code']}`" for k in chunk), inline=False)
        await i.followup.send(embed=em, ephemeral=True)
        asyncio.create_task(_logs.log_gerasala(i.user, quantia, keys, [k["code"] for k in cr]))

    # /verifica
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="verifica", description="[ADMIN] Compras de um usuário.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.describe(usuario="Usuário")
    async def cmd_verifica(self, i, usuario:discord.User):
        await i.response.defer(ephemeral=True)
        if not is_admin(i.user.id): return await i.followup.send(embed=_err("Sem permissão."), ephemeral=True)
        rows = await asyncio.to_thread(pedidos_por_id, str(usuario.id))
        pds = [dict(r) for r in rows] if rows else []
        em = _emb(f"{STATS}  Compras — {usuario.display_name}", 0x2ecc71)
        em.set_thumbnail(url=usuario.display_avatar.url)
        if not pds:
            em.description = f"{OFF} Nenhuma compra."
            return await i.followup.send(embed=em, ephemeral=True)
        em.description = f"**{len(pds)}** compra(s) de {usuario.mention}:"
        st = {"pago":ON,"pendente":REFRESH,"expirado":OFF}
        embs=[em]; cur=em; c=0
        for n,p in enumerate(pds,1):
            if c==20: cur=discord.Embed(color=0x2ecc71); embs.append(cur); c=0
            emoji=st.get(p.get("status",""),"❓"); dt=(p.get("criado_em")or"")[:16].replace("T"," ")
            k=f"`{p['key_gerada']}`" if p.get("key_gerada") else "—"
            cur.add_field(name=f"{emoji} #{n} — {dt}", value=f"{MOBILE} **{p.get('quantia','?')} salas** — R$ {float(p.get('valor')or 0):.2f}\n{GIFT} {k}", inline=True)
            c+=1
        await i.followup.send(embeds=embs[:10], ephemeral=True)
        asyncio.create_task(_logs.log_verifica(i.user, usuario, len(pds), sum(1 for p in pds if p.get("status")=="pago"), sum(float(p.get("valor")or 0) for p in pds if p.get("status")=="pago")))

    # /verificakey
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="verificakey", description="[ADMIN] Info de uma key.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.describe(key="Código da key")
    async def cmd_verificakey(self, i, key:str):
        await i.response.defer(ephemeral=True)
        if not is_admin(i.user.id): return await i.followup.send(embed=_err("Sem permissão."), ephemeral=True)
        row = await asyncio.to_thread(buscar_key, key.strip().upper())
        if not row: return await i.followup.send(embed=_err(f"Key `{key}` não encontrada."), ephemeral=True)
        s=row["quantia"]-row["salas_usadas"]; di=row.get("dono_id")or"—"; pend=di.startswith("PENDENTE_") if di!="—" else False
        em = _emb(f"{GIFT}  Info Key", 0xA855F7)
        em.add_field(name=f"{GIFT} Key", value=f"`{row['code']}`", inline=False)
        em.add_field(name=f"{INFO} Dono", value=f"{'⏳ ' if pend else ''}{row.get('dono_nome','—')}", inline=True)
        em.add_field(name=f"{CART} Saldo", value=f"**{s}** salas", inline=True)
        em.add_field(name=f"{STATS} Total", value=f"{row['quantia']}", inline=True)
        em.add_field(name=f"{MOBILE} Usadas", value=f"{row['salas_usadas']}", inline=True)
        em.add_field(name=f"{DOT} Criada", value=(row.get("criado_em")or"")[:16].replace("T"," ") or "—", inline=True)
        em.add_field(name=f"{ON} Resgatada", value=(row.get("resgatado_em")or"")[:16].replace("T"," ") or "—", inline=True)
        await i.followup.send(embed=em, ephemeral=True)
        asyncio.create_task(_logs.log_verificakey(i.user, row["code"], row.get("dono_nome","—"), di, s, row["salas_usadas"], row["quantia"]))

    # /lucro
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="lucro", description="[ADMIN] Lucro estimado.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_lucro(self, i):
        await i.response.defer(ephemeral=True)
        if not is_admin(i.user.id): return await i.followup.send(embed=_err("Sem permissão."), ephemeral=True)
        from utils.database import lucro_config_get, vendas_usuario
        uid = str(i.user.id)
        cfg = await asyncio.to_thread(lucro_config_get, uid)
        v   = await asyncio.to_thread(vendas_usuario)  # total geral, sem filtro por user
        vps = cfg["valor_por_sala"] if cfg["valor_por_sala"] > 0 else 0.03

        em = _emb(f"{MONEY}  Lucro — SalasFF", 0x2ecc71, f"R${vps:.2f}/sala • {_ts().strftime('%d/%m/%Y %H:%M')}")
        em.add_field(name=f"{DOT} Hoje",   value=f"{STATS} **{v['hoje']}** vendidas\n**R$ {v['hoje']*vps:.2f}**",   inline=True)
        em.add_field(name=f"{DOT} Ontem",  value=f"{STATS} **{v['ontem']}** vendidas\n**R$ {v['ontem']*vps:.2f}**",  inline=True)
        em.add_field(name=f"{DOT} Semana", value=f"{STATS} **{v['semana']}** vendidas\n**R$ {v['semana']*vps:.2f}**", inline=True)
        em.add_field(name=f"{TOP} Total",  value=f"{STATS} **{v['total']}** vendidas\n**R$ {v['total']*vps:.2f}**",  inline=False)
        await i.followup.send(embed=em, view=LucroSlashView(), ephemeral=True)

    # /editarpainel
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="editarpainel", description="[ADMIN] Edita texto de painel.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.choices(painel=[app_commands.Choice(name="🎮 Salas",value="salas"),app_commands.Choice(name="🛒 Compras",value="compras")])
    async def cmd_editarpainel(self, i, painel:app_commands.Choice[str]):
        if not is_admin(i.user.id): return await i.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        if painel.value=="salas": await i.response.send_modal(EditarPainelModal(_load_painel()))
        else: await i.response.send_modal(EditarComprasModal())

    # /modos
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="modos", description="[ADMIN] Modos configurados.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_modos(self, i):
        await i.response.defer(ephemeral=True)
        if not is_admin(i.user.id): return await i.followup.send(embed=_err("Sem permissão."), ephemeral=True)
        em = _emb(f"{SETTINGS}  Modos", desc="Config atual e API:")
        txt=""
        for k,v in config.MODOS.items(): txt+=f"**{k}** — {v['nome']}\n> salaid: `{v['salaid']}`\n> canal: <#{v['canal_id']}>\n\n"
        em.add_field(name=f"{INFO} Config", value=txt, inline=False)
        try:
            d = await api.listar_modos()
            if d.get("modos"): em.add_field(name=f"{STATS} API", value="\n".join(f"**{m.get('nome','?')}** — `{m.get('salaid','?')}`" for m in d["modos"]), inline=False)
        except Exception as ex: em.add_field(name="❌ Erro", value=str(ex)[:200], inline=False)
        await i.followup.send(embed=em, ephemeral=True)

    # /debugkeys
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="debugkeys", description="[ADMIN] Diagnóstico dados.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_debugkeys(self, i):
        await i.response.defer(ephemeral=True)
        if not is_admin(i.user.id): return await i.followup.send(embed=_err("Sem permissão."), ephemeral=True)
        from utils.database import DATA_DIR, KEYS_PATH, SALAS_PATH, PEDIDOS_PATH, _load, _CODE_DIR
        em = _emb(f"{SETTINGS}  Debug Dados", 0xFF8C00)
        em.add_field(name="DATA_DIR", value=f"`{DATA_DIR}`", inline=False)
        em.add_field(name="KEYS_PATH", value=f"`{KEYS_PATH}`", inline=False)
        ks=_load(KEYS_PATH); ss=_load(SALAS_PATH); ps=_load(PEDIDOS_PATH)
        em.add_field(name=f"{GIFT} Keys", value=f"**{len(ks)}**", inline=True)
        em.add_field(name=f"{MOBILE} Salas", value=f"**{len(ss)}**", inline=True)
        em.add_field(name=f"{CART} Pedidos", value=f"**{len(ps)}**", inline=True)
        cs=sum(1 for k in ks.values() if k["quantia"]-k["salas_usadas"]>0 and k.get("dono_id"))
        ts=sum(k["quantia"]-k["salas_usadas"] for k in ks.values() if k["quantia"]-k["salas_usadas"]>0 and k.get("dono_id"))
        em.add_field(name="Com saldo", value=f"**{cs}** keys ativas — **{ts}** salas", inline=False)
        sk=sorted(ks.values(), key=lambda k:k.get("criado_em",""), reverse=True)[:3]
        if sk: em.add_field(name="Últimas 3 keys", value="\n".join(f"`{k['code']}` modo={k['modo']} qtd={k['quantia']}" for k in sk), inline=False)
        sizes=""
        for nm,pt in [("keys.json",KEYS_PATH),("salas.json",SALAS_PATH),("pedidos_pix.json",PEDIDOS_PATH)]:
            try: sizes+=f"`{nm}` → {os.path.getsize(pt)//1024}kb\n"
            except: sizes+=f"`{nm}` → ❌\n"
        em.add_field(name="Tamanho", value=sizes, inline=False)
        lp=os.path.join(_CODE_DIR,"keys.json")
        if os.path.abspath(lp)!=os.path.abspath(KEYS_PATH):
            try: lk=_load(lp); em.add_field(name="Local keys", value=f"`{lp}`\n**{len(lk)}** registros", inline=False)
            except: pass
        await i.followup.send(embed=em, ephemeral=True)

    # /adm — Painel unificado de administração
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="adm", description="[ADMIN] Painel de administração completo.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_adm(self, i):
        if not is_admin(i.user.id):
            return await i.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        em = _emb(f"{SETTINGS}  Painel Admin — SalasFF", 0x5865F2)
        em.description = f"Selecione uma categoria abaixo.\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        await i.response.send_message(embed=em, view=AdmSelectView(self), ephemeral=True)

    # /painelglobal
    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="painelglobal", description="[ADMIN] Central de painéis: criação, config e postagem.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_painelglobal(self, i: discord.Interaction):
        import logging as _lg_pg
        _log_pg = _lg_pg.getLogger("salasff.painelglobal")
        try:
            await i.response.defer(ephemeral=True)
            if not is_admin(i.user.id):
                return await i.followup.send(embed=_err("Sem permissão."), ephemeral=True)
            em = _emb(f"{SETTINGS}  Painel Global", 0x5865F2)
            em.description = (
                "Selecione uma das opções abaixo:\n\n"
                f"{DOT} **Criação de Salas** — posta o painel de criação no canal atual.\n"
                f"{DOT} **Config de Compras** — altera preço e título do painel de compras.\n"
                f"{DOT} **Postar Compras/Grátis** — publica o painel de compras ou grátis.\n"
                f"{DOT} **Adicionar Bot** — publica o painel de instalação (Futuro Cliente).\n"
                f"{DOT} **Dono de Org** — vantagens exclusivas.\n"
                f"{DOT} **Tutorial Bônus** — passo a passo de como pegar bônus.\n"
                f"{DOT} **Token Mode** — envie mensagens de sala com sua própria conta."
            )
            await i.followup.send(embed=em, view=PainelGlobalView(), ephemeral=True)
        except Exception as _ex_pg:
            _log_pg.error(f"[painelglobal] {_ex_pg}", exc_info=True)
            try:
                await i.followup.send(embed=_err("Erro interno.", f"`{_ex_pg}`"), ephemeral=True)
            except Exception:
                pass

class InfConfigCog(commands.Cog):
    """Cog dedicado somente ao /infconfig — criado para contornar o fato de o
    comando original ter ficado preso dentro de _ModalRemoverSalas por um erro
    de indentação e nunca ter sido sincronizado."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="infconfig", description="[ADMIN] Saldo clientes, estatísticas e envio de DM.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_infconfig(self, i: discord.Interaction):
        if not is_admin(i.user.id):
            return await i.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        em = _emb(f"{STATS}  Painel de Informações", 0x5865F2)
        em.description = "Selecione uma opção abaixo."
        await i.response.send_message(embed=em, view=InfConfigView(), ephemeral=True)


class PainelGlobalView(discord.ui.View):
    """View persistente — agrupa /painel, /painelcompraconfig e /painelcomprar."""

    def __init__(self):
        super().__init__(timeout=None)

    # ── 1) Painel de Criação de Salas ─────────────────────────────────────
    @discord.ui.button(label="Criação de Salas", emoji=PE["swordbattle"], style=discord.ButtonStyle.primary, row=0, custom_id="painelglobal:criacao")
    async def btn_painel_salas(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)
        cfg_p = _load_painel()
        titulo = cfg_p.get("titulo", "SalasFF")
        linha1 = cfg_p.get("linha1", "")
        desc   = cfg_p.get("desc", "")
        rodape = cfg_p.get("rodape", "")
        texto = titulo
        if linha1: texto += f"\n{linha1}"
        if desc:   texto += f"\n\n{desc}"
        sub_comps = [{"id": 2, "type": 10, "content": texto}]
        if rodape: sub_comps.append({"id": 3, "type": 10, "content": f"-# {rodape}"})
        sub_comps += [
            {"id": 4, "type": 1, "components": [
                {"id": 5, "type": 2, "style": 2, "label": "Normal",       "custom_id": "painel:criar:1", "emoji": _em("jogadores")},
                {"id": 6, "type": 2, "style": 2, "label": "Infinito",     "custom_id": "painel:criar:2", "emoji": _em("play")},
                {"id": 7, "type": 2, "style": 2, "label": "Outros Modos", "custom_id": "painel:criar:3", "emoji": _em("top")},
            ]},
            {"id": 8, "type": 1, "components": [
                {"id": 9, "type": 2, "style": 2, "label": "Meu Saldo", "custom_id": "painel:saldo", "emoji": _em("carteira")},
            ]},
        ]
        payload = {"flags": 32768, "components": [{"id": 1, "type": 17, "accent_color": 0x5865F2, "components": sub_comps}]}
        ok = await _post_v2_channel(inter.channel.id, payload)
        if not ok:
            await inter.channel.send(embed=_embed_painel(cfg_p), view=PainelView())
        await inter.followup.send(embed=_emb(f"{ON}  Painel de salas postado no canal.", config.COR_SUCESSO), ephemeral=True)

    # ── 2) Config do Painel de Compras ────────────────────────────────────
    @discord.ui.button(label="Config de Compras", emoji=PE["settings"], style=discord.ButtonStyle.secondary, row=0, custom_id="painelglobal:cfg_compras")
    async def btn_pcc(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        from cogs.botconfig import carregar_cfg, ModalPreco, ModalTituloPanel, ModalSubtituloPanel
        cfg = carregar_cfg()
        preco = cfg.get("preco_por_sala", 0.09)
        titulo = cfg.get("titulo_painel_compra", "F Applications - Compre Aqui")
        sub_titulo = cfg.get("subtitulo_painel_compra", "")
        sub_desc   = cfg.get("subdesc_painel_compra", "")
        em = _emb(f"{SETTINGS}  Config Painel de Compras", config.COR_INFO)
        em.add_field(name=f"{MONEY} Preço atual", value=f"**R$ {preco:.4f}**", inline=True)
        em.add_field(name=f"{DOT} 100 salas", value=f"**R$ {preco*100:.2f}**", inline=True)
        em.add_field(name=f"{DOT} 300 salas", value=f"**R$ {preco*300:.2f}**", inline=True)
        em.add_field(name=f"{INFO} Título do painel", value=f"`{titulo}`", inline=False)
        sub_preview = "_Vazia — nada será postado abaixo._"
        if sub_titulo or sub_desc:
            p = []
            if sub_titulo: p.append(f"**{sub_titulo}**")
            if sub_desc: p.append(sub_desc[:150] + ("…" if len(sub_desc) > 150 else ""))
            sub_preview = "\n".join(p)
        em.add_field(name=f"{INFO} Embed secundária", value=sub_preview, inline=False)

        class _PCCView(discord.ui.View):
            def __init__(s): super().__init__(timeout=120)

            @discord.ui.button(label="Alterar Preço", emoji=PE["settings"], style=discord.ButtonStyle.primary, row=0)
            async def a(s, it, b):
                from cogs.botconfig import carregar_cfg as _cc, ModalPreco as _MP
                await it.response.send_modal(_MP(_cc()))

            @discord.ui.button(label="Alterar Título", emoji=PE["info"], style=discord.ButtonStyle.secondary, row=0)
            async def t(s, it, b):
                from cogs.botconfig import carregar_cfg as _cc, ModalTituloPanel as _MT
                await it.response.send_modal(_MT(_cc()))

            @discord.ui.button(label="Editar Embed Secundária", emoji=PE["info"], style=discord.ButtonStyle.success, row=1)
            async def sub(s, it, b):
                from cogs.botconfig import carregar_cfg as _cc, ModalSubtituloPanel as _MS
                await it.response.send_modal(_MS(_cc()))

        await inter.response.send_message(embed=em, view=_PCCView(), ephemeral=True)

    # ── 3) Postar Painel de Compras ou Grátis ─────────────────────────────
    @discord.ui.button(label="Postar Compras/Grátis", emoji=PE["carteira"], style=discord.ButtonStyle.success, row=0, custom_id="painelglobal:postar_compras")
    async def btn_painelcomprar(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        em = _emb(f"{CART}  Qual painel postar?", config.COR_INFO)
        em.description = "Selecione abaixo qual dos dois painéis você quer publicar no canal atual."
        await inter.response.send_message(embed=em, view=_PainelComprarEscolhaView(inter.channel), ephemeral=True)

    # ── 4) Painel Adicionar Bot (Futuro Cliente) ──────────────────────────
    @discord.ui.button(label="Adicionar Bot", emoji=PE["adduser"], style=discord.ButtonStyle.primary, row=1, custom_id="painelglobal:add_bot")
    async def btn_adicionar_bot(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)

        client_id = str(inter.client.application_id or "1481499372750635038")
        oauth_url = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={client_id}"
            f"&integration_type=1"
            f"&scope=applications.commands"
        )

        texto = (
            f"-# {e('bot')} Equipe F Applications\n\n"
            f"## {e('adduser')} Adicione o Bot à Sua Conta\n\n"
            f"> {e('on')} **Tenha o F Applications disponível em qualquer servidor.**\n"
            f"> {e('swordbattle')} **Crie salas Free Fire em segundos, direto pelo seu Discord.**\n"
            f"> {e('stats')} **Acompanhe seu saldo, histórico e comandos onde quiser.**\n\n"
            f"{e('click')} **Clique no botão abaixo e autorize em poucos segundos.**"
        )

        payload = {
            "flags": 32768,
            "components": [{"id": 1, "type": 17, "accent_color": 0x5865F2, "components": [
                {"id": 2, "type": 10, "content": texto},
                {"id": 3, "type": 1, "components": [
                    {"id": 4, "type": 2, "style": 5, "label": "Futuro Cliente", "url": oauth_url, "emoji": _em("adduser")},
                ]},
                {"id": 5, "type": 10, "content": "-# F Applications • Adicionar ao seu perfil Discord"},
            ]}],
        }

        ok = await _post_v2_channel(inter.channel.id, payload)
        if not ok:
            # Fallback embed clássico
            em_fb = discord.Embed(color=0x5865F2)
            em_fb.description = (
                f"-# {PE['bot']} Equipe F Applications\n\n"
                f"## {PE['adduser']} Adicione o Bot à Sua Conta\n\n"
                f"> {PE['on']} **Tenha o F Applications disponível em qualquer servidor.**\n"
                f"> {PE['swordbattle']} **Crie salas Free Fire em segundos, direto pelo seu Discord.**\n"
                f"> {PE['stats']} **Acompanhe seu saldo, histórico e comandos onde quiser.**\n\n"
                f"{PE['click']} **Clique no botão abaixo e autorize em poucos segundos.**"
            )
            em_fb.set_footer(text="F Applications • Adicionar ao seu perfil Discord")
            view_fb = discord.ui.View(timeout=None)
            view_fb.add_item(discord.ui.Button(
                label="Futuro Cliente",
                emoji=PE["adduser"],
                style=discord.ButtonStyle.link,
                url=oauth_url,
            ))
            await inter.channel.send(embed=em_fb, view=view_fb)

        await inter.followup.send(embed=_emb(f"{ON}  Painel de adicionar bot postado.", config.COR_SUCESSO), ephemeral=True)

    # ── 5) Painel Dono de Org (vantagens exclusivas) ──────────────────────
    @discord.ui.button(label="Dono de Org", emoji=PE["top"], style=discord.ButtonStyle.secondary, row=1, custom_id="painelglobal:dono_org")
    async def btn_dono_org(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)

        client_id = str(inter.client.application_id or "1481499372750635038")
        invite_url = (
            f"https://discord.com/oauth2/authorize"
            f"?client_id={client_id}"
            f"&permissions=8"
            f"&integration_type=0"
            f"&scope=bot+applications.commands"
        )

        payload = {
            "flags": 32768,
            "components": [{"id": 1, "type": 17, "accent_color": 0xA855F7, "components": [
                {"id": 2, "type": 10, "content": f"-# {e('bot')}  Equipe F Applications"},
                {"id": 3, "type": 10, "content": f"## {e('adduser')}  Adicione o Bot no Seu Servidor"},
                {"id": 4, "type": 14, "divider": True, "spacing": 1},
                {"id": 5, "type": 10, "content": (
                    f"### {e('top')}  Vantagens Exclusivas — Donos de Org\n\n"
                    f"{e('presente')}  **Promoções exclusivas** só para donos de org.\n\n"
                    f"{e('adduser')}  **Trocar foto e nome do bot** no seu servidor.\n\n"
                    f"{e('stats')}  **Painéis personalizados** com título e embed do seu jeito.\n\n"
                    f"{e('otherdollar')}  **Vender sala pros seus adm num valor melhor.**"
                )},
                {"id": 6, "type": 14, "divider": True, "spacing": 1},
                {"id": 7, "type": 10, "content": f"{e('click')}  **Use `/dev` no seu servidor para configurar tudo.**"},
                {"id": 8, "type": 1, "components": [
                    {"id": 9, "type": 2, "style": 5, "label": "Adicionar no Servidor", "url": invite_url, "emoji": _em("adduser")},
                ]},
                {"id": 10, "type": 10, "content": "-# F Applications • Benefícios exclusivos de parceria"},
            ]}],
        }

        ok = await _post_v2_channel(inter.channel.id, payload)
        if not ok:
            # Fallback embed clássico
            em_fb = discord.Embed(color=0xA855F7)
            em_fb.description = (
                f"-# {PE['bot']}  Equipe F Applications\n\n"
                f"## {ADDUSER}  Adicione o Bot no Seu Servidor\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"### {TOP}  Vantagens Exclusivas — Donos de Org\n\n"
                f"{PRESENTE}  **Promoções exclusivas** só para donos de org.\n\n"
                f"{ADDUSER}  **Trocar foto e nome do bot** no seu servidor.\n\n"
                f"{STATS}  **Painéis personalizados** com título e embed do seu jeito.\n\n"
                f"{DOLLAR}  **Vender sala pros seus adm num valor melhor.**\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{CLICK}  **Use `/dev` no seu servidor para configurar tudo.**"
            )
            em_fb.set_footer(text="F Applications • Benefícios exclusivos de parceria")
            view_fb = discord.ui.View(timeout=None)
            view_fb.add_item(discord.ui.Button(
                label="Adicionar no Servidor",
                emoji=PE["adduser"],
                style=discord.ButtonStyle.link,
                url=invite_url,
            ))
            await inter.channel.send(embed=em_fb, view=view_fb)

        await inter.followup.send(embed=_emb(f"{ON}  Painel de vantagens postado.", config.COR_SUCESSO), ephemeral=True)

    # ── 6) Tutorial de Bônus ──────────────────────────────────────────────
    @discord.ui.button(label="Tutorial Bônus", emoji=PE["presente"], style=discord.ButtonStyle.success, row=1, custom_id="painelglobal:tutorial")
    async def btn_tutorial_bonus(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)

        payload = _tutorial_bonus_v2_payload()
        ok = await _post_v2_channel(inter.channel.id, payload)
        if not ok:
            # Fallback embed clássico (sem view, só embed estática)
            try:
                await inter.channel.send(embed=_tutorial_bonus_embed())
            except Exception as _ex:
                _log.warning(f"[tutorial bonus] {_ex}")
                return await inter.followup.send(embed=_err("Erro ao postar.", f"`{_ex}`"), ephemeral=True)

        await inter.followup.send(embed=_emb(f"{ON}  Tutorial de bônus postado.", config.COR_SUCESSO), ephemeral=True)

    @discord.ui.button(label="Token Mode", emoji=PE["megafone"], style=discord.ButtonStyle.secondary, row=2, custom_id="painelglobal:token_mode")
    async def btn_token_mode(self, inter: discord.Interaction, btn: discord.ui.Button):
        from cogs.token_mode import abrir_painel_token
        await abrir_painel_token(inter)

    @discord.ui.button(label="Convidar Amigo", emoji="🎉", style=discord.ButtonStyle.success, row=2, custom_id="painelglobal:convites")
    async def btn_convites(self, inter: discord.Interaction, btn: discord.ui.Button):
        from cogs.convites import abrir_painel_convites
        await abrir_painel_convites(inter)

    # ── 8) Ranking Semanal — Postar painel público ─────────────────────────
    @discord.ui.button(label="Postar Ranking", emoji="🏆", style=discord.ButtonStyle.primary, row=3, custom_id="painelglobal:ranking_postar")
    async def btn_ranking_postar(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            from cogs.ranking import postar_ranking_canal
            await postar_ranking_canal(inter)
        except Exception as _ex:
            import logging; logging.getLogger("salasff").error(f"[painelglobal:ranking_postar] {_ex}")
            try:
                await inter.response.send_message(embed=_err("Erro ao abrir ranking.", f"`{_ex}`"), ephemeral=True)
            except Exception:
                pass

    # ── 9) Ranking Semanal — Ver top 10 (ephemeral admin) ─────────────────
    @discord.ui.button(label="Ver Ranking", emoji="📊", style=discord.ButtonStyle.secondary, row=3, custom_id="painelglobal:ranking_ver")
    async def btn_ranking_ver(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            from cogs.ranking import ver_ranking_ephemeral
            await ver_ranking_ephemeral(inter)
        except Exception as _ex:
            import logging; logging.getLogger("salasff").error(f"[painelglobal:ranking_ver] {_ex}")
            try:
                await inter.response.send_message(embed=_err("Erro ao buscar ranking.", f"`{_ex}`"), ephemeral=True)
            except Exception:
                pass

    # ── 10) PIX Banco — config de credenciais por guild ───────────────────
    @discord.ui.button(label="PIX Banco", emoji="🏦", style=discord.ButtonStyle.primary, row=3, custom_id="painelglobal:pix_banco")
    async def btn_pix_banco(self, inter: discord.Interaction, btn: discord.ui.Button):
        # Aqui é admin DA GUILD (não admin global), pra cada mediador configurar o próprio
        if not inter.guild:
            return await inter.response.send_message(
                embed=_err("Use em um servidor.", ""), ephemeral=True
            )
        perms = inter.user.guild_permissions
        if not (perms.administrator or perms.manage_guild):
            return await inter.response.send_message(
                embed=_err("Apenas admins do servidor."), ephemeral=True
            )
        try:
            from cogs.pix_banco import PixBancoView, _embed_status
            await inter.response.send_message(
                embed=_embed_status(str(inter.guild_id)),
                view=PixBancoView(),
                ephemeral=True,
            )
        except Exception as _ex:
            import logging; logging.getLogger("salasff").error(f"[painelglobal:pix_banco] {_ex}")
            try:
                await inter.response.send_message(embed=_err("Erro ao abrir painel.", f"`{_ex}`"), ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Mediador PIX", emoji="💰", style=discord.ButtonStyle.success, row=4, custom_id="painelglobal:mediador_pix")
    async def btn_mediador_pix(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            from cogs.mediador_painel import abrir_painel_mediador
            await abrir_painel_mediador(inter)
        except Exception as _ex:
            import logging; logging.getLogger("salasff").error(f"[painelglobal:mediador_pix] {_ex}")
            try:
                await inter.response.send_message(embed=_err("Erro ao abrir painel.", f"`{_ex}`"), ephemeral=True)
            except Exception:
                pass


class _PainelComprarEscolhaView(discord.ui.View):
    """Sub-view do /painelglobal: escolhe entre painel de compras ou painel grátis."""

    def __init__(self, canal):
        super().__init__(timeout=120)
        self._canal = canal

    @discord.ui.button(label="Painel de Compras", emoji=PE["carteira"], style=discord.ButtonStyle.primary, row=0)
    async def btn_compras(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)
        from cogs.botconfig import carregar_cfg
        from utils.pix import get_preco_por_sala_guild
        import aiohttp as _aiohttp

        gid = str(inter.guild.id) if inter.guild else None
        preco = get_preco_por_sala_guild(gid)
        cfg = carregar_cfg()
        titulo = cfg.get("titulo_painel_compra", "F Applications - Compre Aqui")
        sub_titulo = cfg.get("subtitulo_painel_compra", "")
        sub_desc   = cfg.get("subdesc_painel_compra", "")
        sub_rodape = cfg.get("subrodape_painel_compra", "")

        # Emojis dos botões (mesmos do PainelComprarView)
        emoji_perfil  = {"id": str(PE["info"].id),     "name": PE["info"].name,     "animated": PE["info"].animated}
        emoji_comprar = {"id": str(PE["stats"].id),    "name": PE["stats"].name,    "animated": PE["stats"].animated}

        containers = [
            {
                "id": 1,
                "type": 17,
                "components": [
                    {
                        "id": 2,
                        "type": 10,
                        "content": f"## {titulo}\n\n> **Valor:** R$ {preco:.2f} por sala",
                    },
                    {
                        "id": 3,
                        "type": 1,
                        "components": [
                            {
                                "id": 4,
                                "type": 2,
                                "style": 2,
                                "label": "Meu Perfil",
                                "custom_id": "comprar:perfil",
                                "emoji": emoji_perfil,
                            },
                            {
                                "id": 5,
                                "type": 2,
                                "style": 3,
                                "label": "Comprar Salas",
                                "custom_id": "comprar:comprar",
                                "emoji": emoji_comprar,
                            },
                        ],
                    },
                ],
            }
        ]

        # Embed secundária (se configurada) — posta em um container V2 separado abaixo
        if sub_titulo or sub_desc or sub_rodape:
            sub_parts = []
            if sub_titulo:
                sub_parts.append(f"## {sub_titulo}")
            if sub_desc:
                sub_parts.append(sub_desc)
            if sub_rodape:
                sub_parts.append(f"-# {sub_rodape}")
            containers.append({
                "id": 10,
                "type": 17,
                "accent_color": 0x2B2D31,
                "components": [
                    {"id": 11, "type": 10, "content": "\n\n".join(sub_parts)},
                ],
            })

        payload = {"flags": 32768, "components": containers}

        url = f"https://discord.com/api/v10/channels/{self._canal.id}/messages"
        headers = {
            "Authorization": f"Bot {config.DISCORD_TOKEN}",
            "Content-Type": "application/json",
        }
        ok = False
        try:
            async with _aiohttp.ClientSession() as _sess:
                async with _sess.post(url, headers=headers, json=payload) as resp:
                    ok = resp.status in (200, 201)
                    if not ok:
                        body = await resp.text()
                        _log.warning(f"[compv2] status={resp.status} {body[:200]}")
        except Exception as ex:
            _log.error(f"[compv2] Erro HTTP: {ex}")

        if not ok:
            # Fallback embed clássico
            from cogs.comprar import PainelComprarView
            em = discord.Embed(color=0x2B2D31)
            em.description = f"## {titulo}\n\n> **Valor:** R$ {preco:.2f} por sala"
            await self._canal.send(embed=em, view=PainelComprarView())

        await inter.followup.send(embed=_emb(f"{ON}  Painel de compras postado.", config.COR_SUCESSO), ephemeral=True)

    @discord.ui.button(label="Painel Grátis", emoji=PE["gift"], style=discord.ButtonStyle.success, row=0)
    async def btn_gratis(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)
        texto_gratis = (
            f"-# {e('bot')} Equipe F Applications\n\n"
            f"## {e('swordbattle')} Salas Teste\n\n"
            f"> {e('on')} **Está indeciso em comprar salas com nossa equipe?**\n"
            f"> {e('stats')} **Teste agora! Receba salas grátis, ganhe bônus por compra e aproveite o melhor preço do mercado.**\n\n"
            f"{e('click')} **Basta clicar no botão abaixo e já recebe diretamente na sua conta.**"
        )
        payload_gratis = {
            "flags": 32768,
            "components": [{"id": 1, "type": 17, "accent_color": 0x00FF7F, "components": [
                {"id": 2, "type": 10, "content": texto_gratis},
                {"id": 3, "type": 1, "components": [
                    {"id": 4, "type": 2, "style": 3, "label": "Quero Participar", "custom_id": "gratis:quero_participar", "emoji": _em("click")},
                ]},
                {"id": 5, "type": 10, "content": "-# F Applications • Disponibilidade por tempo limitado"},
            ]}],
        }
        ok = await _post_v2_channel(self._canal.id, payload_gratis)
        if not ok:
            from cogs.comprar import _embed_painel_gratis, PainelGratisView
            await self._canal.send(embed=_embed_painel_gratis(), view=PainelGratisView())
        await inter.followup.send(embed=_emb(f"{ON}  Painel grátis postado.", config.COR_SUCESSO), ephemeral=True)


class SalaV2Cog(commands.Cog):
    """Handlers das interações dos botões V2 da sala criada."""
    def __init__(self, bot): self.bot = bot

    @commands.Cog.listener()
    async def on_interaction(self, inter: discord.Interaction):
        if inter.type != discord.InteractionType.component: return
        cid = inter.data.get("custom_id", "")

        # ── Carteira V2 ──────────────────────────────────────────────────────
        if cid.startswith("cw:"):
            action = cid.split(":", 1)[1]
            uid = str(inter.user.id)
            _gid = str(inter.guild.id) if inter.guild else None

            if action == "comprar":
                from cogs.comprar import ComprarModal
                return await inter.response.send_modal(ComprarModal(_gid))

            if action == "lucro":
                await inter.response.defer(ephemeral=True)
                from utils.database import lucro_config_get, salas_criadas_usuario
                cfg = await asyncio.to_thread(lucro_config_get, uid)
                v   = await asyncio.to_thread(salas_criadas_usuario, uid)
                vps = cfg["valor_por_sala"]; orgs = cfg.get("orgs", [])

                if vps == 0 and not orgs:
                    pass  # mostra tudo zerado mesmo

                def _sl(i, txt, btn): return {"id": i, "type": 9, "components": [{"id": i+1, "type": 10, "content": txt}], "accessory": btn}
                def _sb(i, label, cid, style=2, disabled=False):
                    b = {"id": i, "type": 2, "style": style, "label": label, "custom_id": cid}
                    if disabled: b["disabled"] = True
                    return b

                comps = [{"id": 2, "type": 10, "content": f"## {STATS} Lucro — SalasFF"}]
                comps += [
                    _sl(3,  f"**Hoje**\n{v['hoje']} salas",    _sb(5,  f"R$ {v['hoje']*vps:.2f}",    "lc:hoje",   disabled=True)),
                    _sl(6,  f"**Ontem**\n{v['ontem']} salas",  _sb(8,  f"R$ {v['ontem']*vps:.2f}",   "lc:ontem",  disabled=True)),
                    _sl(9,  f"**Semana**\n{v['semana']} salas", _sb(11, f"R$ {v['semana']*vps:.2f}",  "lc:semana", disabled=True)),
                    {"id": 12, "type": 14, "divider": True, "spacing": 1},
                    _sl(13, f"**Total**\n{v['total']} salas",   _sb(15, f"R$ {v['total']*vps:.2f}",   "lc:total",  disabled=True)),
                ]
                comps.append({"id": 16, "type": 1, "components": [
                    {"id": 17, "type": 2, "style": 1, "label": "Alterar Preço",  "custom_id": "cw:lucro_preco"},
                    {"id": 19, "type": 2, "style": 3, "label": "Add Org",        "custom_id": "cw:lucro_org"},
                    {"id": 20, "type": 2, "style": 4, "label": "Remover Org",    "custom_id": "cw:lucro_rmorg"},
                ]})

                _lfw = f"https://discord.com/api/v10/webhooks/{inter.application_id}/{inter.token}?wait=true"
                _lok = False
                try:
                    async with _aiohttp_v2.ClientSession() as _s:
                        async with _s.post(_lfw, json={"flags": 64|32768, "components": [{"id": 1, "type": 17, "components": comps}]}) as _r:
                            _lok = _r.status in (200, 201)
                            if not _lok: _log.warning(f"[lucro v2] {_r.status} {await _r.text()[:200]}")
                except Exception as _ex:
                    _log.warning(f"[lucro v2] {_ex}")

                if not _lok:
                    em = discord.Embed(title=f"{STATS}  Lucro — SalasFF", color=0x2ecc71)
                    if vps > 0:
                        em.add_field(name=f"{MONEY} Hoje",   value=f"{v['hoje']} salas\nR$ {v['hoje']*vps:.2f}",    inline=True)
                        em.add_field(name=f"{MONEY} Ontem",  value=f"{v['ontem']} salas\nR$ {v['ontem']*vps:.2f}",  inline=True)
                        em.add_field(name=f"{MONEY} Semana", value=f"{v['semana']} salas\nR$ {v['semana']*vps:.2f}", inline=True)
                        em.add_field(name=f"{DOLLAR} Total", value=f"{v['total']} salas\nR$ {v['total']*vps:.2f}",  inline=False)
                    await inter.followup.send(embed=em, view=LucroSubView(), ephemeral=True)
                return

            if action == "go":
                atual = await asyncio.to_thread(go_config_get, uid)
                label = f"{atual} min" if atual > 0 else f"{config.DEFAULT_INICIAR_MINUTOS} min (padrão)"
                em = _info("Tempo de GO", f"{SETTINGS} Tempo atual: **{label}**\n{DOT} Selecione abaixo o tempo para início automático:")
                return await inter.response.send_message(embed=em, view=ConfigGoView(), ephemeral=True)

            if action == "senha":
                from utils.database import senha_config_get
                atual = await asyncio.to_thread(senha_config_get, uid)
                return await inter.response.send_modal(ConfigSenhaModal(atual))

            if action == "senha_clear":
                await inter.response.defer(ephemeral=True)
                from utils.database import senha_config_set
                await asyncio.to_thread(senha_config_set, uid, "")
                em = _ok("Senha Removida", f"{DOT} Suas próximas salas serão criadas **sem senha** (públicas).")
                return await inter.followup.send(embed=em, ephemeral=True)

            if action == "bonus":
                await inter.response.defer(ephemeral=True)
                from utils.database import (bonus_info, get_bonus_config,
                                            saldo_total_usuario, bonus_ganho_periodo)
                b      = await asyncio.to_thread(bonus_info, uid)
                saldo  = await asyncio.to_thread(saldo_total_usuario, uid)
                ganho  = await asyncio.to_thread(bonus_ganho_periodo, uid)
                _br3, _bpc3 = get_bonus_config()
                falta = b["falta_proximo"]
                prog  = int(((_br3 - falta) / _br3) * 10) if _br3 else 0
                barra = f"`{'█'*prog}{'░'*(10-prog)}` {_br3-falta}/{_br3}"

                def _bsec(i, txt, btn): return {"id": i, "type": 9, "components": [{"id": i+1, "type": 10, "content": txt}], "accessory": btn}
                def _bbtn(i, label, cid, style=2, disabled=False, emoji=None):
                    bb = {"id": i, "type": 2, "style": style, "label": label, "custom_id": cid}
                    if disabled: bb["disabled"] = True
                    if emoji: bb["emoji"] = emoji
                    return bb

                cab = (
                    f"## {e('presente')} Painel de Bônus — {inter.user.display_name}\n"
                    f"-# 💸 Resgate automático ativo — suas salas bônus caem direto no saldo!"
                )
                comps = [{"id": 2, "type": 10, "content": cab}]

                if b["total_comprado"] == 0:
                    comps.append({"id": 3, "type": 10, "content": (
                        f"{OFF} Você ainda **não comprou salas**.\n"
                        f"{DOT} A cada **{_br3} salas** compradas = **+{_bpc3} sala(s) grátis** automáticas!"
                    )})
                else:
                    comps += [
                        _bsec(3,  f"{CART} **Total Comprado**\nSalas compradas acumuladas na faixa.",
                                  _bbtn(5,  f"{b['total_comprado']} salas",   "bn:total",  disabled=True, emoji=_em("carteira"))),
                        _bsec(6,  f"{PRESENTE} **Bônus Recebido**\nSalas grátis já creditadas no saldo.",
                                  _bbtn(8,  f"{b['bonus_resgatado']} salas",  "bn:recebido", disabled=True, emoji=_em("presente"))),
                        {"id": 9, "type": 14, "divider": True, "spacing": 1},
                    ]

                # Linha de ganhos por período (botões disabled — só pra mostrar)
                comps.append({"id": 10, "type": 1, "components": [
                    {"id": 11, "type": 2, "style": 3, "label": f"24h: +{ganho['dia']}",      "custom_id": "bn:dia",    "disabled": True, "emoji": _em("clock")},
                    {"id": 12, "type": 2, "style": 1, "label": f"Semana: +{ganho['semana']}", "custom_id": "bn:semana", "disabled": True, "emoji": _em("calendario")},
                    {"id": 13, "type": 2, "style": 2, "label": f"Total: +{ganho['total']}",   "custom_id": "bn:total2", "disabled": True, "emoji": _em("stats")},
                ]})

                # Progresso pro próximo bônus
                comps.append({"id": 14, "type": 10, "content": (
                    f"{GIFT} **Próximo Bônus**\n{barra}\n"
                    f"-# Faltam **{falta} salas** para **+{_bpc3} sala(s) grátis**!"
                )})

                # Botões interativos
                comps.append({"id": 20, "type": 1, "components": [
                    {"id": 21, "type": 2, "style": 3, "label": "Comprar Salas", "custom_id": "cw:comprar",    "emoji": _em("carteira")},
                    {"id": 22, "type": 2, "style": 2, "label": "Atualizar",     "custom_id": "cw:bonus",      "emoji": _em("refresh")},
                    {"id": 23, "type": 2, "style": 4, "label": "Resetar Faixa", "custom_id": "cw:bonus_reset", "emoji": _em("reloading")},
                ]})

                _bpayload = {"flags": 64|32768, "components": [{"id": 1, "type": 17, "accent_color": 0xFFD700, "components": comps}]}
                _bfw = f"https://discord.com/api/v10/webhooks/{inter.application_id}/{inter.token}?wait=true"
                _bok = False
                try:
                    async with _aiohttp_v2.ClientSession() as _s:
                        async with _s.post(_bfw, json=_bpayload) as _r:
                            _bok = _r.status in (200, 201)
                            if not _bok: _log.warning(f"[bonus v2] {_r.status} {await _r.text()[:200]}")
                except Exception as _ex:
                    _log.warning(f"[bonus v2] {_ex}")

                if not _bok:
                    em = discord.Embed(title=f"{PRESENTE}  Painel de Bônus", color=0xFFD700)
                    em.set_thumbnail(url=inter.user.display_avatar.url)
                    em.add_field(name=f"{STATS} Suas Compras", value=(
                        f"> Total: **{b['total_comprado']}** salas\n"
                        f"> Bônus recebido: **{b['bonus_resgatado']}** salas\n"
                        f"> Ganhou 24h: **+{ganho['dia']}** • Semana: **+{ganho['semana']}**"
                    ), inline=False)
                    em.add_field(name=f"{GIFT} Próximo Bônus", value=f"{barra}\nFaltam **{falta} salas**!", inline=False)
                    await inter.followup.send(embed=em, view=BonusPainelView(b["bonus_disponivel"], saldo, b["total_comprado"]), ephemeral=True)
                return

            if action == "bonus_reset":
                await inter.response.defer(ephemeral=True)
                from utils.database import bonus_info
                b = await asyncio.to_thread(bonus_info, uid)
                if b["total_comprado"] == 0:
                    return await inter.followup.send(embed=_err("Nada para Resetar", f"{DOT} Você não tem dados de bônus para resetar."), ephemeral=True)
                em = discord.Embed(title=f"{RAGE}  Resetar Faixa de Bônus", color=0xFF4444)
                em.description = (
                    f"**⚠️ ATENÇÃO — Esta ação é irreversível!**\n\n"
                    f"{STATS} Seus dados atuais:\n"
                    f"> {CART} Total comprado: **{b['total_comprado']} salas**\n"
                    f"> {PRESENTE} Bônus recebido: **{b['bonus_resgatado']} salas**\n\n"
                    f"{RAGE} Ao confirmar, seu contador volta a **0**.\n"
                    f"{DOT} Tem certeza que deseja **resetar**?"
                )
                return await inter.followup.send(embed=em, view=BonusResetConfirmView(), ephemeral=True)

            if action == "lucro_preco":
                return await inter.response.send_modal(AlterarPrecoLucroModal())

            if action == "lucro_cfg":
                await inter.response.defer(ephemeral=True)
                from utils.database import lucro_config_get
                cfg2 = await asyncio.to_thread(lucro_config_get, uid)
                vps2 = cfg2["valor_por_sala"]
                em = _info("Config Lucro", f"{MONEY} Valor atual: **R$ {vps2:.2f}**/sala" if vps2 > 0 else f"{OFF} Não configurado.")
                return await inter.followup.send(embed=em, view=ConfigLucroView(), ephemeral=True)

            if action == "lucro_org":
                return await inter.response.send_modal(AddOrgModal())

            if action == "lucro_rmorg":
                await inter.response.defer(ephemeral=True)
                from utils.database import lucro_config_get
                cfg3 = await asyncio.to_thread(lucro_config_get, uid)
                orgs3 = cfg3.get("orgs", [])
                if not orgs3:
                    return await inter.followup.send(embed=_err("Sem orgs", f"{OFF} Nenhuma org cadastrada."), ephemeral=True)
                return await inter.followup.send(embed=_warn("Remover Org", f"{DOT} Selecione abaixo:"), view=RemoverOrgSelectView(orgs3), ephemeral=True)

            if action == "outros":
                await inter.response.defer(ephemeral=True)
                uid2 = str(inter.user.id)
                go2 = await asyncio.to_thread(go_config_get, uid2)
                go_lbl2 = f"{go2} min" if go2 > 0 else f"{config.DEFAULT_INICIAR_MINUTOS} min (padrão)"
                from utils.database import senha_config_get
                senha_atual = await asyncio.to_thread(senha_config_get, uid2)
                senha_lbl = f"`{senha_atual}`" if senha_atual else "*(sem senha — sala pública)*"
                def _sep2(i): return {"id": i, "type": 14, "divider": True, "spacing": 1}
                def _sec2(i, txt, btn): return {"id": i, "type": 9, "components": [{"id": i+1, "type": 10, "content": txt}], "accessory": btn}
                def _btn2(i, label, cid, style=2, emoji=None):
                    b = {"id": i, "type": 2, "style": style, "label": label, "custom_id": cid}
                    if emoji: b["emoji"] = emoji
                    return b
                _payload_outros = {
                    "flags": 64 | 32768,
                    "components": [{"id": 1, "type": 17, "components": [
                        _sec2(2,  f"{STATS} **Ver Lucro**\nVeja quanto você ganhou vendendo salas.",
                                  _btn2(4,  "Ver Lucro",   "cw:lucro",    style=1, emoji=_em("stats"))),
                        _sec2(5,  f"{CLOCK} **Config GO**\nInício automático: {go_lbl2}",
                                  _btn2(7,  "Config GO",   "cw:go",       style=2, emoji=_em("settings"))),
                        _sec2(20, f"{SETTINGS} **Config Senha**\nSenha das salas: {senha_lbl}",
                                  _btn2(22, "Config Senha", "cw:senha",   style=2, emoji=_em("settings"))),
                        _sec2(8,  f"{PRESENTE} **Bônus**\nSalas bônus acumuladas por compras.",
                                  _btn2(10, "Bônus",       "cw:bonus",    style=1, emoji=_em("presente"))),
                        _sec2(11, f"{CHANNEL} **Histórico**\nVer histórico de criação das salas.",
                                  _btn2(13, "Histórico",   "cw:historico", style=1, emoji=_em("channel"))),
                        _sec2(14, f"{TOP} **Meta**\nDefina uma meta de salas e acompanhe seu progresso.",
                                  _btn2(16, "Meta",        "cw:meta",     style=3, emoji=_em("top"))),
                    ]}],
                }
                _fw2 = f"https://discord.com/api/v10/webhooks/{inter.application_id}/{inter.token}?wait=true"
                try:
                    async with _aiohttp_v2.ClientSession() as _s:
                        async with _s.post(_fw2, json=_payload_outros) as _r:
                            if _r.status not in (200, 201):
                                _log.warning(f"[cw outros] {_r.status} {await _r.text()[:200]}")
                except Exception as _ex:
                    _log.warning(f"[cw outros] {_ex}")
                return

            if action == "meta":
                from utils.database import meta_progresso
                prog = await asyncio.to_thread(meta_progresso, uid)
                payload_v2, _cor = _meta_payload_v2(inter.user, prog)
                if payload_v2:
                    # Tenta postar via Components V2 (com botões em caixa)
                    await inter.response.defer(ephemeral=True)
                    _fwm = f"https://discord.com/api/v10/webhooks/{inter.application_id}/{inter.token}?wait=true"
                    _v2_ok = False
                    try:
                        async with _aiohttp_v2.ClientSession() as _s:
                            async with _s.post(_fwm, json=payload_v2) as _r:
                                _v2_ok = _r.status in (200, 201)
                                if not _v2_ok:
                                    _log.warning(f"[meta v2] {_r.status} {(await _r.text())[:200]}")
                    except Exception as _ex:
                        _log.warning(f"[meta v2] {_ex}")
                    if _v2_ok:
                        return
                # Fallback: embed clássico
                em = _meta_embed(inter.user, prog)
                if not inter.response.is_done():
                    await inter.response.defer(ephemeral=True)
                return await inter.followup.send(embed=em, view=MetaView(), ephemeral=True)

            if action == "meta_config":
                from utils.database import meta_get
                atual = await asyncio.to_thread(meta_get, uid)
                return await inter.response.send_modal(MetaConfigModal(atual))

            if action == "meta_reset":
                await inter.response.defer(ephemeral=True)
                from utils.database import meta_delete
                await asyncio.to_thread(meta_delete, uid)
                em = _ok("Meta Removida")
                em.description = f"{DOT} Sua meta foi apagada. Você pode criar uma nova quando quiser."
                return await inter.followup.send(embed=em, ephemeral=True)

            if action == "historico":
                await inter.response.defer(ephemeral=True)
                salas_raw = _load_user_historico(uid, limit=10)
                modos_nome = {1: "Normal", 2: "Infinito", 3: "Full Capa", 4: "F 1500 Ouro"}
                em = discord.Embed(title=f"{CHANNEL}  Histórico de Salas", color=0x5865F2)
                if not salas_raw:
                    em.description = f"{OFF} Nenhuma sala criada ainda."
                else:
                    linhas = []
                    for s in salas_raw:
                        modo_n = modos_nome.get(s.get("modo", 0), "?")
                        dt = (s.get("criado_em") or "")[:16].replace("T", " ")
                        sid = s.get("sala_id") or "—"
                        linhas.append(f"> {DOT} `{dt}` — **{modo_n}** — ID: `{sid}`")
                    em.description = "\n".join(linhas)
                return await inter.followup.send(embed=em, ephemeral=True)

            return  # cw:saldo / cw:gastas são disabled

        # ── .cs modo selector ────────────────────────────────────────────────
        if cid.startswith("cs:modo:"):
            try:
                parts = cid.split(":")  # cs : modo : author_id : modo_num
                if len(parts) < 4:
                    return
                author_id_str, modo_str = parts[2], parts[3]
                if str(inter.user.id) != author_id_str:
                    return await inter.response.send_message("Esse menu não é seu.", ephemeral=True)
                modo_num = int(modo_str)
                if modo_num == 3:
                    em = _emb(f"{TOP}  Outros Modos")
                    em.description = f"{DOT} Escolha o modo:"
                    return await inter.response.send_message(embed=em, view=C3ModoView(), ephemeral=True)
                # Normal (1) ou Infinito (2)
                if not await _safe_defer(inter, ephemeral=True):
                    return
                uid = str(inter.user.id)
                k, guild_pagou, gid = await _reservar_sala(inter, modo_num, uid, inter.user.display_name)
                if not guild_pagou and not k:
                    return await inter.followup.send(
                        embed=_err("Sem saldo", "Sem saldo disponível.\nUse **Comprar Salas** no `/c` para adquirir."),
                        ephemeral=True,
                    )
                go = await asyncio.to_thread(go_config_get, uid)
                if go <= 0:
                    go = config.DEFAULT_INICIAR_MINUTOS
                await _criar_sala_flow(
                    inter, modo_num, go,
                    key_row=k, key_ja_consumida=True,
                    guild_pagou=guild_pagou, guild_id=gid,
                    public_channel_id=inter.channel_id,
                )
            except Exception as _cs_ex:
                _log.error(f"[cs:modo] {_cs_ex}", exc_info=True)
                try:
                    _em_err = _err("Erro", f"`{_cs_ex}`")
                    if inter.response.is_done():
                        await inter.followup.send(embed=_em_err, ephemeral=True)
                    else:
                        await inter.response.send_message(embed=_em_err, ephemeral=True)
                except Exception:
                    pass
            return

        if not cid.startswith("sv2:"): return
        parts = cid.split(":", 2)
        if len(parts) < 3: return
        action, pid = parts[1], parts[2]
        sess = _sala_v2_cache.get(pid)
        sala = sess["sala"] if sess else {}
        uid_dono = sess["uid"] if sess else None

        # "ready" é o botão público de GO Player — qualquer um pode clicar
        if action != "ready" and uid_dono and inter.user.id != uid_dono:
            return await inter.response.send_message("❌ Só o criador pode usar.", ephemeral=True)

        if action == "cpid":
            return await inter.response.send_modal(CopiarModal("ID da Sala", str(sala.get("id", "—"))))

        if action == "cpsen":
            return await inter.response.send_modal(CopiarModal("Senha da Sala", str(sala.get("senha", "—"))))

        if action == "copy":
            sid = str(sala.get("id", "—"))
            sen = str(sala.get("senha", "—"))
            return await inter.response.send_message(f"{sid}\n{sen}", ephemeral=True)

        if action == "link":
            link = str(sala.get("link") or "")
            if not link:
                return await inter.response.send_message("❌ Link da sala não disponível.", ephemeral=True)
            return await inter.response.send_message(link, ephemeral=True)

        if action == "start":
            # Se a sala foi criada via prefix, mostra escolha GO Adm / GO Player
            if sess and sess.get("is_prefix"):
                em = _emb(f"{STATS}  Escolha o tipo de GO", 0x5865F2)
                em.description = (
                    f"{DOT} **GO Adm** — inicia a partida imediatamente.\n"
                    f"{DOT} **GO Player** — posta no canal e aguarda **2 players** clicarem em **Estou Pronto** pra iniciar automaticamente."
                )
                return await inter.response.send_message(embed=em, view=GoEscolhaView(pid), ephemeral=True)

            # Fluxo normal (slash): inicia direto
            await inter.response.defer(ephemeral=True)
            d = await api.iniciar_partida(pid)
            if d.get("success"):
                s = d.get("sala") or {}
                em = _ok("Partida Iniciada!")
                em.add_field(name=f"{INFO}  **Nome**",  value=f"> ***{s.get('nome','—')}***", inline=True)
                em.add_field(name=f"{SETTINGS}  **Senha**", value=f"> `{s.get('senha','—')}`", inline=True)
                em.add_field(name=f"{STATS}  **ID**",   value=f"> `{s.get('id','—')}`",   inline=True)
                return await inter.followup.send(embed=em, ephemeral=True)
            return await inter.followup.send(embed=_err(d.get("msg", "Erro")), ephemeral=True)

        if action == "goadm":
            await inter.response.defer(ephemeral=True)
            d = await api.iniciar_partida(pid)
            if d.get("success"):
                s = d.get("sala") or {}
                em = _ok("Partida Iniciada!")
                em.add_field(name=f"{INFO}  **Nome**",  value=f"> ***{s.get('nome','—')}***", inline=True)
                em.add_field(name=f"{SETTINGS}  **Senha**", value=f"> `{s.get('senha','—')}`", inline=True)
                em.add_field(name=f"{STATS}  **ID**",   value=f"> `{s.get('id','—')}`",   inline=True)
                return await inter.followup.send(embed=em, ephemeral=True)
            return await inter.followup.send(embed=_err(d.get("msg", "Erro")), ephemeral=True)

        if action == "goplayer":
            # Posta mensagem pública no canal com botão "Estou Pronto"
            await inter.response.defer(ephemeral=True)
            sid = str(sala.get("id", "—"))
            sen = str(sala.get("senha", "—"))

            texto = (
                f"## {e('megafone')} Sala Pronta — Aguardando Players!\n\n"
                f"**ID** {e('seta')} `{sid}`\n"
                f"**Senha** {e('seta')} `{sen}`\n\n"
                f"-# Os **2 primeiros** jogadores a clicarem em **Estou Pronto** darão GO automático."
            )
            _E_ONLINE = {"id": "1460537720999772264", "name": "online", "animated": True}
            payload = {
                "flags": 32768,
                "components": [{"id": 1, "type": 17, "accent_color": 0x00FF7F, "components": [
                    {"id": 2, "type": 10, "content": texto},
                    {"id": 3, "type": 1, "components": [
                        {"id": 4, "type": 2, "style": 3, "label": "Estou Pronto",
                         "custom_id": f"sv2:ready:{pid}", "emoji": _E_ONLINE},
                    ]},
                ]}],
            }
            url = f"https://discord.com/api/v10/channels/{inter.channel.id}/messages"
            headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
            try:
                async with _aiohttp_v2.ClientSession() as _s:
                    async with _s.post(url, headers=headers, json=payload) as _r:
                        if _r.status not in (200, 201):
                            body = await _r.text()
                            _log.warning(f"[goplayer post] {_r.status} {body[:200]}")
                            return await inter.followup.send(
                                embed=_err("Erro ao postar", f"{OFF} Não consegui enviar a mensagem pública."),
                                ephemeral=True,
                            )
            except Exception as _ex:
                _log.warning(f"[goplayer post] {_ex}")
                return await inter.followup.send(embed=_err("Erro", f"```{_ex}```"), ephemeral=True)

            # Inicializa o registro de players prontos
            if sess is not None:
                sess.setdefault("ready", [])
            return await inter.followup.send(
                embed=_ok("Aguardando Players", f"{DOT} Mensagem postada no canal. Aguardando 2 jogadores."),
                ephemeral=True,
            )

        if action == "ready":
            # Qualquer usuário pode clicar; precisamos de 2 cliques distintos
            if sess is None:
                return await inter.response.send_message("❌ Sala expirada.", ephemeral=True)
            ready_list = sess.setdefault("ready", [])
            already = any(r["id"] == inter.user.id for r in ready_list)
            if already:
                return await inter.response.send_message(
                    f"⏳ {inter.user.mention}, você já marcou presença. Aguardando mais um jogador.",
                    ephemeral=True,
                )
            ready_list.append({"id": inter.user.id, "name": inter.user.display_name})

            if len(ready_list) < 2:
                restantes = 2 - len(ready_list)
                return await inter.response.send_message(
                    f"✅ {inter.user.mention} está pronto! Faltam **{restantes}** jogador(es).",
                    ephemeral=False,
                )

            # 2 prontos — dispara GO automático
            await inter.response.defer()
            d = await api.iniciar_partida(pid)
            p1 = ready_list[0]
            p2 = ready_list[1]
            if d.get("success"):
                s = d.get("sala") or {}
                conteudo = (
                    f"## {e('megafone')} Sala Iniciada Automaticamente!\n\n"
                    f"**Jogadores prontos:**\n"
                    f"> <@{p1['id']}>\n"
                    f"> <@{p2['id']}>\n\n"
                    f"**ID** {e('seta')} `{s.get('id','—')}`\n"
                    f"**Senha** {e('seta')} `{s.get('senha','—')}`"
                )
                try:
                    await inter.channel.send(conteudo)
                except Exception as _ex:
                    _log.warning(f"[ready go] {_ex}")
                # Desabilita o botão ready na mensagem original
                try:
                    _payload_disabled = {
                        "flags": 32768,
                        "components": [{"id": 1, "type": 17, "accent_color": 0x00FF7F, "components": [
                            {"id": 2, "type": 10, "content": f"## {e('megafone')} Sala já iniciada!\n\n-# Os players **{p1['name']}** e **{p2['name']}** deram GO."},
                        ]}],
                    }
                    await inter.edit_original_response(**{"content": None})
                    _patch_url = f"https://discord.com/api/v10/channels/{inter.channel.id}/messages/{inter.message.id}"
                    _headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
                    async with _aiohttp_v2.ClientSession() as _s:
                        async with _s.patch(_patch_url, headers=_headers, json=_payload_disabled) as _r:
                            if _r.status not in (200, 204):
                                _log.warning(f"[ready patch] {_r.status}")
                except Exception as _ex:
                    _log.warning(f"[ready patch] {_ex}")
                return
            else:
                await inter.followup.send(embed=_err(d.get("msg", "Erro ao iniciar")), ephemeral=True)
                # Permite tentar de novo: remove o último
                ready_list.pop()
                return

        if action == "kick":
            return await inter.response.send_modal(ExpulsarModal(pid))

        if action == "status":
            await inter.response.defer(ephemeral=True)
            d = await api.info_sala(pid)
            s = d.get("sala") or sala
            m = modo_info(sess["modo"] if sess else 0)
            st_code = d.get("status", 0)
            # Mapa de status → (emoji_id, name, animated, label)
            _st_map = {
                2: ("1487111032752181361", "loading",   True,  "Criando…"),
                3: ("1460537720999772264", "online",    True,  "Aguardando"),
                4: ("1483205696077037821", "za_houst1", False, "Iniciada"),
            }
            _st_ei, _st_en, _st_ea, _st_lb = _st_map.get(st_code, ("1483691698373660753", "DiscordOff", True, "—"))

            equipes = s.get("equipes", [])
            total_jg = sum(len(eq.get("jogadores", [])) for eq in equipes)
            if total_jg == 0:
                jg_txt = "-# Sala vazia — nenhum jogador ainda"
            else:
                linhas = []
                for eq in equipes:
                    for jg in eq.get("jogadores", []):
                        nick = jg.get("nickname") or jg.get("name") or "?"
                        jid  = jg.get("id") or jg.get("accountId") or "?"
                        linhas.append(f"**{nick}** — `{jid}`")
                jg_txt = "\n".join(linhas[:20])
                if total_jg > 20:
                    jg_txt += f"\n-# … e mais {total_jg - 20} jogadores"

            ia_txt = d.get("inicio_automatico", "") or ""
            _comps = [
                {"id": 2, "type": 10, "content": f"<:za_houst1:1483205696077037821> **Sala — {s.get('nome','—')}**"},
                {"id": 3, "type": 9,
                 "components": [{"id": 4, "type": 10, "content": "**ID**"}],
                 "accessory": {"id": 5, "type": 2, "style": 2, "label": str(s.get("id","—")), "custom_id": "st:id", "disabled": True,
                               "emoji": {"id": "1489005986541736057", "name": "swordbattle", "animated": False}}},
                {"id": 6, "type": 9,
                 "components": [{"id": 7, "type": 10, "content": "**Senha**"}],
                 "accessory": {"id": 8, "type": 2, "style": 2, "label": str(s.get("senha","—")), "custom_id": "st:sen", "disabled": True,
                               "emoji": {"id": "1489005986541736057", "name": "swordbattle", "animated": False}}},
                {"id": 9, "type": 9,
                 "components": [{"id": 10, "type": 10, "content": "**Modo**"}],
                 "accessory": {"id": 11, "type": 2, "style": 2, "label": m["nome"], "custom_id": "st:modo", "disabled": True,
                               "emoji": {"id": "1455278816325931051", "name": "modo_1", "animated": False}}},
                # Status — bolinha não-clicável
                {"id": 12, "type": 9,
                 "components": [{"id": 13, "type": 10, "content": "**Status**"}],
                 "accessory": {"id": 14, "type": 2, "style": 2, "label": _st_lb, "custom_id": "st:status", "disabled": True,
                               "emoji": {"id": _st_ei, "name": _st_en, "animated": _st_ea}}},
            ]
            if ia_txt:
                _comps.append({"id": 15, "type": 10, "content": f"-# GO em **{ia_txt}**"})
            if jg_txt:
                _comps.append({"id": 16, "type": 10, "content": f"**Jogadores ({total_jg})**\n{jg_txt}"})

            _st_fw = f"https://discord.com/api/v10/webhooks/{inter.application_id}/{inter.token}?wait=true"
            _st_ok = False
            try:
                async with _aiohttp_v2.ClientSession() as _s2:
                    async with _s2.post(_st_fw, json={
                        "flags": 64 | 32768,
                        "components": [{"id": 1, "type": 17, "accent_color": 0x2B2D31, "components": _comps}],
                    }) as _r:
                        _st_ok = _r.status in (200, 201)
                        if not _st_ok:
                            _log.warning(f"[sv2 status v2] {_r.status} {await _r.text()[:200]}")
            except Exception as _ex:
                _log.warning(f"[sv2 status v2] {_ex}")

            if not _st_ok:
                status_map = {2: f"{AWAITING} Criando…", 3: f"{ON} Aguardando", 4: f"{PLAY} Iniciada"}
                em = discord.Embed(title=f"{BOT}  Sala — {s.get('nome','—')}", color=0x2B2D31)
                em.add_field(name=f"{SALA_ID}  ID",      value=f"`{s.get('id','—')}`",    inline=True)
                em.add_field(name=f"{SALA_SENHA}  Senha", value=f"`{s.get('senha','—')}`", inline=True)
                em.add_field(name=f"{MOBILE}  Modo",      value=m["nome"],                 inline=True)
                em.add_field(name=f"{VISION}  Status",    value=status_map.get(st_code, "—"), inline=True)
                em.add_field(name=f"{ROLES}  Jogadores ({total_jg})", value=jg_txt or "> *Sala vazia*", inline=False)
                await inter.followup.send(embed=em, ephemeral=True)
            return


async def setup(bot):
    import logging as _sl
    _sl_log = _sl.getLogger("salasff.setup")
    for _CogCls, _cog_name in [
        (MainCog, "MainCog"),
        (AdminToolsCog, "AdminToolsCog"),
        (InfConfigCog, "InfConfigCog"),
        (SalaV2Cog, "SalaV2Cog"),
    ]:
        try:
            await bot.add_cog(_CogCls(bot))
            _sl_log.info(f"[setup] {_cog_name} OK")
        except Exception as _ex_cog:
            _sl_log.error(f"[setup] {_cog_name} FALHOU: {_ex_cog}", exc_info=True)

    # Registra views persistentes (custom_id fixo) para os botões sobreviverem ao restart
    for _ViewCls in [PainelView, SalaPublicaView]:
        try:
            bot.add_view(_ViewCls())
        except Exception as _ex_v:
            _sl_log.warning(f"[setup] add_view {_ViewCls.__name__}: {_ex_v}")