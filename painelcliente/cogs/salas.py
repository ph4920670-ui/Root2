"""
Comandos:
  /c    → carteira (saldo do usuário)
  /cs   → escolhe modo (select com todos) e cria sala
  /c1   → cria 4X4 SEM CARREGAMENTO direto (sala única)
  /c2   → cria X1 GELO INF em modo infinito
"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands

from utils import database
from utils import salasff
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, SWORD, button_emoji
from utils.scope import user_app
from views.carteira_view import montar_carteira
from views.sala_view import (
    montar_painel_sala,
    acompanhar_sala,
    PainelSalaView,
)
from discord.ui import Container, TextDisplay


# ─── Modos fixos pros comandos rápidos ────────────────────────────
SALAID_C1 = "190739084104966408"   # 4X4 SEM CARREGAMENTO
MODO_C1   = "4X4 SEM CARREGAMENTO"

SALAID_C2 = "153348828322603634"   # X1 GELO INF
MODO_C2   = "X1 GELO INF"


def _guild_id_efetivo(interaction: discord.Interaction) -> int:
    """
    Em User Install (DM ou outro server sem o bot), guild_id pode ser None.
    Usamos 0 como "guild virtual" pra manter o saldo único por usuário,
    independente de onde ele use o comando.
    """
    return interaction.guild_id or 0


# ═══════════════════════════════════════════════════════════════════
# Helper — cria sala, desconta saldo, envia painel, inicia polling
# ═══════════════════════════════════════════════════════════════════

async def criar_e_anunciar_sala(
    bot: discord.Client,
    interaction: discord.Interaction,
    salaid: str,
    modo_nome: str,
    infinito: bool,
):
    guild_id = _guild_id_efetivo(interaction)
    user_id = interaction.user.id

    # 1. Checa saldo
    saldo = await database.get_saldo(guild_id, user_id)
    if saldo < 1:
        view = PainelSalaView()
        c = Container(accent_colour=discord.Colour.red())
        c.add_item(TextDisplay(
            f"# {DOLLAR} Saldo insuficiente\n"
            f"Você precisa de pelo menos 1 sala. Compre pelo `/painelcompras`."
        ))
        view.add_item(c)
        return await interaction.followup.send(view=view, ephemeral=True)

    # 2. Desconta atomicamente
    if not await database.consumir_saldo(guild_id, user_id, 1):
        view = PainelSalaView()
        c = Container(accent_colour=discord.Colour.red())
        c.add_item(TextDisplay(f"# {AWAITING} Erro ao consumir saldo. Tente novamente."))
        view.add_item(c)
        return await interaction.followup.send(view=view, ephemeral=True)

    # 2.1. Log de saldo
    from utils import logs as _logs
    novo_saldo_apos_consumo = await database.get_saldo(guild_id, user_id)
    try:
        await _logs.log_saldo(
            bot, guild_id=guild_id, user_id=user_id,
            motivo=f"Criou sala: {modo_nome}",
            variacao=-1, novo_saldo=novo_saldo_apos_consumo,
        )
    except Exception as e:
        print(f"⚠️ Erro log saldo: {e}")

    # 3. Chama API SalasFF
    resp = await salasff.criar_sala(salaid=salaid)
    if not resp or not resp.get("success"):
        # Devolve saldo
        await database.adicionar_saldo(guild_id, user_id, 1)
        view = PainelSalaView()
        c = Container(accent_colour=discord.Colour.red())
        msg = (resp or {}).get("msg") \
              or (resp or {}).get("error") \
              or (resp or {}).get("message") \
              or "API SalasFF indisponível."
        c.add_item(TextDisplay(
            f"# {AWAITING} Falha ao criar sala\n"
            f"{CLOUD} `{msg}`\n\n"
            f"Seu saldo foi devolvido."
        ))
        view.add_item(c)
        return await interaction.followup.send(view=view, ephemeral=True)

    pedidoid = resp.get("pedidoid")
    if not pedidoid:
        await database.adicionar_saldo(guild_id, user_id, 1)
        return await interaction.followup.send(
            f"{AWAITING} Erro: API não retornou `pedidoid`. Saldo devolvido.",
            ephemeral=True,
        )

    # 4. Monta painel (sempre ephemeral)
    # ID e SENHA não são enviados aqui — o usuário pega clicando em "Copiar ID/Senha" no painel.
    view = montar_painel_sala(resp, modo_nome, user_id, infinito)
    try:
        await interaction.followup.send(view=view, ephemeral=True)
    except Exception as e:
        await database.adicionar_saldo(guild_id, user_id, 1)
        return await interaction.followup.send(
            f"{AWAITING} Erro ao enviar painel: `{e}`. Saldo devolvido.",
            ephemeral=True,
        )

    # Como painel é ephemeral, não dá pra editar pelo polling.
    # Registramos channel_id/message_id como 0 e pulamos a fase de update.
    pseudo_channel_id = 0
    pseudo_message_id = 0

    # 5. Registra no banco
    await database.registrar_sala(
        pedidoid=pedidoid,
        guild_id=guild_id,
        user_id=user_id,
        channel_id=pseudo_channel_id,
        message_id=pseudo_message_id,
        salaid=salaid,
        infinito=infinito,
    )

    # 5.1. Stats: total criadas, bônus, ranking diário
    stats_result = await database.registrar_sala_criada(guild_id, user_id)

    # 6. Inicia polling em background (só se tem mensagem no canal)
    if pseudo_message_id:
        asyncio.create_task(acompanhar_sala(
            bot=bot,
            pedidoid=pedidoid,
            salaid=salaid,
            modo_nome=modo_nome,
            channel_id=pseudo_channel_id,
            message_id=pseudo_message_id,
            user_id=user_id,
            guild_id=guild_id,
            infinito=infinito,
        ))

    # 7. Confirma pro user (efêmero)
    novo_saldo = await database.get_saldo(guild_id, user_id)
    msg_extra = ""
    if stats_result.get("ganhou_bonus"):
        msg_extra = (
            f"\n{SWORD} **BÔNUS!** Você completou um múltiplo de 10 salas criadas "
            f"e ganhou `{stats_result['bonus_ganho']}` sala(s) bônus!\n"
            f"{CLOUD} Resgate em `#sala-bonus`."
        )
    await interaction.followup.send(
        f"{SWORD} Sala criada! Saldo restante: `{novo_saldo}` sala(s).\n"
        f"{CHANNEL} Total criadas: `{stats_result['novo_total']}`.{msg_extra}",
        ephemeral=True,
    )


# ═══════════════════════════════════════════════════════════════════
# Select de modos pra /cs
# ═══════════════════════════════════════════════════════════════════

class ModoSelect(discord.ui.Select):
    def __init__(self, bot, infinito: bool):
        self.bot = bot
        self.infinito = infinito
        options = [
            discord.SelectOption(
                label=m["nome"][:100],
                value=m["salaid"],
                emoji=button_emoji("box"),
            )
            for m in salasff.MODOS_FIXOS
        ]
        super().__init__(
            placeholder="Escolha o modo da sala...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        salaid = self.values[0]
        modo = salasff.get_modo_por_id(salaid)
        modo_nome = modo["nome"] if modo else "Modo"

        await interaction.response.defer(ephemeral=True, thinking=True)
        await criar_e_anunciar_sala(
            bot=self.bot,
            interaction=interaction,
            salaid=salaid,
            modo_nome=modo_nome,
            infinito=self.infinito,
        )


class ModoSelectView(discord.ui.View):
    def __init__(self, bot, infinito: bool = False):
        super().__init__(timeout=120)
        self.add_item(ModoSelect(bot, infinito))


# ═══════════════════════════════════════════════════════════════════
# COG
# ═══════════════════════════════════════════════════════════════════

class SalasCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─── /c ── carteira ─────────────────────────────────────────
    @app_commands.command(
        name="c",
        description="Mostra sua carteira (saldo de salas).",
    )
    @user_app()
    async def c(self, interaction: discord.Interaction):
        guild_id = _guild_id_efetivo(interaction)
        saldo = await database.get_saldo(guild_id, interaction.user.id)
        saldo_global = await salasff.saldo_global()
        view = montar_carteira(interaction.user, saldo, saldo_global)
        await interaction.response.send_message(view=view, ephemeral=True)

    # ─── /cs ── escolhe modo ────────────────────────────────────
    @app_commands.command(
        name="cs",
        description="Escolhe um modo e cria uma sala.",
    )
    @user_app()
    async def cs(self, interaction: discord.Interaction):
        guild_id = _guild_id_efetivo(interaction)
        saldo = await database.get_saldo(guild_id, interaction.user.id)
        if saldo < 1:
            return await interaction.response.send_message(
                f"{DOLLAR} Você não tem saldo. Compre pelo painel de vendas do servidor.",
                ephemeral=True,
            )
        view = ModoSelectView(self.bot, infinito=False)
        await interaction.response.send_message(
            f"{PRESENTE} Escolha o modo:", view=view, ephemeral=True
        )

    # ─── /c1 ── 4X4 SEM CARREGAMENTO direto ─────────────────────
    @app_commands.command(
        name="c1",
        description="Cria sala 4X4 SEM CARREGAMENTO direto.",
    )
    @user_app()
    async def c1(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await criar_e_anunciar_sala(
            bot=self.bot,
            interaction=interaction,
            salaid=SALAID_C1,
            modo_nome=MODO_C1,
            infinito=False,
        )

    # ─── /c2 ── X1 GELO INF em loop infinito ────────────────────
    @app_commands.command(
        name="c2",
        description="Cria X1 GELO INF em modo infinito (recria ao iniciar).",
    )
    @user_app()
    async def c2(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await criar_e_anunciar_sala(
            bot=self.bot,
            interaction=interaction,
            salaid=SALAID_C2,
            modo_nome=MODO_C2,
            infinito=True,
        )


async def setup(bot):
    await bot.add_cog(SalasCog(bot))
