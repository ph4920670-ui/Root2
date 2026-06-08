"""
cogs/perfil.py — comando /perfil

Mostra:
- Saldo atual do usuário (salas)
- Últimas 5 compras (transações confirmadas)
- Botões "Adicionar saldo" e "Remover saldo" (somente admin)

Admin pode passar o parâmetro `usuario` pra ver perfil de outro membro.
"""

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import LayoutView, Container, Section, TextDisplay, Separator

from utils import database
from utils.scope import user_app
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, SWORD, VISION, button_emoji


def _fmt_data(iso_str: str) -> str:
    """Formata 'YYYY-MM-DD HH:MM:SS' em 'DD/MM HH:MM'."""
    if not iso_str:
        return "?"
    try:
        # SQLite default já vem assim
        data, hora = iso_str.split(" ")
        ano, mes, dia = data.split("-")
        h, m, *_ = hora.split(":")
        return f"{dia}/{mes} {h}:{m}"
    except Exception:
        return iso_str[:16]


async def _montar_view_perfil(
    target: discord.User | discord.Member,
    saldo: int,
    transacoes: list[dict],
    is_admin: bool,
) -> LayoutView:
    """Monta a view V2 do perfil."""
    view = LayoutView(timeout=None)
    container = Container(accent_colour=discord.Colour.from_str("#5865F2"))

    container.add_item(TextDisplay(f"# {VISION} Perfil — {target.display_name}"))
    container.add_item(Separator())
    container.add_item(TextDisplay(
        f"{DOLLAR} **Saldo:** `{saldo}` sala(s)\n"
        f"{CHANNEL} **Usuário:** {target.mention}\n"
        f"{CHANNEL} **ID:** `{target.id}`"
    ))
    container.add_item(Separator())

    # Histórico
    if transacoes:
        linhas = []
        for t in transacoes:
            data = _fmt_data(t.get("criado_em", ""))
            valor = float(t.get("valor", 0))
            salas = int(t.get("salas", 0))
            linhas.append(
                f"{PRESENTE} `{data}` · **{salas}** salas · R$ {valor:.2f}"
            )
        container.add_item(TextDisplay(
            f"### {SWORD} Últimas Compras\n" + "\n".join(linhas)
        ))
    else:
        container.add_item(TextDisplay(
            f"### {SWORD} Últimas Compras\n"
            f"-# Nenhuma compra registrada ainda."
        ))

    # Botões de admin (Sections — botões dentro do container)
    if is_admin:
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"-# {AWAITING} Controles de administrador"
        ))

        sec_add = Section(accessory=AdicionarSaldoButton(target.id))
        sec_add.add_item(TextDisplay(f"➕ **Adicionar saldo** ao usuário"))
        container.add_item(sec_add)

        sec_rem = Section(accessory=RemoverSaldoButton(target.id))
        sec_rem.add_item(TextDisplay(f"➖ **Remover saldo** do usuário"))
        container.add_item(sec_rem)

    view.add_item(container)
    return view


# ═══════════════════════════════════════════════════════════════════
# Modais de admin (Adicionar / Remover saldo)
# ═══════════════════════════════════════════════════════════════════

class AdicionarSaldoModal(discord.ui.Modal, title="Adicionar Saldo"):
    quantidade = discord.ui.TextInput(
        label="Quantas salas adicionar?",
        placeholder="Ex: 10",
        required=True,
        max_length=8,
    )
    motivo = discord.ui.TextInput(
        label="Motivo (opcional)",
        placeholder="Ex: bônus por evento",
        required=False,
        max_length=100,
    )

    def __init__(self, target_id: int):
        super().__init__()
        self.target_id = target_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qtd = int(self.quantidade.value.strip())
            if qtd <= 0:
                raise ValueError("deve ser positivo")
        except ValueError:
            return await interaction.response.send_message(
                f"{AWAITING} Quantidade inválida.", ephemeral=True
            )

        guild_id = interaction.guild_id or 0
        novo_saldo = await database.adicionar_saldo(guild_id, self.target_id, qtd)

        # Log
        try:
            from utils import logs as _logs
            await _logs.log_saldo(
                interaction.client,
                guild_id=guild_id,
                user_id=self.target_id,
                motivo=f"Admin +{qtd}: {self.motivo.value or 'sem motivo'}",
                variacao=qtd,
                novo_saldo=novo_saldo,
            )
        except Exception:
            pass

        await interaction.response.send_message(
            f"{SWORD} Adicionei `{qtd}` sala(s) ao <@{self.target_id}>.\n"
            f"{DOLLAR} Novo saldo: `{novo_saldo}`",
            ephemeral=True,
        )


