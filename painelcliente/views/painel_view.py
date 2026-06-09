import discord
from discord.ui import LayoutView, Container, Section, TextDisplay, MediaGallery, ActionRow
from utils import database
from utils.misticpay import criar_pix, consultar_pix
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, SWORD, VISION, button_emoji
from utils import logs


# ═══════════════════════════════════════════════════════════════════
# PAINEL PÚBLICO (o que o cliente vê) — Components V2
# ═══════════════════════════════════════════════════════════════════

class PainelView(LayoutView):
    """
    Painel persistente em Components V2.
    Estrutura: Container → MediaGallery (imagem por cima) + TextDisplay + Select de produtos
    """

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot
        # Os componentes são montados dinamicamente no método build()

    async def build(self, guild_id: int):
        """Monta o conteúdo do painel a partir do banco."""
        config = await database.get_config(guild_id)
        produtos = await database.listar_produtos(guild_id)

        # Limpa componentes antigos (caso seja rebuild)
        self.clear_items()

        container = Container(accent_colour=discord.Colour.from_str("#5865F2"))

        # Imagem por cima (MediaGallery)
        if config.get("imagem_url"):
            try:
                gallery = MediaGallery()
                gallery.add_item(media=config["imagem_url"])
                container.add_item(gallery)
            except Exception as e:
                print(f"⚠️ Erro ao adicionar imagem: {e}")

        # Título + descrição
        titulo = config.get("titulo") or f"{VISION} Painel de Compras"
        descricao = config.get("descricao") or "Selecione um produto abaixo."
        container.add_item(TextDisplay(f"# {titulo}\n{descricao}"))

        # Lista de produtos como texto
        if produtos:
            linhas = "\n".join(
                f"{PRESENTE} **{p['nome']}** — {CHANNEL} `{p.get('salas', 0)}` salas · {DOLLAR} `R$ {p['preco']:.2f}`"
                for p in produtos
            )
            container.add_item(TextDisplay(f"### {CHANNEL} Produtos disponíveis\n{linhas}"))

            # Select DENTRO do container — full embed Components V2.
            row = ActionRow()
            row.add_item(ProdutoSelect(produtos))
            container.add_item(row)
        else:
            container.add_item(TextDisplay(f"### {AWAITING} Nenhum produto cadastrado ainda."))

        self.add_item(container)
        return self


