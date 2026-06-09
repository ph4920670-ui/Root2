"""
views/ticket_view.py — Sistema de tickets em thread privada.

Fluxo:
- Usuário clica em "Abrir Ticket" no canal 🎫｜abrir-ticket
- Bot cria THREAD PRIVADA no próprio canal
- Adiciona o user + cargo Suporte
- Posta painel V2 "TICKET SUPORTE" com 2 botões:
  • Finalizar Ticket (verde) → só staff arquiva
  • Sair Ticket (vermelho) → qualquer um sai da thread
"""

import io
import html as _html

import discord
from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow, Section

from utils import database
from utils.emojis import AWAITING, CLOUD, PRESENTE, SWORD, VISION, button_emoji


# ═══════════════════════════════════════════════════════════════════
# Log de tickets
# ═══════════════════════════════════════════════════════════════════

async def _enviar_log_ticket(guild: discord.Guild, embed: discord.Embed,
                             arquivo: discord.File | None = None):
    """Envia um embed (e opcionalmente um arquivo) pro canal de logs."""
    try:
        config = await database.get_guild_config(guild.id)
        canal_id = config.get("canal_logs_tickets_id")
        if not canal_id:
            return
        canal = guild.get_channel(canal_id)
        if canal is None:
            return
        if arquivo is not None:
            await canal.send(embed=embed, file=arquivo)
        else:
            await canal.send(embed=embed)
    except Exception:
        pass


async def _gerar_transcript(thread: discord.Thread) -> discord.File | None:
    """Gera um HTML com todas as mensagens da thread. Retorna um discord.File."""
    try:
        linhas = []
        async for msg in thread.history(limit=None, oldest_first=True):
            autor = _html.escape(str(msg.author))
            ts = msg.created_at.strftime("%d/%m/%Y %H:%M")
            conteudo = _html.escape(msg.content or "")
            conteudo = conteudo.replace("\n", "<br>")

            anexos = ""
            for a in msg.attachments:
                anexos += (f'<div class="anexo">📎 <a href="{a.url}">'
                           f'{_html.escape(a.filename)}</a></div>')
            # embeds viram nota simples
            if msg.embeds and not conteudo:
                conteudo = "<i>[embed]</i>"

            bot_badge = ' <span class="bot">BOT</span>' if msg.author.bot else ""
            linhas.append(
                f'<div class="msg">'
                f'<div class="head"><span class="autor">{autor}</span>{bot_badge}'
                f'<span class="ts">{ts}</span></div>'
                f'<div class="corpo">{conteudo}{anexos}</div>'
                f'</div>'
            )

        if not linhas:
            linhas.append('<div class="msg"><div class="corpo">'
                           '<i>Sem mensagens.</i></div></div>')

        nome_thread = _html.escape(thread.name)
        documento = f"""<!DOCTYPE html>
<html lang="pt-br"><head><meta charset="utf-8">
<title>Transcript — {nome_thread}</title>
<style>
  body{{background:#1e1f22;color:#dbdee1;font-family:'gg sans',Arial,sans-serif;
       margin:0;padding:24px;}}
  .topo{{border-bottom:1px solid #3f4147;padding-bottom:14px;margin-bottom:18px;}}
  .topo h1{{margin:0;font-size:20px;color:#fff;}}
  .topo p{{margin:4px 0 0;color:#949ba4;font-size:13px;}}
  .msg{{padding:8px 0;border-bottom:1px solid #2b2d31;}}
  .head{{margin-bottom:3px;}}
  .autor{{color:#f2f3f5;font-weight:600;}}
  .bot{{background:#5865f2;color:#fff;font-size:10px;padding:1px 5px;
        border-radius:3px;margin-left:6px;vertical-align:middle;}}
  .ts{{color:#949ba4;font-size:12px;margin-left:8px;}}
  .corpo{{color:#dbdee1;font-size:14px;line-height:1.4;}}
  .anexo{{margin-top:4px;}}
  .anexo a{{color:#00a8fc;text-decoration:none;}}
</style></head><body>
<div class="topo">
  <h1>🎫 {nome_thread}</h1>
  <p>{len(linhas)} mensagem(ns) • gerado em {discord.utils.utcnow().strftime("%d/%m/%Y %H:%M")} UTC</p>
</div>
{''.join(linhas)}
</body></html>"""

        buffer = io.BytesIO(documento.encode("utf-8"))
        return discord.File(buffer, filename=f"transcript-{thread.name}.html")
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
# Painel publicado no canal 🎫｜abrir-ticket
# ═══════════════════════════════════════════════════════════════════

