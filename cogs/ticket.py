# cogs/ticket.py — Sistema de Tickets com transcript no ftickets.discloud.app
#
# Fluxo:
#   /ticket (admin)
#     └─ painel ephemeral com:
#         • Enviar Painel         → posta no canal painel com botão "Suporte"
#         • Canal de Logs         → seleciona canal de logs (transcripts)
#         • Cargo de Staff        → seleciona cargo que vê os tickets
#
#   Botão "Suporte" (qualquer user)
#     └─ cria THREAD PRIVADA no canal
#         • adiciona user + cargo staff
#         • msg boas-vindas com botão "Fechar Ticket"
#
#   Botão "Fechar Ticket"
#     └─ coleta mensagens → gera HTML → POST /upload no site
#         • manda link no canal de logs
#         • arquiva thread

import json
import os
import io
import uuid
import asyncio
import logging
import html as html_lib
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.emojis import PE, e as _e

_log = logging.getLogger("salasff.tickets")
_BR = ZoneInfo("America/Sao_Paulo")

# ── Components V2 ────────────────────────────────────────────────────────────
FLAG_V2        = 1 << 15   # 32768
FLAG_EPHEMERAL = 1 << 6    # 64

# Tickets em processo de fechamento (evita fechar 2x ao clicar rápido)
_fechando: set[int] = set()


def _emj(key: str) -> dict:
    """Emoji do bot no formato dict que a API Components V2 espera."""
    em = PE[key]
    return {"id": str(em.id), "name": em.name, "animated": bool(em.animated)}


