import discord
from discord import app_commands
from discord.ext import commands
from views.admin_view import AdminMenuView
from utils.scope import admin_guilds, is_admin_guild


class PainelCompras(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="painelcompras",
        description="Abre o menu de gerenciamento do painel de compras.",
    )
    @admin_guilds()
    @app_commands.default_permissions(administrator=True)
    async def painelcompras(self, interaction: discord.Interaction):
        if not is_admin_guild(interaction):
            return await interaction.response.send_message(
                "Comando disponível apenas no servidor admin.", ephemeral=True
            )
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "Você precisa ser **Administrador** para usar este comando.",
                ephemeral=True,
            )

        view = AdminMenuView(self.bot)
        await interaction.response.send_message(
            "**Painel de Compras — Menu Admin**\n"
            "Escolha uma opção abaixo:",
            view=view,
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(PainelCompras(bot))
