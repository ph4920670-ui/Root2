"""
/planos — publica o painel de planos do Bot de Mediar (Components V2).

Painel com texto de venda + menu (select) com os planos:
- Semanal
- 1 Mês

Nome e preço de cada plano são configuráveis pelo /botconfig (salvos no
banco, por servidor). Ao escolher um plano no menu, o bot mostra um resumo.
Disponível em qualquer servidor (guild-install), restrito a administradores.
"""

import discord
from discord import app_commands, MediaGalleryItem
from discord.ext import commands
from discord.ui import (LayoutView, Container, TextDisplay, Separator,
                        ActionRow, MediaGallery)

from utils import database
from utils.misticpay import criar_pix
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, SWORD, VISION
from utils.emojis import button_emoji
from utils.scope import guild_app


# ═══════════════════════════════════════════════════════════════════
# Planos — descrição fixa; nome e preço vêm do /botconfig (banco)
# ═══════════════════════════════════════════════════════════════════

BANCOS = ["Inter Kids", "Nubank", "SumUp", "XP"]

# Nome do arquivo do banner anexado (em assets/)
BANNER_ARQUIVO = "banner_planos.png"


def banner_file() -> discord.File:
    """Retorna o banner como discord.File pra anexar na mensagem."""
    import os
    caminho = os.path.join(os.path.dirname(__file__), "..", "assets", BANNER_ARQUIVO)
    return discord.File(caminho, filename=BANNER_ARQUIVO)


def _planos_do_config(config: dict) -> dict:
    """Monta o dicionário de planos a partir do guild_config."""
    return {
        "semanal": {
            "nome": config.get("plano_semanal_nome") or "Semanal",
            "preco": config.get("plano_semanal_preco") or "R$ 20,99",
            "emoji": "✧",
            "desc": "Acesso completo por 7 dias. Ideal pra testar o sistema.",
        },
        "mensal": {
            "nome": config.get("plano_mensal_nome") or "1 Mês",
            "preco": config.get("plano_mensal_preco") or "R$ 60,00",
            "emoji": "✦",
            "desc": "30 dias de mediação automática. Mais popular.",
        },
    }


# ═══════════════════════════════════════════════════════════════════
# Painel V2
# ═══════════════════════════════════════════════════════════════════

class PainelPlanosView(LayoutView):
    """Persistente — painel de planos com banner e menu de seleção."""

    def __init__(self, planos: dict | None = None):
        super().__init__(timeout=None)
        # planos=None só ocorre no registro persistente do on_ready;
        # nesse caso usamos os defaults só pra montar o select.
        if planos is None:
            planos = _planos_do_config({})

        container = Container(accent_colour=discord.Colour.from_str("#7B2FF7"))

        # Banner no topo do container (imagem anexada via attachment://)
        container.add_item(MediaGallery(
            MediaGalleryItem(media=f"attachment://{BANNER_ARQUIVO}")
        ))

        container.add_item(TextDisplay(f"# {VISION} Bot de Mediar — Power Base"))
        container.add_item(Separator())

        container.add_item(TextDisplay(
            "Se você quer mediar automático sem se preocupar em tomar multa, "
            "compre aqui agora seu plano."
        ))
        container.add_item(Separator())

        # Bancos permitidos
        container.add_item(TextDisplay(
            f"### {CHANNEL} Bancos permitidos\n{PRESENTE} {'   '.join(BANCOS)}"
        ))

        container.add_item(Separator())

        # Menu de planos DENTRO do container (ActionRow é exigido)
        row = ActionRow()
        row.add_item(PlanosSelect(planos))
        container.add_item(row)

        self.add_item(container)


def _preco_para_float(preco_texto: str) -> float | None:
    """Converte 'R$ 60,00' / '20,99' / 'R$1.234,56' em float. None se falhar."""
    import re
    if not preco_texto:
        return None
    # mantém só dígitos, vírgula e ponto
    limpo = re.sub(r"[^\d,.]", "", preco_texto)
    if not limpo:
        return None
    # formato brasileiro: ponto = milhar, vírgula = decimal
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return round(float(limpo), 2)
    except ValueError:
        return None


