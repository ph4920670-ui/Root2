import discord
from discord.ui import LayoutView, Container, TextDisplay
from utils import database
from utils.emojis import button_emoji as _emoji
from views.painel_view import PainelView


# ═══════════════════════════════════════════════════════════════════
# MENU ADMIN — aparece quando usa /painelcompras
# ═══════════════════════════════════════════════════════════════════

class AdminMenuView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
        # Botões adicionados manualmente pra suportar emoji custom no label
        self.add_item(self._btn_editar())
        self.add_item(self._btn_enviar())

    def _btn_editar(self):
        btn = discord.ui.Button(
            label="Editar Painel",
            style=discord.ButtonStyle.primary,
            emoji=_emoji("vision"),
        )
        btn.callback = self._cb_editar
        return btn

    def _btn_enviar(self):
        btn = discord.ui.Button(
            label="Enviar Painel",
            style=discord.ButtonStyle.success,
            emoji=_emoji("cloud"),
        )
        btn.callback = self._cb_enviar
        return btn

    async def _cb_editar(self, interaction: discord.Interaction):
        view = EditarPainelView(self.bot)
        await interaction.response.send_message(
            "**Menu de edição do painel:**", view=view, ephemeral=True
        )

    async def _cb_enviar(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        painel = PainelView(self.bot)
        await painel.build(interaction.guild_id)
        try:
            await interaction.channel.send(view=painel)
            await interaction.followup.send(
                "Painel enviado neste canal!", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "Não tenho permissão para enviar mensagens neste canal.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"Erro ao enviar: `{e}`", ephemeral=True
            )


# ═══════════════════════════════════════════════════════════════════
# MENU EDITAR PAINEL
# ═══════════════════════════════════════════════════════════════════

class EditarPainelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
        self.add_item(self._btn("Imagem",            "cloud",    discord.ButtonStyle.secondary, 0, self._cb_imagem))
        self.add_item(self._btn("Texto",             "vision",   discord.ButtonStyle.secondary, 0, self._cb_texto))
        self.add_item(self._btn("Adicionar Produto", "box",      discord.ButtonStyle.success,   1, self._cb_adicionar))
        self.add_item(self._btn("Editar Produto",    "database", discord.ButtonStyle.primary,   1, self._cb_editar_p))
        self.add_item(self._btn("Remover Produto",   "awaiting", discord.ButtonStyle.danger,    1, self._cb_remover))

    def _btn(self, label, emoji_name, style, row, callback):
        btn = discord.ui.Button(label=label, style=style, row=row, emoji=_emoji(emoji_name))
        btn.callback = callback
        return btn

    async def _cb_imagem(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EditarImagemModal())

    async def _cb_texto(self, interaction: discord.Interaction):
        config = await database.get_config(interaction.guild_id)
        await interaction.response.send_modal(EditarTextoModal(config))

    async def _cb_adicionar(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AdicionarProdutoModal())

    async def _cb_editar_p(self, interaction: discord.Interaction):
        produtos = await database.listar_produtos(interaction.guild_id)
        if not produtos:
            return await interaction.response.send_message(
                "Nenhum produto cadastrado.", ephemeral=True
            )
        view = SelecionarProdutoView(produtos, modo="editar")
        await interaction.response.send_message(
            "Escolha o produto para editar:", view=view, ephemeral=True
        )

    async def _cb_remover(self, interaction: discord.Interaction):
        produtos = await database.listar_produtos(interaction.guild_id)
        if not produtos:
            return await interaction.response.send_message(
                "Nenhum produto cadastrado.", ephemeral=True
            )
        view = SelecionarProdutoView(produtos, modo="remover")
        await interaction.response.send_message(
            "Escolha o produto para remover:", view=view, ephemeral=True
        )


# ═══════════════════════════════════════════════════════════════════
# MODAIS
# ═══════════════════════════════════════════════════════════════════

class EditarImagemModal(discord.ui.Modal, title="Editar imagem do painel"):
    url = discord.ui.TextInput(
        label="URL da imagem",
        placeholder="https://i.imgur.com/exemplo.png",
        required=True,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        url = self.url.value.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return await interaction.response.send_message(
                "URL inválida. Precisa começar com http:// ou https://",
                ephemeral=True,
            )
        await database.update_config(interaction.guild_id, imagem_url=url)
        await interaction.response.send_message(
            "Imagem atualizada! Envie o painel novamente para ver.",
            ephemeral=True,
        )


class EditarTextoModal(discord.ui.Modal, title="Editar texto do painel"):
    def __init__(self, config: dict):
        super().__init__()
        self.titulo = discord.ui.TextInput(
            label="Título",
            default=config.get("titulo", ""),
            required=True,
            max_length=80,
        )
        self.descricao = discord.ui.TextInput(
            label="Descrição",
            default=config.get("descricao", ""),
            required=True,
            max_length=500,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.titulo)
        self.add_item(self.descricao)

    async def on_submit(self, interaction: discord.Interaction):
        await database.update_config(
            interaction.guild_id,
            titulo=self.titulo.value,
            descricao=self.descricao.value,
        )
        await interaction.response.send_message(
            "Texto atualizado! Envie o painel novamente para ver.",
            ephemeral=True,
        )


class AdicionarProdutoModal(discord.ui.Modal, title="Adicionar produto"):
    nome = discord.ui.TextInput(
        label="Nome do produto", required=True, max_length=100
    )
    preco = discord.ui.TextInput(
        label="Preço (ex: 19.90)", required=True, max_length=10
    )
    salas = discord.ui.TextInput(
        label="Quantidade de salas que este produto dá",
        required=True,
        max_length=5,
        placeholder="ex: 10",
    )
    descricao = discord.ui.TextInput(
        label="Descrição (opcional)",
        required=False,
        max_length=200,
        style=discord.TextStyle.paragraph,
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = float(self.preco.value.replace(",", "."))
            if valor <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Preço inválido. Use formato `19.90`.", ephemeral=True
            )

        try:
            qtd_salas = int(self.salas.value.strip())
            if qtd_salas <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Quantidade de salas inválida. Use um número inteiro maior que 0.",
                ephemeral=True,
            )

        await database.adicionar_produto(
            guild_id=interaction.guild_id,
            nome=self.nome.value,
            preco=valor,
            salas=qtd_salas,
            descricao=self.descricao.value or "",
        )
        await interaction.response.send_message(
            f"Produto **{self.nome.value}** adicionado: `{qtd_salas}` salas por `R$ {valor:.2f}`.",
            ephemeral=True,
        )


class EditarProdutoModal(discord.ui.Modal, title="Editar produto"):
    def __init__(self, produto: dict):
        super().__init__()
        self.produto_id = produto["id"]
        self.nome = discord.ui.TextInput(
            label="Nome do produto",
            default=produto["nome"],
            required=True,
            max_length=100,
        )
        self.preco = discord.ui.TextInput(
            label="Preço",
            default=f"{produto['preco']:.2f}",
            required=True,
            max_length=10,
        )
        self.salas = discord.ui.TextInput(
            label="Quantidade de salas",
            default=str(produto.get("salas", 0)),
            required=True,
            max_length=5,
        )
        self.descricao = discord.ui.TextInput(
            label="Descrição",
            default=produto.get("descricao", ""),
            required=False,
            max_length=200,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.nome)
        self.add_item(self.preco)
        self.add_item(self.salas)
        self.add_item(self.descricao)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = float(self.preco.value.replace(",", "."))
            if valor <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Preço inválido.", ephemeral=True
            )

        try:
            qtd_salas = int(self.salas.value.strip())
            if qtd_salas <= 0:
                raise ValueError
        except ValueError:
            return await interaction.response.send_message(
                "Quantidade de salas inválida.", ephemeral=True
            )

        await database.atualizar_produto(
            produto_id=self.produto_id,
            nome=self.nome.value,
            preco=valor,
            salas=qtd_salas,
            descricao=self.descricao.value or "",
        )
        await interaction.response.send_message(
            f"Produto atualizado: **{self.nome.value}** — `{qtd_salas}` salas · `R$ {valor:.2f}`",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# SELECT DE PRODUTO (pra editar/remover)
# ═══════════════════════════════════════════════════════════════════

class SelecionarProdutoView(discord.ui.View):
    def __init__(self, produtos: list[dict], modo: str):
        super().__init__(timeout=180)
        self.add_item(SelecionarProdutoSelect(produtos, modo))


class SelecionarProdutoSelect(discord.ui.Select):
    def __init__(self, produtos: list[dict], modo: str):
        self.modo = modo
        options = [
            discord.SelectOption(
                label=p["nome"][:100],
                description=f"R$ {p['preco']:.2f}"[:100],
                value=str(p["id"]),
            )
            for p in produtos[:25]
        ]
        super().__init__(
            placeholder=f"Escolha o produto para {modo}...",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        produto_id = self.values[0]
        produto = await database.get_produto(produto_id)
        if produto is None:
            return await interaction.response.send_message(
                "Produto não encontrado.", ephemeral=True
            )

        if self.modo == "editar":
            await interaction.response.send_modal(EditarProdutoModal(produto))
        elif self.modo == "remover":
            await database.remover_produto(produto_id)
            await interaction.response.send_message(
                f"Produto **{produto['nome']}** removido.", ephemeral=True
            )