class PainelTicketView(LayoutView):
    """Persistente — painel principal com botão de abrir ticket."""

    def __init__(self):
        super().__init__(timeout=None)
        container = Container(accent_colour=discord.Colour.from_str("#5865F2"))

        container.add_item(TextDisplay(f"# {VISION} Suporte — Abrir Ticket"))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"Precisa de ajuda? Clique no botão ao lado e uma **thread privada** "
            f"será criada só entre você e a equipe de **Suporte**."
        ))
        container.add_item(TextDisplay(
            f"{CLOUD} Atendimento humano — descreva sua dúvida na thread.\n"
            f"{AWAITING} Não abra tickets duplicados, por favor."
        ))
        container.add_item(Separator())

        sec = Section(accessory=AbrirTicketButton())
        sec.add_item(TextDisplay(f"{SWORD} **Abrir Ticket** — cria thread privada"))
        container.add_item(sec)

        container.add_item(TextDisplay(
            f"-# Cada usuário pode ter 1 ticket aberto por vez"
        ))
        self.add_item(container)


class AbrirTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Abrir Ticket",
            style=discord.ButtonStyle.success,
            emoji=button_emoji("cloud"),
            custom_id="ticket:abrir",
        )

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message(
                f"{AWAITING} Só funciona em canais de texto do servidor.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Resolve cargo Suporte
        config = await database.get_guild_config(guild.id)
        cargo_id = config.get("cargo_suporte_id")
        cargo_suporte = guild.get_role(cargo_id) if cargo_id else None
        if cargo_suporte is None:
            cargo_suporte = discord.utils.find(
                lambda r: r.name.lower() == "suporte", guild.roles
            )

        # Procura thread já aberta do usuário neste canal
        canal_painel: discord.TextChannel = interaction.channel
        nome_thread = f"ticket-{interaction.user.name}".lower()[:90]

        existente = discord.utils.find(
            lambda t: isinstance(t, discord.Thread)
                      and t.parent_id == canal_painel.id
                      and t.name == nome_thread
                      and not t.archived,
            guild.threads,
        )
        if existente:
            return await interaction.followup.send(
                f"{AWAITING} Você já tem um ticket aberto: {existente.mention}",
                ephemeral=True,
            )

        # Cria thread PRIVADA
        try:
            thread = await canal_painel.create_thread(
                name=nome_thread,
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=1440,  # 24h
                reason=f"Ticket de {interaction.user}",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                f"{AWAITING} Não tenho permissão pra criar thread privada. "
                f"O servidor precisa ter nível de boost que libere threads privadas, "
                f"ou conceda **Manage Threads** ao bot.",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"{AWAITING} Erro ao criar thread: `{e}`",
                ephemeral=True,
            )

        # Monta a menção: usuário + membros do suporte.
        # Mencionar na 1ª mensagem adiciona todos à thread privada SEM gerar
        # as mensagens de sistema "Fulano adicionou Beltrano ao tópico".
        mencoes = [interaction.user.mention]
        if cargo_suporte is not None:
            for membro in cargo_suporte.members:
                if membro.id != interaction.user.id:
                    mencoes.append(membro.mention)

        # 1) Mensagem de menção — adiciona os membros silenciosamente.
        try:
            msg_mencao = await thread.send(content=" ".join(mencoes))
            # Apaga logo em seguida: os membros já entraram, o ping já notificou.
            await msg_mencao.delete()
        except Exception:
            # Fallback: se não der pra mencionar, adiciona manualmente.
            try:
                await thread.add_user(interaction.user)
            except Exception:
                pass
            if cargo_suporte is not None:
                for membro in cargo_suporte.members:
                    try:
                        await thread.add_user(membro)
                    except Exception:
                        pass

        # 2) Painel V2 do ticket
        await thread.send(view=TicketSuporteView())

        # 3) Log de abertura
        embed_log = discord.Embed(
            title=f"{SWORD} Ticket aberto",
            colour=discord.Colour.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed_log.add_field(name="Usuário",
                            value=f"{interaction.user.mention}\n`{interaction.user}`",
                            inline=True)
        embed_log.add_field(name="Thread", value=thread.mention, inline=True)
        embed_log.add_field(name="Canal", value=canal_painel.mention, inline=True)
        embed_log.set_footer(text=f"ID: {interaction.user.id}")
        await _enviar_log_ticket(guild, embed_log)

        await interaction.followup.send(
            f"{SWORD} Ticket criado: {thread.mention}",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# Painel dentro do ticket (na thread)
# ═══════════════════════════════════════════════════════════════════

class TicketSuporteView(LayoutView):
    """Painel V2 com botões Finalizar + Sair Ticket."""

    def __init__(self):
        super().__init__(timeout=None)
        container = Container(accent_colour=discord.Colour.from_str("#2b2d31"))

        container.add_item(TextDisplay(f"# {SWORD} TICKET SUPORTE"))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"⚡ Bem-vindo ao canal oficial de atendimento.\n"
            f"↳ Você solicitou atendimento na opção **Suporte**.\n\n"
            f"{AWAITING} Todos os responsáveis já estão cientes do atendimento — "
            f"descreva com o máximo de detalhes possíveis o motivo do contato "
            f"para agilizar o atendimento.\n\n"
            f"⚠️ Evite chamar alguém via DM, basta aguardar que alguém já irá "
            f"lhe atender..."
        ))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"📋 Caso deseje sair do atendimento, use o botão **Sair Ticket**."
        ))
        container.add_item(Separator())

        sec_finalizar = Section(accessory=FinalizarTicketButton())
        sec_finalizar.add_item(TextDisplay(
            f"✅ **Finalizar Ticket** — encerra o atendimento (staff)"
        ))
        container.add_item(sec_finalizar)

        sec_sair = Section(accessory=SairTicketButton())
        sec_sair.add_item(TextDisplay(
            f"🚪 **Sair Ticket** — você sai da thread"
        ))
        container.add_item(sec_sair)

        self.add_item(container)