class PlanosSelect(discord.ui.Select):
    def __init__(self, planos: dict):
        options = [
            discord.SelectOption(
                label=f"{p['nome']} — {p['preco']}"[:100],
                description=p["desc"][:100],
                value=chave,
            )
            for chave, p in planos.items()
        ]
        super().__init__(
            placeholder="Escolher plano para configurar e enviar...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="planos:select",
        )

    async def callback(self, interaction: discord.Interaction):
        chave = self.values[0]
        # Relê os planos do banco — garante valores atualizados.
        config = await database.get_guild_config(interaction.guild_id)
        planos = _planos_do_config(config)
        plano = planos.get(chave)
        if plano is None:
            return await interaction.response.send_message(
                f"{AWAITING} Plano inválido.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        valor = _preco_para_float(plano["preco"])
        if valor is None or valor <= 0:
            return await interaction.followup.send(
                f"{AWAITING} O preço do plano **{plano['nome']}** "
                f"(`{plano['preco']}`) não é um valor válido. "
                f"Ajuste em `/botconfig`.",
                ephemeral=True,
            )

        # Gera a cobrança PIX no MisticPay
        pix = await criar_pix(
            valor=valor,
            descricao=f"Plano {plano['nome']} - Bot de Mediar",
            payer_name=interaction.user.display_name[:60],
        )
        if pix is None or not pix.get("copia_cola"):
            return await interaction.followup.send(
                f"{AWAITING} Não consegui gerar o PIX agora. "
                f"Tente de novo em instantes ou abra um ticket.",
                ephemeral=True,
            )

        view = PlanoEscolhidoView(plano, pix["copia_cola"])
        await interaction.followup.send(view=view, ephemeral=True)

        # Monitora o pagamento em segundo plano e dá o cargo ao confirmar.
        import asyncio
        asyncio.create_task(_monitorar_pagamento(
            txid=pix["txid"],
            guild=interaction.guild,
            user=interaction.user,
            plano=plano,
        ))


async def _monitorar_pagamento(txid, guild, user, plano):
    """Checa o PIX periodicamente; ao confirmar, dá o cargo cliente."""
    import asyncio
    from utils.misticpay import consultar_pix

    # Checa por ~15 minutos: a cada 20s, 45 vezes.
    for _ in range(45):
        await asyncio.sleep(20)
        try:
            status = await consultar_pix(txid)
        except Exception:
            continue

        if status == "pago":
            await _liberar_cliente(guild, user, plano)
            return
        if status == "falha":
            return  # transação falhou/cancelada — para de checar
    # tempo esgotado sem confirmação — para silenciosamente


async def _liberar_cliente(guild, user, plano):
    """Dá o cargo cliente ao usuário e registra no log de compras."""
    config = await database.get_guild_config(guild.id)

    # 1) Dá o cargo cliente, se configurado
    cargo_id = config.get("cargo_cliente_id")
    cargo_dado = False
    if cargo_id:
        cargo = guild.get_role(cargo_id)
        membro = guild.get_member(user.id)
        if cargo and membro:
            try:
                await membro.add_roles(
                    cargo, reason=f"Compra confirmada — plano {plano['nome']}"
                )
                cargo_dado = True
            except Exception:
                pass

    # 2) Avisa o usuário por DM
    try:
        await user.send(
            f"{SWORD} Pagamento do plano **{plano['nome']}** confirmado! "
            + ("Seu cargo de cliente foi liberado."
               if cargo_dado else "Fale com o suporte para liberar seu acesso.")
        )
    except Exception:
        pass

    # 3) Log de compra
    canal_id = config.get("canal_logs_compras_id")
    if canal_id:
        canal = guild.get_channel(canal_id)
        if canal:
            embed = discord.Embed(
                title=f"{SWORD} Compra confirmada",
                colour=discord.Colour.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name="Cliente",
                            value=f"{user.mention}\n`{user}`", inline=True)
            embed.add_field(name="Plano",
                            value=f"{plano['nome']} — {plano['preco']}", inline=True)
            embed.add_field(name="Cargo",
                            value="✅ liberado" if cargo_dado else "⚠️ não configurado",
                            inline=True)
            embed.set_footer(text=f"ID: {user.id}")
            try:
                await canal.send(embed=embed)
            except Exception:
                pass


class PlanoEscolhidoView(LayoutView):
    """Resumo ephemeral do plano + PIX copia-e-cola com botão de copiar."""

    def __init__(self, plano: dict, pix_copia_cola: str):
        super().__init__(timeout=900)
        self.pix_copia_cola = pix_copia_cola

        container = Container(accent_colour=discord.Colour.from_str("#7B2FF7"))

        container.add_item(TextDisplay(
            f"# {plano['emoji']} Plano {plano['nome']}"
        ))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"{DOLLAR} **Valor:** `{plano['preco']}`\n"
            f"{CLOUD} {plano['desc']}"
        ))
        container.add_item(Separator())

        # PIX copia-e-cola — bloco de código + botão de copiar
        container.add_item(TextDisplay("**PIX copia e cola**"))
        container.add_item(TextDisplay(f"```\n{pix_copia_cola}\n```"))

        row = ActionRow()
        row.add_item(CopiarPixButton(pix_copia_cola))
        container.add_item(row)

        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"-# Pague o PIX acima e abra um ticket de suporte para "
            f"liberar o plano **{plano['nome']}**."
        ))

        self.add_item(container)


class CopiarPixButton(discord.ui.Button):
    """Reenvia a chave PIX pura, sozinha, pra facilitar a cópia."""

    def __init__(self, pix_copia_cola: str):
        super().__init__(
            label="Copiar PIX",
            style=discord.ButtonStyle.success,
        )
        self.pix_copia_cola = pix_copia_cola

    async def callback(self, interaction: discord.Interaction):
        # Mensagem só com a chave PIX — toque longo no celular copia tudo.
        await interaction.response.send_message(
            self.pix_copia_cola,
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# COG
# ═══════════════════════════════════════════════════════════════════

class PlanosCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="planos",
        description="Publica o painel de planos do Bot de Mediar.",
    )
    @guild_app()
    @app_commands.default_permissions(administrator=True)
    async def planos(self, interaction: discord.Interaction):
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

        # Lê os planos configurados pra este servidor.
        config = await database.get_guild_config(interaction.guild_id)
        planos = _planos_do_config(config)

        try:
            await interaction.channel.send(
                view=PainelPlanosView(planos),
                file=banner_file(),
            )
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"{AWAITING} Não tenho permissão pra enviar mensagem neste canal.",
                ephemeral=True,
            )
        except FileNotFoundError:
            return await interaction.response.send_message(
                f"{AWAITING} Banner não encontrado (`assets/{BANNER_ARQUIVO}`). "
                f"Verifique se a pasta `assets` subiu no deploy.",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"{AWAITING} Erro ao publicar painel: `{e}`", ephemeral=True
            )

        await interaction.response.send_message(
            f"{SWORD} Painel de planos publicado neste canal.", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(PlanosCog(bot))

