"""
views/loja_view.py — Painéis de venda em Components V2.

LojaDiscordView   → publicado em 🔶｜sala-discord
    • Container com: título, valor, mínimo, botões "Meu Perfil" + "Comprar Salas"

LojaTelegramView  → publicado em 🔷｜sala-telegram
    • Container com: título "F Bot Vendas", descrição, select de produtos

ComoUsarDiscordView   → publicado em 🔶｜como-usar
ComoUsarTelegramView  → publicado em 🔷｜como-usar
    • Containers com o passo a passo
"""

import discord
from discord.ui import LayoutView, Container, Section, TextDisplay, Separator, ActionRow

from utils import database
from utils import salasff
from utils.misticpay import criar_pix
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, SWORD, VISION, button_emoji
from views.carteira_view import montar_carteira
from views.painel_view import PixResultView


PRECO_PADRAO = 0.07


# ═══════════════════════════════════════════════════════════════════
# COMO USAR — Discord (Components V2)
# ═══════════════════════════════════════════════════════════════════

class ComoUsarDiscordView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        container = Container(accent_colour=discord.Colour.from_str("#5865F2"))

        container.add_item(TextDisplay(f"# {VISION} Como usar — Sala Discord"))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"**1.** Clique em **Comprar Salas** e digite a quantidade (mínimo 10).\n"
            f"**2.** Pague o **PIX** gerado pelo banco.\n"
            f"**3.** Clique em **Já paguei — Verificar**.\n"
            f"**4.** As salas são creditadas no seu saldo automaticamente."
        ))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"### {CLOUD} Comandos úteis\n"
            f"{CHANNEL} `/c` — ver carteira (mesmo que **Meu Perfil**)\n"
            f"{CHANNEL} `/cs` — escolher modo e criar sala\n"
            f"{CHANNEL} `/c1`, `/c2` — sala rápida (1h ou 2h)"
        ))
        container.add_item(TextDisplay(
            f"-# {AWAITING} Pagamento via PIX · Entrega instantânea"
        ))

        self.add_item(container)


# ═══════════════════════════════════════════════════════════════════
# COMO USAR — Telegram (Components V2)
# ═══════════════════════════════════════════════════════════════════

class ComoUsarTelegramView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        container = Container(accent_colour=discord.Colour.from_str("#229ED9"))

        container.add_item(TextDisplay(f"# {VISION} Como usar — Sala Telegram"))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"**1.** Escolha o plano no menu abaixo.\n"
            f"**2.** Pague o **PIX** gerado.\n"
            f"**3.** Clique em **Já paguei — Verificar**.\n"
            f"**4.** O **link de convite** do Telegram chega no seu privado."
        ))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"{AWAITING} Link único, válido por tempo limitado — use logo.\n"
            f"{CLOUD} Acesso dura conforme o plano escolhido.\n"
            f"{PRESENTE} Quando expirar, você é removido automaticamente."
        ))
        container.add_item(TextDisplay(
            f"-# {AWAITING} Pagamento via PIX · Entrega instantânea"
        ))

        self.add_item(container)


# ═══════════════════════════════════════════════════════════════════
# Painel SALA DISCORD — Components V2
# ═══════════════════════════════════════════════════════════════════

class LojaDiscordView(LayoutView):
    """Persistente — container com info + botões na borda (via Section)."""

    def __init__(self, preco: float = PRECO_PADRAO):
        super().__init__(timeout=None)

        container = Container(accent_colour=discord.Colour.from_str("#43B581"))

        container.add_item(TextDisplay(f"# {PRESENTE} Salas Discord"))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"{DOLLAR} **Valor:** R$ {preco:.2f} por sala\n"
            f"{AWAITING} **Mínimo:** 10 salas por compra"
        ))
        container.add_item(Separator())

        # Section 1: texto curto à esquerda + botão "Meu Perfil" à direita
        sec_perfil = Section(accessory=MeuPerfilButton())
        sec_perfil.add_item(TextDisplay(f"{VISION} **Sua conta** — saldo e histórico"))
        container.add_item(sec_perfil)

        # Section 2: texto + botão "Comprar Salas" à direita
        sec_comprar = Section(accessory=ComprarSalasButton())
        sec_comprar.add_item(TextDisplay(f"{PRESENTE} **Comprar agora** — gera PIX"))
        container.add_item(sec_comprar)

        container.add_item(TextDisplay(
            f"-# {CLOUD} Pagamento via PIX · Entrega instantânea"
        ))

        self.add_item(container)

    async def build(self, guild_id: int):
        """Recarrega o preço do banco antes de publicar."""
        config = await database.get_guild_config(guild_id)
        preco = float(config.get("preco_sala_discord") or PRECO_PADRAO)
        container = self.children[0]
        for item in container.children:
            if isinstance(item, TextDisplay) and "Valor" in item.content:
                item.content = (
                    f"{DOLLAR} **Valor:** R$ {preco:.2f} por sala\n"
                    f"{AWAITING} **Mínimo:** 10 salas por compra"
                )
                break


class MeuPerfilButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Meu Perfil",
            style=discord.ButtonStyle.secondary,
            emoji=button_emoji("vision"),
            custom_id="loja_discord:perfil",
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id or 0
        saldo = await database.get_saldo(guild_id, interaction.user.id)
        try:
            saldo_global = await salasff.saldo_global()
        except Exception:
            saldo_global = 0
        view = montar_carteira(interaction.user, saldo, saldo_global)
        await interaction.response.send_message(view=view, ephemeral=True)


class ComprarSalasButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Comprar Salas",
            style=discord.ButtonStyle.success,
            emoji=button_emoji("dollar"),
            custom_id="loja_discord:comprar",
        )

    async def callback(self, interaction: discord.Interaction):
        config = await database.get_guild_config(interaction.guild_id or 0)
        preco = float(config.get("preco_sala_discord") or PRECO_PADRAO)
        await interaction.response.send_modal(ComprarSalasModal(preco))


class ComprarSalasModal(discord.ui.Modal, title="Comprar Salas"):
    quantidade = discord.ui.TextInput(
        label="Quantidade (mínimo 10)",
        placeholder="Ex: 50",
        required=True,
        max_length=8,
    )

    def __init__(self, preco_unitario: float):
        super().__init__()
        self.preco = preco_unitario
        self.quantidade.label = f"Quantidade (min 10) · R$ {preco_unitario:.2f} cada"

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qtd = int(self.quantidade.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                f"{AWAITING} Quantidade inválida. Digite só números.",
                ephemeral=True,
            )
        if qtd < 10:
            return await interaction.response.send_message(
                f"{AWAITING} Mínimo: **10 salas**.",
                ephemeral=True,
            )

        valor_total = round(qtd * self.preco, 2)
        await interaction.response.defer(ephemeral=True, thinking=True)

        pix = await criar_pix(
            valor=valor_total,
            descricao=f"{qtd} salas - {interaction.user.name}",
        )
        if pix is None:
            return await interaction.followup.send(
                f"{AWAITING} Erro ao gerar PIX. Tente novamente.",
                ephemeral=True,
            )

        await database.criar_transacao(
            guild_id=interaction.guild_id or 0,
            user_id=interaction.user.id,
            produto_id=0,
            txid=pix["txid"],
            valor=pix["valor"],
            salas=qtd,
        )

        produto_fake = {
            "nome": f"{qtd} salas Free Fire",
            "salas": qtd,
            "preco": valor_total,
        }
        await interaction.followup.send(
            view=PixResultView(pix=pix, produto=produto_fake),
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# Painel SALA TELEGRAM — Components V2
# ═══════════════════════════════════════════════════════════════════

class LojaTelegramView(LayoutView):
    """Persistente — container com cabeçalho + menu select."""

    def __init__(self):
        super().__init__(timeout=None)

        container = Container(accent_colour=discord.Colour.from_str("#229ED9"))
        container.add_item(TextDisplay(f"# {PRESENTE} F Bot Vendas"))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"Bem-vindo! Pagamento via **PIX** e entrega **instantânea**.\n\n"
            f"Escolha um plano no menu abaixo:"
        ))
        container.add_item(TextDisplay(
            f"-# {CLOUD} Após pagar, o link do grupo Telegram chega no privado"
        ))
        # Select dentro do container (envolto em ActionRow)
        self._select = ProdutosTelegramSelect()
        row = ActionRow()
        row.add_item(self._select)
        container.add_item(row)
        self.add_item(container)

    async def build(self, guild_id: int):
        """Carrega produtos do banco e popula o select."""
        select = self._select
        produtos = await database.listar_produtos(guild_id)
        if not produtos:
            select.placeholder = "Nenhum plano cadastrado ainda"
            select.disabled = True
            select.options = [discord.SelectOption(
                label="Nenhum produto disponível",
                value="__empty__",
                description="Admin: cadastre via /painelcompras",
            )]
            return
        select.disabled = False
        select.placeholder = "Escolha um plano Telegram..."
        select.options = [
            discord.SelectOption(
                label=str(p["nome"])[:100],
                value=str(p["id"]),
                description=f"R$ {float(p['preco']):.2f}"[:100],
            )
            for p in produtos[:25]
        ]


class ProdutosTelegramSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="Carregando planos...",
            min_values=1,
            max_values=1,
            custom_id="loja_tg:select",
            options=[discord.SelectOption(label="Carregando...", value="__loading__")],
        )

    async def callback(self, interaction: discord.Interaction):
        valor = self.values[0]
        if valor in ("__empty__", "__loading__"):
            return await interaction.response.send_message(
                f"{AWAITING} Nenhum plano disponível no momento.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        produtos = await database.listar_produtos(interaction.guild_id or 0)
        try:
            produto_id = int(valor)
        except ValueError:
            return await interaction.followup.send(
                f"{AWAITING} Produto inválido.", ephemeral=True
            )

        produto = next((p for p in produtos if p["id"] == produto_id), None)
        if not produto:
            return await interaction.followup.send(
                f"{AWAITING} Produto não encontrado.", ephemeral=True
            )

        pix = await criar_pix(
            valor=float(produto["preco"]),
            descricao=f"{produto['nome']} - {interaction.user.name}",
        )
        if pix is None:
            return await interaction.followup.send(
                f"{AWAITING} Erro ao gerar PIX. Tente novamente.",
                ephemeral=True,
            )

        await database.criar_transacao(
            guild_id=interaction.guild_id or 0,
            user_id=interaction.user.id,
            produto_id=int(produto["id"]),
            txid=pix["txid"],
            valor=pix["valor"],
            salas=int(produto.get("salas", 0)),
        )

        await interaction.followup.send(
            view=PixResultView(pix=pix, produto=produto),
            ephemeral=True,
        )