async def _post_v2_channel(channel_id: int, payload: dict) -> bool:
    """POST de uma mensagem Components V2 num canal/thread via API REST."""
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url, headers=headers, json=payload) as r:
                ok = r.status in (200, 201)
                if not ok:
                    _log.warning(f"[v2 post] canal={channel_id} status={r.status} body={(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.warning(f"[v2 post] {ex}")
        return False


async def _followup_v2(inter: discord.Interaction, payload: dict, ephemeral: bool = True) -> bool:
    """Envia followup Components V2 numa interação já deferida."""
    flags = payload.get("flags", FLAG_V2)
    if ephemeral:
        flags |= FLAG_EPHEMERAL
    payload = {**payload, "flags": flags}
    url = f"https://discord.com/api/v10/webhooks/{inter.application_id}/{inter.token}?wait=true"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url, json=payload) as r:
                ok = r.status in (200, 201)
                if not ok:
                    _log.warning(f"[v2 followup] status={r.status} body={(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.warning(f"[v2 followup] {ex}")
        return False


# ═══════════════════════════════════════════
#  Persistência simples em JSON (por guild)
#  Formato:
#  {
#    "<guild_id>": {
#       "canal_logs": 123,        # canal onde o transcript é postado
#       "cargo_staff": 456        # cargo que vê os tickets
#    }
#  }
# ═══════════════════════════════════════════
def _carregar_cfg() -> dict:
    try:
        if os.path.exists(config.TICKETS_CONFIG_PATH):
            with open(config.TICKETS_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as ex:
        _log.warning(f"[cfg load] {ex}")
    return {}


def _salvar_cfg(d: dict):
    try:
        with open(config.TICKETS_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
    except Exception as ex:
        _log.warning(f"[cfg save] {ex}")


def _get_guild_cfg(guild_id: int) -> dict:
    cfg = _carregar_cfg()
    return cfg.get(str(guild_id), {})


def _set_guild_cfg(guild_id: int, chave: str, valor):
    cfg = _carregar_cfg()
    gid = str(guild_id)
    if gid not in cfg:
        cfg[gid] = {}
    cfg[gid][chave] = valor
    _salvar_cfg(cfg)


def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


# ═══════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════
def _emb(desc: str, cor: int = config.COR_INFO) -> discord.Embed:
    return discord.Embed(description=desc, color=cor)


def _err(titulo: str, desc: str = "") -> discord.Embed:
    em = discord.Embed(title=f"❌  {titulo}", description=desc, color=config.COR_ERRO)
    return em


# ═══════════════════════════════════════════
#  Transcript — coleta mensagens do thread e gera HTML
# ═══════════════════════════════════════════
def _esc(t: str) -> str:
    """Escape HTML."""
    return html_lib.escape(t or "", quote=True)


async def _coletar_mensagens(thread: discord.Thread):
    """Retorna lista de mensagens do thread em ordem cronológica."""
    msgs = []
    async for m in thread.history(limit=None, oldest_first=True):
        msgs.append(m)
    return msgs


def _render_transcript_html(thread: discord.Thread, msgs: list, fechado_por: discord.User) -> str:
    """Gera HTML estilizado tipo Discord com as mensagens do ticket."""
    nome_guild = _esc(thread.guild.name if thread.guild else "?")
    nome_canal = _esc(thread.name)
    data_agora = datetime.now(_BR).strftime("%d/%m/%Y às %H:%M")
    fechado = _esc(f"{fechado_por.display_name} ({fechado_por.id})")

    # Monta cada mensagem
    msgs_html = []
    for m in msgs:
        autor = _esc(m.author.display_name)
        avatar = m.author.display_avatar.url if m.author.display_avatar else ""
        ts = m.created_at.astimezone(_BR).strftime("%d/%m/%Y %H:%M")
        cor_nome = "#FFFFFF" if m.author.bot else "#5865F2"

        # Conteúdo
        conteudo = _esc(m.content).replace("\n", "<br>") if m.content else ""

        # Embeds → resumo simples
        embeds_html = ""
        for em in m.embeds:
            titulo = _esc(em.title or "")
            desc = _esc(em.description or "").replace("\n", "<br>")
            cor = f"#{em.color.value:06x}" if em.color else "#5865F2"
            if titulo or desc:
                titulo_tag = f'<div class="embed-title">{titulo}</div>' if titulo else ''
                desc_tag = f'<div class="embed-desc">{desc}</div>' if desc else ''
                embeds_html += (
                    f'<div class="embed" style="border-left-color:{cor}">'
                    f'{titulo_tag}'
                    f'{desc_tag}'
                    f'</div>'
                )

        # Anexos → links
        anexos_html = ""
        for a in m.attachments:
            anexos_html += f'<div class="anexo"><a href="{_esc(a.url)}" target="_blank">📎 {_esc(a.filename)}</a></div>'

        msgs_html.append(f"""
        <div class="msg">
            <img class="avatar" src="{_esc(avatar)}" alt="">
            <div class="body">
                <div class="head">
                    <span class="nome" style="color:{cor_nome}">{autor}</span>
                    <span class="ts">{ts}</span>
                </div>
                {f'<div class="texto">{conteudo}</div>' if conteudo else ''}
                {embeds_html}
                {anexos_html}
            </div>
        </div>
        """)

    total = len(msgs)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transcript — {nome_canal}</title>
<style>
    * {{ box-sizing: border-box; }}
    body {{
        background: #2b2d31;
        color: #dcddde;
        font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
        margin: 0;
        padding: 0;
    }}
    .container {{ max-width: 900px; margin: 0 auto; padding: 1.5rem; }}
    .header {{
        background: #1e1f22;
        padding: 1.5rem;
        border-radius: 12px;
        margin-bottom: 1rem;
        border: 1px solid #3f4147;
    }}
    .header h1 {{ color: #57f287; margin: 0 0 0.5rem 0; font-size: 1.4rem; }}
    .header .info {{ color: #b5bac1; font-size: 0.9rem; line-height: 1.6; }}
    .header .info b {{ color: #fff; }}

    .msgs {{ background: #313338; border-radius: 12px; padding: 1rem; }}
    .msg {{ display: flex; gap: 1rem; padding: 0.5rem 0; margin-bottom: 0.5rem; }}
    .avatar {{ width: 40px; height: 40px; border-radius: 50%; flex-shrink: 0; }}
    .body {{ flex: 1; min-width: 0; }}
    .head {{ display: flex; align-items: baseline; gap: 0.5rem; margin-bottom: 0.2rem; }}
    .nome {{ font-weight: 600; }}
    .ts {{ color: #949ba4; font-size: 0.75rem; }}
    .texto {{ color: #dbdee1; word-wrap: break-word; }}

    .embed {{
        background: #2b2d31;
        border-left: 4px solid #5865F2;
        border-radius: 4px;
        padding: 0.5rem 0.75rem;
        margin-top: 0.5rem;
        max-width: 500px;
    }}
    .embed-title {{ color: #fff; font-weight: 600; margin-bottom: 0.3rem; }}
    .embed-desc {{ color: #dbdee1; font-size: 0.9rem; }}

    .anexo {{ margin-top: 0.3rem; }}
    .anexo a {{ color: #00a8fc; text-decoration: none; font-size: 0.9rem; }}
    .anexo a:hover {{ text-decoration: underline; }}

    .footer {{
        text-align: center;
        color: #6d6f78;
        font-size: 0.8rem;
        margin-top: 1rem;
        padding: 1rem;
    }}
</style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📋 Transcript de Ticket</h1>
            <div class="info">
                <b>Servidor:</b> {nome_guild}<br>
                <b>Canal:</b> #{nome_canal}<br>
                <b>Fechado por:</b> {fechado}<br>
                <b>Data:</b> {data_agora} (BRT)<br>
                <b>Total de mensagens:</b> {total}
            </div>
        </div>
        <div class="msgs">
            {"".join(msgs_html) if msgs_html else '<div style="text-align:center; padding:2rem; color:#949ba4;">Nenhuma mensagem no ticket.</div>'}
        </div>
        <div class="footer">F Applications • Gerado automaticamente</div>
    </div>
</body>
</html>"""


async def _upload_transcript(transcript_id: str, html: str, ticket_numero: str, guild_name: str, fechado_por_nome: str) -> str | None:
    """Faz POST /upload no site ftickets. Retorna URL ou None."""
    url = f"{config.FTICKETS_URL.rstrip('/')}/upload"
    payload = {
        "id": transcript_id,
        "html": html,
        "ticket_numero": ticket_numero,
        "guild_name": guild_name,
        "fechado_por": fechado_por_nome,
        "data": datetime.now(_BR).isoformat(),
    }
    headers = {"X-Auth-Token": config.FTICKETS_UPLOAD_TOKEN}

    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=payload, headers=headers) as r:
                if r.status == 200:
                    data = await r.json()
                    return data.get("url")
                body = (await r.text())[:200]
                _log.warning(f"[upload] status={r.status} body={body}")
                return None
    except Exception as ex:
        _log.warning(f"[upload] erro: {ex}")
        return None


# ═══════════════════════════════════════════
#  Views
# ═══════════════════════════════════════════
def _painel_publico_payload() -> dict:
    """Painel público (Components V2) com o botão de abrir ticket dentro do container."""
    return {
        "flags": FLAG_V2,
        "components": [{
            "id": 1, "type": 17, "accent_color": config.COR_INFO,
            "components": [
                {"id": 2, "type": 10, "content": (
                    f"## {_e('channel')}  Central de Suporte\n"
                    f"Precisa de ajuda? Abra um ticket privado e fale com a equipe."
                )},
                {"id": 3, "type": 14, "divider": True, "spacing": 1},
                {"id": 4, "type": 10, "content": (
                    f"{_e('click')}  Um tópico **privado** é criado só pra você e o staff\n"
                    f"{_e('vision')}  Descreva sua dúvida com detalhes\n"
                    f"{_e('on')}  Respondemos o mais rápido possível"
                )},
                {"id": 5, "type": 1, "components": [
                    {"type": 2, "style": 1, "label": "Abrir Ticket",
                     "custom_id": "ticket:abrir", "emoji": _emj("channel")},
                ]},
            ],
        }],
    }


def _boas_vindas_payload(user: discord.abc.User, mencao_staff: str) -> dict:
    """Mensagem de boas-vindas do ticket (V2) com o botão Fechar dentro do container."""
    head = f"{user.mention} {mencao_staff}".strip()
    return {
        "flags": FLAG_V2,
        "allowed_mentions": {"parse": ["users", "roles"]},
        "content": head or None,
        "components": [{
            "id": 1, "type": 17, "accent_color": config.COR_SUCESSO,
            "components": [
                {"id": 2, "type": 9,
                 "components": [{"id": 3, "type": 10, "content": (
                     f"## {_e('channel')}  Ticket Aberto\n"
                     f"Olá **{user.display_name}**, bem-vindo ao suporte!"
                 )}],
                 "accessory": {"id": 4, "type": 11, "media": {"url": user.display_avatar.url}}},
                {"id": 5, "type": 14, "divider": True, "spacing": 1},
                {"id": 6, "type": 10, "content": (
                    f"{_e('vision')}  Descreva sua dúvida ou problema aqui — a equipe já foi avisada.\n"
                    f"{_e('off')}  Quando terminar, clique em **Fechar Ticket** abaixo."
                )},
                {"id": 7, "type": 1, "components": [
                    {"type": 2, "style": 4, "label": "Fechar Ticket",
                     "custom_id": "ticket:fechar", "emoji": _emj("off")},
                ]},
            ],
        }],
    }


class PainelTicketPublicoView(discord.ui.View):
    """View persistente — botão Abrir Ticket do painel público."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Abrir Ticket", emoji=PE["channel"], style=discord.ButtonStyle.primary, custom_id="ticket:abrir")
    async def abrir(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not inter.guild or not isinstance(inter.channel, discord.TextChannel):
            return await inter.response.send_message(embed=_err("Erro", "Canal inválido."), ephemeral=True)

        await inter.response.defer(ephemeral=True)

        cfg = _get_guild_cfg(inter.guild.id)
        cargo_staff_id = cfg.get("cargo_staff")

        # Verifica se já existe ticket aberto desse user
        for t in inter.channel.threads:
            if t.name.startswith(f"ticket-{inter.user.id}") and not t.archived:
                return await inter.followup.send(
                    embed=_emb(f"{_e('awaiting')}  Você já tem um ticket aberto: {t.mention}", config.COR_AVISO),
                    ephemeral=True
                )

        try:
            thread = await inter.channel.create_thread(
                name=f"ticket-{inter.user.id}-{uuid.uuid4().hex[:6]}",
                type=discord.ChannelType.private_thread,
                invitable=False,
                reason=f"Ticket aberto por {inter.user}",
            )
        except discord.Forbidden:
            return await inter.followup.send(
                embed=_err("Sem permissão", "O bot precisa de **Criar Threads Privadas** neste canal."),
                ephemeral=True
            )
        except Exception as ex:
            _log.warning(f"[abrir] erro: {ex}")
            return await inter.followup.send(embed=_err("Erro ao criar ticket", f"`{ex}`"), ephemeral=True)

        try:
            await thread.add_user(inter.user)
        except Exception:
            pass

        mencao_staff = ""
        if cargo_staff_id:
            cargo = inter.guild.get_role(cargo_staff_id)
            if cargo:
                mencao_staff = cargo.mention
                for m in cargo.members:
                    try:
                        await thread.add_user(m)
                    except Exception:
                        pass

        # Boas-vindas em Components V2 (botão Fechar dentro do container)
        ok = await _post_v2_channel(thread.id, _boas_vindas_payload(inter.user, mencao_staff))
        if not ok:
            # Fallback simples se o V2 falhar (nunca deixa o ticket sem botão)
            try:
                em = discord.Embed(title="🎫  Ticket Aberto",
                                   description=f"{inter.user.mention}, descreva sua dúvida. Clique em **Fechar Ticket** quando terminar.",
                                   color=config.COR_SUCESSO)
                await thread.send(content=f"{inter.user.mention} {mencao_staff}".strip(),
                                  embed=em, view=FecharTicketView())
            except Exception as ex:
                _log.warning(f"[abrir send fallback] {ex}")

        await inter.followup.send(
            embed=_emb(f"{_e('on')}  Seu ticket foi aberto: {thread.mention}", config.COR_SUCESSO),
            ephemeral=True
        )


class FecharTicketView(discord.ui.View):
    """View persistente — botão Fechar Ticket."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fechar Ticket", emoji=PE["off"], style=discord.ButtonStyle.danger, custom_id="ticket:fechar")
    async def fechar(self, inter: discord.Interaction, btn: discord.ui.Button):
        await _fechar_ticket(inter)


async def _fechar_ticket(inter: discord.Interaction):
    """Lógica de fechamento — robusta contra clique duplo e thread já arquivada."""
    if not isinstance(inter.channel, discord.Thread):
        return await inter.response.send_message(embed=_err("Canal inválido", "Esse botão só funciona dentro de um ticket."), ephemeral=True)

    thread: discord.Thread = inter.channel

    # Já está sendo fechado? (clique duplo)
    if thread.id in _fechando:
        return await inter.response.send_message(
            embed=_emb(f"{_e('awaiting')}  Esse ticket já está sendo fechado…", config.COR_AVISO),
            ephemeral=True,
        )
    # Já arquivado?
    if thread.archived:
        return await inter.response.send_message(
            embed=_emb(f"{_e('off')}  Esse ticket já está fechado.", config.COR_AVISO),
            ephemeral=True,
        )

    _fechando.add(thread.id)
    try:
        await inter.response.defer(ephemeral=True, thinking=True)

        try:
            msgs = await _coletar_mensagens(thread)
        except Exception as ex:
            _log.warning(f"[fechar coleta] {ex}")
            msgs = []

        link = None
        html = None
        try:
            html = _render_transcript_html(thread, msgs, inter.user)
            transcript_id = f"{thread.id}-{uuid.uuid4().hex[:8]}"
            guild_name = thread.guild.name if thread.guild else "?"
            link = await _upload_transcript(transcript_id, html, thread.name, guild_name, inter.user.display_name)
        except Exception as ex:
            _log.warning(f"[fechar transcript] {ex}")

        # Log no canal configurado
        cfg = _get_guild_cfg(thread.guild.id) if thread.guild else {}
        canal_logs_id = cfg.get("canal_logs")
        canal_logs = thread.guild.get_channel(canal_logs_id) if (thread.guild and canal_logs_id) else None

        if canal_logs:
            try:
                _comps = [
                    {"id": 2, "type": 10, "content": (
                        f"## {_e('off')}  Ticket Fechado\n"
                        f"{_e('channel')}  **Ticket:** `{thread.name}`"
                    )},
                    {"id": 3, "type": 14, "divider": True, "spacing": 1},
                    {"id": 4, "type": 10, "content": (
                        f"{_e('roles')}  **Fechado por:** {inter.user.mention}\n"
                        f"{_e('stats')}  **Mensagens:** `{len(msgs)}`\n"
                        f"{_e('clock')}  {datetime.now(_BR).strftime('%d/%m/%Y às %H:%M')} (BRT)"
                    )},
                ]
                if link:
                    _comps.append({"id": 5, "type": 1, "components": [
                        {"type": 2, "style": 5, "label": "Ver Transcript", "url": link, "emoji": _emj("vision")},
                    ]})
                await _post_v2_channel(canal_logs.id, {
                    "flags": FLAG_V2, "components": [{"id": 1, "type": 17, "accent_color": config.COR_AVISO, "components": _comps}],
                })
                # HTML como backup
                if html:
                    await canal_logs.send(file=discord.File(io.BytesIO(html.encode("utf-8")), filename=f"{thread.name}.html"))
            except Exception as ex:
                _log.warning(f"[fechar log] {ex}")

        # Resposta ao usuário
        if link:
            await inter.followup.send(embed=_emb(f"{_e('on')}  Ticket fechado!\n{_e('vision')}  [Ver transcript]({link})", config.COR_SUCESSO), ephemeral=True)
        else:
            await inter.followup.send(embed=_emb(f"{_e('on')}  Ticket fechado. (transcript indisponível)", config.COR_AVISO), ephemeral=True)

        # Mensagem final + arquiva
        try:
            await thread.send(embed=_emb(f"{_e('off')}  Ticket fechado por {inter.user.mention}. Arquivando…", config.COR_AVISO))
            await asyncio.sleep(1)
            await thread.edit(archived=True, locked=True, reason=f"Fechado por {inter.user}")
        except discord.Forbidden:
            _log.warning("[fechar arquivar] sem permissão Gerenciar Threads")
            try:
                await thread.send(embed=_err("Não consegui arquivar", "Falta a permissão **Gerenciar Tópicos** ao bot. O transcript já foi salvo."))
            except Exception:
                pass
        except Exception as ex:
            _log.warning(f"[fechar arquivar] {ex}")
    finally:
        _fechando.discard(thread.id)


# ═══════════════════════════════════════════
#  Painel admin (do /ticket)
# ═══════════════════════════════════════════
class PainelAdminView(discord.ui.View):
    """Painel ephemeral do /ticket — configuração."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Enviar Painel", emoji="📤", style=discord.ButtonStyle.success, row=0)
    async def enviar(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        await inter.response.defer(ephemeral=True)

        ok = await _post_v2_channel(inter.channel.id, _painel_publico_payload())
        if not ok:
            # Fallback discord.py se o V2 falhar
            try:
                em = discord.Embed(title="🎫  Central de Suporte",
                                   description="Clique em **Abrir Ticket** para falar com a equipe.",
                                   color=config.COR_INFO)
                await inter.channel.send(embed=em, view=PainelTicketPublicoView())
            except Exception as ex:
                return await inter.followup.send(embed=_err("Erro ao enviar.", f"`{ex}`"), ephemeral=True)

        await inter.followup.send(embed=_emb(f"{_e('on')}  Painel enviado.", config.COR_SUCESSO), ephemeral=True)

    @discord.ui.button(label="Canal de Logs", emoji="📁", style=discord.ButtonStyle.primary, row=0)
    async def canal_logs(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        view = _SelectCanalView(inter.user.id)
        await inter.response.send_message(
            embed=_emb("📁  Selecione o canal onde os transcripts serão enviados:"),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Cargo de Staff", emoji="👥", style=discord.ButtonStyle.primary, row=0)
    async def cargo_staff(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        view = _SelectCargoView(inter.user.id)
        await inter.response.send_message(
            embed=_emb("👥  Selecione o cargo de staff que verá os tickets:"),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Ver Configuração", emoji="ℹ️", style=discord.ButtonStyle.secondary, row=1)
    async def ver_cfg(self, inter: discord.Interaction, btn: discord.ui.Button):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        cfg = _get_guild_cfg(inter.guild.id)
        canal_id = cfg.get("canal_logs")
        cargo_id = cfg.get("cargo_staff")

        canal = inter.guild.get_channel(canal_id) if canal_id else None
        cargo = inter.guild.get_role(cargo_id) if cargo_id else None

        em = discord.Embed(title="⚙️  Configuração Atual", color=config.COR_INFO)
        em.add_field(
            name="📁  Canal de Logs",
            value=canal.mention if canal else "*Não configurado*",
            inline=False
        )
        em.add_field(
            name="👥  Cargo de Staff",
            value=cargo.mention if cargo else "*Não configurado*",
            inline=False
        )
        em.set_footer(text=f"F Applications • Guild {inter.guild.id}")

        await inter.response.send_message(embed=em, ephemeral=True)


class _SelectCanalView(discord.ui.View):
    def __init__(self, dono_id: int):
        super().__init__(timeout=120)
        self.dono_id = dono_id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Escolha o canal de logs...",
        min_values=1,
        max_values=1,
    )
    async def select_canal(self, inter: discord.Interaction, select: discord.ui.ChannelSelect):
        if inter.user.id != self.dono_id:
            return await inter.response.send_message(embed=_err("Apenas quem abriu o painel."), ephemeral=True)

        canal = select.values[0]
        _set_guild_cfg(inter.guild.id, "canal_logs", canal.id)
        await inter.response.edit_message(
            embed=_emb(f"✅  Canal de logs definido: {canal.mention}", config.COR_SUCESSO),
            view=None,
        )


class _SelectCargoView(discord.ui.View):
    def __init__(self, dono_id: int):
        super().__init__(timeout=120)
        self.dono_id = dono_id

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Escolha o cargo de staff...",
        min_values=1,
        max_values=1,
    )
    async def select_cargo(self, inter: discord.Interaction, select: discord.ui.RoleSelect):
        if inter.user.id != self.dono_id:
            return await inter.response.send_message(embed=_err("Apenas quem abriu o painel."), ephemeral=True)

        cargo = select.values[0]
        _set_guild_cfg(inter.guild.id, "cargo_staff", cargo.id)
        await inter.response.edit_message(
            embed=_emb(f"✅  Cargo de staff definido: {cargo.mention}", config.COR_SUCESSO),
            view=None,
        )


# ═══════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════
class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="ticket", description="[ADMIN] Painel de configuração de tickets.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_ticket(self, inter: discord.Interaction):
        if not is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        em = discord.Embed(
            title=f"{_e('channel')}  Painel de Tickets",
            description=(
                "Configure o sistema de tickets:\n\n"
                f"{_e('enviar')} **Enviar Painel** — posta o painel com botão **Abrir Ticket**.\n"
                f"{_e('channel')} **Canal de Logs** — onde os transcripts vão ao fechar.\n"
                f"{_e('roles')} **Cargo de Staff** — quem é adicionado nos tickets.\n"
                f"{_e('info')} **Ver Configuração** — mostra a config atual."
            ),
            color=config.COR_INFO,
        )
        em.set_footer(text="F Applications • /ticket")

        await inter.response.send_message(embed=em, view=PainelAdminView(), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
    # Views persistentes — botões sobrevivem a restart (corrige "não consigo fechar")
    for _ViewCls in (PainelTicketPublicoView, FecharTicketView):
        try:
            bot.add_view(_ViewCls())
        except Exception as ex:
            _log.warning(f"[setup] add_view {_ViewCls.__name__}: {ex}")
