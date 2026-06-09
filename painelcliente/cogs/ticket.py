"""
/ticket — publica o painel de abrir ticket no canal atual.

Disponível em QUALQUER servidor (guild-install), restrito a administradores.
Use em servidores de fora para soltar o painel de suporte sem precisar
rodar o .setar (que é destrutivo).
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.emojis import AWAITING, SWORD
from utils.scope import guild_app
from views.ticket_view import PainelTicketView


class TicketCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="ticket",
        description="Publica o painel de abrir ticket neste canal.",
    )
    @guild_app()
    @app_commands.default_permissions(administrator=True)
    async def ticket(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(
            interaction.channel, discord.TextChannel
        ):
            return await interaction.response.send_message(
                f"{AWAITING} Use este comando em um canal de texto do servidor.",
                ephemeral=True,
            )
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "Apenas administradores podem publicar o painel.", ephemeral=True
            )

        try:
            await interaction.channel.send(view=PainelTicketView())
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"{AWAITING} Não tenho permissão pra enviar mensagem neste canal.",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"{AWAITING} Erro ao publicar painel: `{e}`", ephemeral=True
            )

        await interaction.response.send_message(
            f"{SWORD} Painel de ticket publicado neste canal.", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(TicketCog(bot))