class FinalizarTicketButton(discord.ui.Button):
    """Só staff (cargo Suporte ou admin) pode finalizar — arquiva a thread."""

    def __init__(self):
        super().__init__(
            label="Finalizar Ticket",
            style=discord.ButtonStyle.success,
            emoji=button_emoji("trophy"),
            custom_id="ticket:finalizar",
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            return await interaction.response.send_message(
                f"{AWAITING} Botão só funciona dentro de um ticket.",
                ephemeral=True,
            )

        # Verifica permissão (admin ou cargo Suporte)
        if not _eh_staff(interaction):
            return await interaction.response.send_message(
                f"{AWAITING} Apenas a equipe de **Suporte** pode finalizar tickets.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            f"{SWORD} Ticket finalizado por {interaction.user.mention}. "
            f"Gerando transcript e arquivando...",
            ephemeral=False,
        )

        thread = interaction.channel

        # Gera o transcript ANTES de arquivar (depois não dá pra ler o histórico).
        transcript = await _gerar_transcript(thread)

        # Log de finalização (com transcript anexado, se gerou)
        embed_log = discord.Embed(
            title=f"{AWAITING} Ticket finalizado",
            colour=discord.Colour.red(),
            timestamp=discord.utils.utcnow(),
        )
        embed_log.add_field(name="Finalizado por",
                            value=f"{interaction.user.mention}\n`{interaction.user}`",
                            inline=True)
        embed_log.add_field(name="Thread", value=f"`{thread.name}`", inline=True)
        if transcript is not None:
            embed_log.add_field(
                name="Transcript",
                value="📄 Histórico completo anexado abaixo.",
                inline=False,
            )
        embed_log.set_footer(text=f"ID: {interaction.user.id}")
        await _enviar_log_ticket(interaction.guild, embed_log, arquivo=transcript)

        try:
            await interaction.channel.edit(archived=True, locked=True,
                                           reason=f"Finalizado por {interaction.user}")
        except Exception as e:
            try:
                await interaction.followup.send(
                    f"{AWAITING} Erro ao arquivar: `{e}`",
                    ephemeral=True,
                )
            except Exception:
                pass


class SairTicketButton(discord.ui.Button):
    """Qualquer um pode sair — remove a si próprio da thread."""

    def __init__(self):
        super().__init__(
            label="Sair Ticket",
            style=discord.ButtonStyle.danger,
            emoji=button_emoji("awaiting"),
            custom_id="ticket:sair",
        )

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            return await interaction.response.send_message(
                f"{AWAITING} Botão só funciona dentro de um ticket.",
                ephemeral=True,
            )
        # Responde primeiro pra evitar timeout
        await interaction.response.send_message(
            f"{CLOUD} {interaction.user.mention} saiu do ticket.",
            ephemeral=False,
        )
        try:
            await interaction.channel.remove_user(interaction.user)
        except Exception as e:
            try:
                await interaction.followup.send(
                    f"{AWAITING} Erro ao remover: `{e}`",
                    ephemeral=True,
                )
            except Exception:
                pass


def _eh_staff(interaction: discord.Interaction) -> bool:
    """True se o usuário é admin ou tem o cargo Suporte."""
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator:
        return True
    nomes_cargo = {r.name.lower() for r in member.roles}
    return "suporte" in nomes_cargo