class ProdutoSelect(discord.ui.Select):
    """Select menu persistente — usa custom_id fixo."""

    def __init__(self, produtos: list[dict]):
        options = [
            discord.SelectOption(
                label=p["nome"][:100],
                description=f"{p.get('salas', 0)} salas · R$ {p['preco']:.2f}"[:100],
                value=str(p["id"]),
                emoji=button_emoji("box"),
            )
            for p in produtos[:25]
        ]
        super().__init__(
            placeholder="Selecione um produto para comprar...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="painel:produto_select",
        )

    async def callback(self, interaction: discord.Interaction):
        produto_id = self.values[0]
        produto = await database.get_produto(produto_id)

        if produto is None:
            return await interaction.response.send_message(
                "Produto não encontrado. Pode ter sido removido.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Gera PIX
        pix = await criar_pix(
            valor=produto["preco"],
            descricao=f"{produto['nome']} - {interaction.user.name}",
        )

        if pix is None:
            return await interaction.followup.send(
                "Erro ao gerar PIX. Tente novamente em alguns segundos.",
                ephemeral=True,
            )

        # Salva transação
        await database.criar_transacao(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            produto_id=produto_id,
            txid=pix["txid"],
            valor=pix["valor"],
            salas=int(produto.get("salas", 0)),
        )

        # Monta o resultado em Components V2
        result_view = PixResultView(pix=pix, produto=produto)
        await interaction.followup.send(view=result_view, ephemeral=True)


# ═══════════════════════════════════════════════════════════════════
# RESULTADO DO PIX (mostrado em ephemeral pro comprador)
# ═══════════════════════════════════════════════════════════════════

class PixResultView(LayoutView):
    def __init__(self, pix: dict, produto: dict):
        super().__init__(timeout=1800)  # 30 min
        self.pix = pix
        self.produto = produto

        container = Container(accent_colour=discord.Colour.green())
        container.add_item(TextDisplay(
            f"# {DOLLAR} PIX Gerado\n"
            f"{PRESENTE} **Produto:** {produto['nome']}\n"
            f"{DOLLAR} **Valor:** `R$ {pix['valor']:.2f}`\n\n"
            f"### {CLOUD} Código Copia-e-Cola\n"
            f"```\n{pix['copia_cola']}\n```\n"
            f"{AWAITING} O código expira em 30 minutos."
        ))
        self.add_item(container)
        row = ActionRow()
        row.add_item(CopiarPixButton(copia_cola=pix["copia_cola"]))
        row.add_item(VerificarPagamentoButton(txid=pix["txid"]))
        self.add_item(row)


class CopiarPixButton(discord.ui.Button):
    def __init__(self, copia_cola: str):
        super().__init__(
            label="Copiar PIX",
            style=discord.ButtonStyle.primary,
        )
        self.copia_cola = copia_cola

    async def callback(self, interaction: discord.Interaction):
        # Envia só o código puro, sem código markdown, sem símbolo, sem nada.
        # O cliente seleciona e copia direto pra colar no banco.
        await interaction.response.send_message(self.copia_cola, ephemeral=True)


class VerificarPagamentoButton(discord.ui.Button):
    def __init__(self, txid: str):
        super().__init__(
            label="Já paguei — Verificar",
            style=discord.ButtonStyle.success,
            custom_id=f"painel:verificar:{txid}",
        )
        self.txid = txid

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        status = await consultar_pix(self.txid)

        if status == "pago":
            transacao = await database.get_transacao(self.txid)

            # Evita creditar 2x
            if transacao and transacao.get("status") != "pago":
                await database.atualizar_status_transacao(self.txid, "pago")

                # Credita salas automaticamente
                salas = int(transacao.get("salas", 0))
                if salas > 0:
                    novo_saldo = await database.adicionar_saldo(
                        guild_id=transacao["guild_id"],
                        user_id=transacao["user_id"],
                        quantidade=salas,
                    )

                    # Logs (em canais configurados em /botconfig)
                    produto = await database.get_produto(transacao["produto_id"])
                    nome_prod = produto.get("nome") if produto else "Produto"
                    try:
                        await logs.log_venda(
                            interaction.client,
                            guild_id=transacao["guild_id"],
                            user_id=transacao["user_id"],
                            produto_nome=nome_prod,
                            valor=float(transacao.get("valor", 0)),
                            salas=salas,
                            txid=self.txid,
                        )
                        await logs.log_saldo(
                            interaction.client,
                            guild_id=transacao["guild_id"],
                            user_id=transacao["user_id"],
                            motivo=f"Compra: {nome_prod}",
                            variacao=salas,
                            novo_saldo=novo_saldo,
                        )
                    except Exception as e:
                        print(f"⚠️ Erro ao postar logs: {e}")

                    return await interaction.followup.send(
                        f"{SWORD} **Pagamento confirmado!**\n"
                        f"{CHANNEL} **+{salas}** sala(s) adicionada(s).\n"
                        f"{DOLLAR} Seu saldo agora: `{novo_saldo}` sala(s).\n"
                        f"Use `/c1`, `/c2` ou `/cs` para criar suas salas.",
                        ephemeral=True,
                    )

            await interaction.followup.send(
                f"{SWORD} **Pagamento confirmado!**",
                ephemeral=True,
            )
        elif status == "expirado":
            await database.atualizar_status_transacao(self.txid, "expirado")
            await interaction.followup.send(
                f"{AWAITING} Pagamento expirou. Gere um novo PIX.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"{AWAITING} Pagamento ainda não foi confirmado. Tente novamente em alguns segundos.",
                ephemeral=True,
            )
