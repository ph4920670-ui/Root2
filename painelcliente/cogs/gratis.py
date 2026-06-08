"""
Comandos e tasks dos canais grátis (sala-teste, sala-bonus, ranking).

/publicar-gratis — admin publica os 3 painéis nos canais certos
Task diária às 00:00 BRT — premia top 3 do dia anterior e edita o painel
"""

import asyncio
import discord
from datetime import datetime, timezone, timedelta
from discord import app_commands
from discord.ext import commands, tasks

from utils import database
from utils.emojis import AWAITING, CLOUD, SWORD
from utils.scope import admin_guilds, is_admin_guild
from views.gratis_view import (
    PainelTesteView,
    PainelBonusView,
    montar_view_ranking,
    PainelRankingFixoView,
    RANKING_PREMIOS,
)


BRT = timezone(timedelta(hours=-3))


def _agora_brt() -> datetime:
    return datetime.now(timezone.utc).astimezone(BRT)


def _ontem_brt_str() -> str:
    return (_agora_brt() - timedelta(days=1)).strftime("%Y-%m-%d")


def _achar_canal(guild: discord.Guild, slug_lower: str):
    return discord.utils.get(guild.text_channels, name=slug_lower)


class GratisCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tarefa_ranking.start()

    def cog_unload(self):
        self.tarefa_ranking.cancel()

    # ═══════════════════════════════════════════════════════════════
    # COMANDO ADMIN — publica os 3 painéis
    # ═══════════════════════════════════════════════════════════════

    @app_commands.command(
        name="publicar-gratis",
        description="Publica os painéis dos canais sala-teste, sala-bonus e ranking.",
    )
    @admin_guilds()
    @app_commands.default_permissions(administrator=True)
    async def publicar_gratis(self, interaction: discord.Interaction):
        if not is_admin_guild(interaction):
            return await interaction.response.send_message(
                "Comando disponível apenas no servidor admin.", ephemeral=True
            )
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "Apenas administradores.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        publicados = []

        # Sala teste
        canal_teste = _achar_canal(guild, "🎁｜sala-teste")
        if canal_teste:
            try:
                await canal_teste.send(view=PainelTesteView())
                publicados.append(canal_teste.mention)
            except Exception as e:
                print(f"⚠️ Erro publicando sala-teste: {e}")

        # Sala bônus
        canal_bonus = _achar_canal(guild, "🎁｜sala-bonus")
        if canal_bonus:
            try:
                await canal_bonus.send(view=PainelBonusView())
                publicados.append(canal_bonus.mention)
            except Exception as e:
                print(f"⚠️ Erro publicando sala-bonus: {e}")

        # Ranking — V2 unificado
        canal_ranking = _achar_canal(guild, "🏆｜ranking")
        if canal_ranking:
            try:
                await canal_ranking.send(view=PainelRankingFixoView())
                publicados.append(canal_ranking.mention)
            except Exception as e:
                print(f"⚠️ Erro publicando ranking: {e}")

        if not publicados:
            return await interaction.followup.send(
                f"{AWAITING} Nenhum dos canais foi encontrado. "
                f"Rode `.setar confirmar` no servidor primeiro pra criar os canais.",
                ephemeral=True,
            )

        await interaction.followup.send(
            f"{SWORD} Painéis publicados em: " + ", ".join(publicados),
            ephemeral=True,
        )

    # ═══════════════════════════════════════════════════════════════
    # TASK DIÁRIA — roda a cada minuto e premia quando dá 00:00 BRT
    # ═══════════════════════════════════════════════════════════════

    @tasks.loop(minutes=1)
    async def tarefa_ranking(self):
        agora = _agora_brt()

        # Só roda no minuto 0 da hora 0 (00:00 BRT)
        if agora.hour != 0 or agora.minute != 0:
            return

        dia_premiar = (agora - timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"🏆 Rodando premiação do dia {dia_premiar} (BRT)...")

        guild_ids = await database.listar_guilds_com_ranking(dia_premiar)
        for guild_id in guild_ids:
            # Não premiar 2x
            if await database.ja_premiou_dia(guild_id, dia_premiar):
                continue

            try:
                await self._premiar_guild(guild_id, dia_premiar)
            except Exception as e:
                print(f"⚠️ Erro ao premiar guild {guild_id}: {e}")

        print("🏆 Premiação concluída.")

    @tarefa_ranking.before_loop
    async def antes_da_task(self):
        await self.bot.wait_until_ready()

    async def _premiar_guild(self, guild_id: int, dia: str):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        top = await database.top_ranking_dia(guild_id, dia=dia, limit=3)
        if not top:
            return

        premiados = []
        for i, entry in enumerate(top[:3], start=1):
            user_id = entry["user_id"]
            premio = RANKING_PREMIOS.get(i, 0)
            if premio <= 0:
                continue

            # Credita o prêmio no saldo do usuário
            await database.adicionar_saldo(guild_id, user_id, premio)
            await database.registrar_premiacao(guild_id, dia, user_id, i, premio)
            premiados.append((i, user_id, entry["salas_no_dia"], premio))

        # Log no canal de logs do ranking (configurável /botconfig)
        from utils import logs as _logs
        try:
            await _logs.log_ranking(self.bot, guild_id=guild_id, dia=dia, premiados=premiados)
        except Exception as e:
            print(f"⚠️ Erro log ranking: {e}")

        # Anuncia no canal de ranking
        canal_ranking = _achar_canal(guild, "🏆｜ranking")
        if canal_ranking and premiados:
            linhas = []
            for pos, user_id, qtd, premio in premiados:
                membro = guild.get_member(user_id)
                nome = membro.mention if membro else f"<@{user_id}>"
                linhas.append(
                    f"{SWORD} **{pos}º lugar** — {nome} "
                    f"com `{qtd}` salas → recebeu `{premio}` salas!"
                )

            from discord.ui import LayoutView, Container, TextDisplay, Separator
            view = LayoutView(timeout=None)
            container = Container(accent_colour=discord.Colour.from_str("#FFD700"))
            container.add_item(TextDisplay(
                f"# {SWORD} Premiação do dia `{dia}`"
            ))
            container.add_item(Separator())
            container.add_item(TextDisplay("\n".join(linhas)))
            container.add_item(TextDisplay(
                f"-# {CLOUD} O ranking foi resetado · Boa sorte hoje!"
            ))
            view.add_item(container)

            try:
                await canal_ranking.send(view=view)

                # Publica o painel novo (zerado pro dia atual)
                novo_top = await database.top_ranking_dia(guild.id)
                await canal_ranking.send(view=montar_view_ranking(novo_top, guild))
            except Exception as e:
                print(f"⚠️ Erro ao anunciar premiação: {e}")


async def setup(bot):
    await bot.add_cog(GratisCog(bot))
