"""
cogs/sugestao.py — sistema de sugestões com votação.

Toda mensagem postada no canal 💬｜sujestao é:
  1. Apagada
  2. Re-publicada como embed estilizado com o autor
  3. Recebe botões ✅ / ❌ com contador

Os votos são salvos em memória (resetam quando o bot reinicia).
Pra persistência, dá pra trocar pro banco depois — me fala se quiser.
"""

import discord
from discord.ext import commands

from utils.emojis import AWAITING, SWORD, button_emoji


CANAL_SUGESTOES = "💬｜sujestao"

# Cache em memória: {message_id: {"sim": set[user_id], "nao": set[user_id]}}
# Resetam ao reiniciar — votos persistentes precisariam ir pro banco
_votos: dict[int, dict[str, set[int]]] = {}


def _embed_sugestao(autor: discord.Member, conteudo: str) -> discord.Embed:
    e = discord.Embed(
        description=conteudo,
        colour=discord.Colour.from_str("#5865F2"),
    )
    e.set_author(
        name=autor.display_name,
        icon_url=autor.display_avatar.url,
    )
    e.set_footer(text="Vote nos botões abaixo")
    return e


class SugestaoView(discord.ui.View):
    """Botões de votação persistentes."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="0",
        style=discord.ButtonStyle.success,
        emoji=button_emoji("trophy"),
        custom_id="sugestao:sim",
    )
    async def sim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _processar_voto(interaction, button, voto="sim", view=self)

    @discord.ui.button(
        label="0",
        style=discord.ButtonStyle.danger,
        emoji=button_emoji("awaiting"),
        custom_id="sugestao:nao",
    )
    async def nao(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _processar_voto(interaction, button, voto="nao", view=self)


async def _processar_voto(
    interaction: discord.Interaction,
    button: discord.ui.Button,
    voto: str,
    view: SugestaoView,
):
    msg_id = interaction.message.id
    user_id = interaction.user.id

    # Inicia tracking dessa mensagem se ainda não existir
    if msg_id not in _votos:
        _votos[msg_id] = {"sim": set(), "nao": set()}

    sim = _votos[msg_id]["sim"]
    nao = _votos[msg_id]["nao"]

    # Toggle: se já votou neste mesmo, remove; senão, troca/adiciona
    if voto == "sim":
        if user_id in sim:
            sim.discard(user_id)
            msg = f"Voto ✅ removido."
        else:
            sim.add(user_id)
            nao.discard(user_id)  # remove voto contrário se tinha
            msg = f"Você votou ✅"
    else:
        if user_id in nao:
            nao.discard(user_id)
            msg = f"Voto ❌ removido."
        else:
            nao.add(user_id)
            sim.discard(user_id)
            msg = f"Você votou ❌"

    # Atualiza labels dos botões com contador
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            if child.custom_id == "sugestao:sim":
                child.label = str(len(sim))
            elif child.custom_id == "sugestao:nao":
                child.label = str(len(nao))

    try:
        await interaction.response.edit_message(view=view)
        await interaction.followup.send(msg, ephemeral=True)
    except discord.HTTPException:
        await interaction.response.send_message(msg, ephemeral=True)


class SugestaoCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignora bots e DMs
        if message.author.bot or message.guild is None:
            return
        # Filtra pelo canal certo
        if message.channel.name != CANAL_SUGESTOES:
            return
        # Ignora se for comando
        if message.content.startswith((".", "!", "?", "/")):
            return

        conteudo = (message.content or "").strip()
        # Inclui menção a anexo se tiver
        if message.attachments:
            anexos = "\n".join(a.url for a in message.attachments[:3])
            conteudo = f"{conteudo}\n\n{anexos}".strip()

        if not conteudo:
            return  # vazio (só sticker, por ex.)

        # Apaga a mensagem original
        try:
            await message.delete()
        except discord.Forbidden:
            return  # sem permissão pra apagar — desiste
        except discord.NotFound:
            pass

        # Posta embed + botões de votação
        try:
            await message.channel.send(
                embed=_embed_sugestao(message.author, conteudo),
                view=SugestaoView(),
            )
        except Exception as e:
            print(f"⚠️ Erro publicando sugestão: {e}")


async def setup(bot):
    await bot.add_cog(SugestaoCog(bot))
