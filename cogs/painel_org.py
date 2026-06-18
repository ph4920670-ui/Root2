# cogs/painel_org.py — Comando +painel para orgs de revenda

import asyncio
import discord
from discord.ext import commands
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_BR = ZoneInfo("America/Sao_Paulo")


class PainelOrgView(discord.ui.View):
    """View com botão Atualizar para o painel da org."""

    def __init__(self, guild_id: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(
        label="Atualizar",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="painelorg:atualizar",
    )
    async def atualizar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        embed = await _build_painel_embed(self.guild_id)
        if embed is None:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="❌ Org não encontrada",
                    description="Este servidor não está cadastrado como org ativa.",
                    color=0xF23F43,
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=embed, view=PainelOrgView(self.guild_id))


async def _build_painel_embed(guild_id: str) -> discord.Embed | None:
    """Constrói o embed do painel da org. Retorna None se a org não existir."""
    from utils.database_orgs import org_get, org_salas_semana, org_salas_mes
    from utils.database import guild_config_get

    org = await asyncio.to_thread(org_get, guild_id)
    if not org or not org.get("ativo"):
        return None

    salas_semana = await asyncio.to_thread(org_salas_semana, guild_id)
    salas_mes    = await asyncio.to_thread(org_salas_mes,    guild_id)

    guild_cfg  = await asyncio.to_thread(guild_config_get, guild_id)
    preco_sala = float(guild_cfg.get("preco_sala", 1.5))

    fat_semana = salas_semana * preco_sala
    fat_mes    = salas_mes    * preco_sala

    porcentagem    = float(org.get("porcentagem", 70))
    saldo_acumulado = float(org.get("saldo_acumulado", 0.0))

    agora = datetime.now(_BR).strftime("%d/%m/%Y %H:%M")

    em = discord.Embed(
        title="📊  Painel da Org",
        color=0x5865F2,
    )
    em.description = f"> **{org.get('nome') or 'Org'}** — Atualizado em `{agora}`"

    em.add_field(
        name="📅  Esta Semana",
        value=(
            f"> **{salas_semana}** salas criadas\n"
            f"> Faturamento: **R$ {fat_semana:.2f}**"
        ),
        inline=True,
    )
    em.add_field(
        name="📆  Este Mês",
        value=(
            f"> **{salas_mes}** salas criadas\n"
            f"> Faturamento: **R$ {fat_mes:.2f}**"
        ),
        inline=True,
    )
    em.add_field(
        name="💰  Saldo Acumulado",
        value=(
            f"> **R$ {saldo_acumulado:.2f}**\n"
            f"> ({porcentagem:.0f}% do faturamento)"
        ),
        inline=False,
    )
    em.add_field(
        name="📈  Totais Históricos",
        value=(
            f"> **{org.get('salas_total', 0)}** salas\n"
            f"> **R$ {float(org.get('faturamento_total', 0)):.2f}** faturado"
        ),
        inline=True,
    )
    em.set_footer(text="SalasFF • Clique em Atualizar para dados mais recentes")
    return em


class PainelOrgCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="orgpainel")
    async def cmd_painel(self, ctx: commands.Context):
        """Mostra o painel público da org (apenas em servidores que são orgs ativas)."""
        if ctx.guild is None:
            return

        guild_id = str(ctx.guild.id)

        from utils.database_orgs import org_get, command_enabled

        # Verifica se o comando está habilitado neste servidor
        enabled = await asyncio.to_thread(command_enabled, guild_id, "painel")
        if not enabled:
            return  # Silenciosamente ignora se desabilitado

        # Verifica se este servidor é uma org ativa
        org = await asyncio.to_thread(org_get, guild_id)
        if not org or not org.get("ativo"):
            return  # Silenciosamente ignora se não for org ativa

        embed = await _build_painel_embed(guild_id)
        if embed is None:
            return

        await ctx.send(embed=embed, view=PainelOrgView(guild_id))


async def setup(bot: commands.Bot):
    await bot.add_cog(PainelOrgCog(bot))