class RemoverSaldoModal(discord.ui.Modal, title="Remover Saldo"):
    quantidade = discord.ui.TextInput(
        label="Quantas salas remover?",
        placeholder="Ex: 5",
        required=True,
        max_length=8,
    )
    motivo = discord.ui.TextInput(
        label="Motivo (opcional)",
        placeholder="Ex: estorno",
        required=False,
        max_length=100,
    )

    def __init__(self, target_id: int):
        super().__init__()
        self.target_id = target_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qtd = int(self.quantidade.value.strip())
            if qtd <= 0:
                raise ValueError("deve ser positivo")
        except ValueError:
            return await interaction.response.send_message(
                f"{AWAITING} Quantidade inválida.", ephemeral=True
            )

        guild_id = interaction.guild_id or 0
        novo_saldo = await database.adicionar_saldo(guild_id, self.target_id, -qtd)

        try:
            from utils import logs as _logs
            await _logs.log_saldo(
                interaction.client,
                guild_id=guild_id,
                user_id=self.target_id,
                motivo=f"Admin -{qtd}: {self.motivo.value or 'sem motivo'}",
                variacao=-qtd,
                novo_saldo=novo_saldo,
            )
        except Exception:
            pass

        await interaction.response.send_message(
            f"{SWORD} Removi `{qtd}` sala(s) de <@{self.target_id}>.\n"
            f"{DOLLAR} Novo saldo: `{novo_saldo}`",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# Botões dos modais (com custom_id contendo o target_id)
# ═══════════════════════════════════════════════════════════════════

class AdicionarSaldoButton(discord.ui.Button):
    def __init__(self, target_id: int):
        super().__init__(
            label="Adicionar",
            style=discord.ButtonStyle.success,
            emoji=button_emoji("presente"),
            # NÃO persistente — view é gerada a cada /perfil
        )
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) \
                or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                f"{AWAITING} Apenas administradores.", ephemeral=True
            )
        await interaction.response.send_modal(AdicionarSaldoModal(self.target_id))


class RemoverSaldoButton(discord.ui.Button):
    def __init__(self, target_id: int):
        super().__init__(
            label="Remover",
            style=discord.ButtonStyle.danger,
            emoji=button_emoji("awaiting"),
        )
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member) \
                or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                f"{AWAITING} Apenas administradores.", ephemeral=True
            )
        await interaction.response.send_modal(RemoverSaldoModal(self.target_id))


# ═══════════════════════════════════════════════════════════════════
# COG
# ═══════════════════════════════════════════════════════════════════

class PerfilCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="perfil",
        description="Mostra seu perfil (saldo + histórico). Admin pode ver de outros.",
    )
    @app_commands.describe(
        usuario="(Admin) Ver perfil de outro usuário"
    )
    @user_app()
    async def perfil(
        self,
        interaction: discord.Interaction,
        usuario: discord.User | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)

        target = usuario or interaction.user
        guild_id = interaction.guild_id or 0

        # Só admin pode ver perfil de outros
        is_admin = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )
        if usuario is not None and not is_admin:
            return await interaction.followup.send(
                f"{AWAITING} Apenas administradores podem ver perfil de outros usuários.",
                ephemeral=True,
            )

        saldo = await database.get_saldo(guild_id, target.id)
        transacoes = await database.ultimas_transacoes(guild_id, target.id, limit=5)

        view = await _montar_view_perfil(target, saldo, transacoes, is_admin)
        await interaction.followup.send(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(PerfilCog(bot))
