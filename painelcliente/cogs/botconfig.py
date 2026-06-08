"""
/botconfig — configura o bot no servidor.

Comportamento por contexto:
- No SERVIDOR ADMIN (GUILD_ID): painel COMPLETO.
  Cargo suporte, categoria tickets, todos os logs, preço de sala e nick.
- Em SERVIDORES DE FORA: painel SÓ-TICKET.
  Apenas cargo de suporte, categoria de tickets, logs de tickets e nick do bot.

Logs configuráveis (apenas no painel completo):
- Tickets, Vendas, Saldo, Sala Teste, Sala Bônus, Ranking
"""

import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow

from utils import database
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, ROLES, SWORD, VISION
from utils.emojis import button_emoji as _emoji
from utils.scope import guild_app, is_admin_guild


# ═══════════════════════════════════════════════════════════════════
# Helpers de exibição
# ═══════════════════════════════════════════════════════════════════

def _mostra(guild: discord.Guild, label, valor, tipo="canal"):
    if not valor:
        return f"{AWAITING} **{label}:** `não configurado`"
    if tipo == "canal":
        ch = guild.get_channel(valor)
        return f"{PRESENTE} **{label}:** {ch.mention if ch else f'`canal removido ({valor})`'}"
    if tipo == "categoria":
        cat = guild.get_channel(valor)
        return f"{PRESENTE} **{label}:** `{cat.name if cat else f'categoria removida ({valor})'}`"
    if tipo == "cargo":
        r = guild.get_role(valor)
        return f"{PRESENTE} **{label}:** {r.mention if r else f'`cargo removido ({valor})`'}"
    return f"{PRESENTE} **{label}:** `{valor}`"


# ═══════════════════════════════════════════════════════════════════
# Painel COMPLETO — apenas no servidor admin
# ═══════════════════════════════════════════════════════════════════

def montar_view_config(config: dict, guild: discord.Guild) -> "ConfigPainelView":
    view = ConfigPainelView()
    container = Container(accent_colour=discord.Colour.from_str("#5865F2"))

    container.add_item(TextDisplay(f"# {CHANNEL} Configurações do Bot"))
    container.add_item(Separator())

    def mostra(label, valor, tipo="canal"):
        return _mostra(guild, label, valor, tipo)

    container.add_item(TextDisplay(
        f"### {ROLES} Suporte\n"
        f"{mostra('Cargo Suporte', config.get('cargo_suporte_id'), 'cargo')}\n"
        f"{mostra('Cargo Cliente', config.get('cargo_cliente_id'), 'cargo')}\n"
        f"{mostra('Categoria Tickets', config.get('categoria_tickets_id'), 'categoria')}"
    ))

    container.add_item(TextDisplay(
        f"### {CLOUD} Canais de Logs — Principais\n"
        f"{mostra('Tickets', config.get('canal_logs_tickets_id'))}\n"
        f"{mostra('Vendas', config.get('canal_logs_vendas_id'))}\n"
        f"{mostra('Saldo', config.get('canal_logs_saldo_id'))}\n"
        f"{mostra('Compras', config.get('canal_logs_compras_id'))}"
    ))

    container.add_item(TextDisplay(
        f"### {SWORD} Canais de Logs — Grátis\n"
        f"{mostra('Sala Teste', config.get('canal_logs_teste_id'))}\n"
        f"{mostra('Sala Bônus', config.get('canal_logs_bonus_id'))}\n"
        f"{mostra('Ranking', config.get('canal_logs_ranking_id'))}"
    ))

    container.add_item(TextDisplay(
        f"### {DOLLAR} Painel Discord\n"
        f"{PRESENTE} **Preço por sala:** `R$ {float(config.get('preco_sala_discord') or 0.07):.2f}`"
    ))

    container.add_item(TextDisplay(
        f"### {DOLLAR} Planos (/planos)\n"
        f"{PRESENTE} **{config.get('plano_semanal_nome') or 'Semanal'}:** "
        f"`{config.get('plano_semanal_preco') or 'R$ 20,99'}`\n"
        f"{PRESENTE} **{config.get('plano_mensal_nome') or '1 Mês'}:** "
        f"`{config.get('plano_mensal_preco') or 'R$ 60,00'}`"
    ))

    container.add_item(TextDisplay(
        f"### {VISION} Identidade\n"
        f"{PRESENTE} **Nick do bot:** `{guild.me.display_name}`"
    ))

    container.add_item(TextDisplay(
        f"### {DOLLAR} Pagamentos (MisticPay)\n"
        f"{PRESENTE} **Credenciais:** "
        f"`{'configuradas' if config.get('_misticpay_ok') else 'não configuradas'}`"
    ))

    container.add_item(Separator())
    container.add_item(TextDisplay(f"-# {VISION} Use os botões abaixo para alterar"))

    view.add_item(container)

    # Linha 0: cargo suporte + cargo cliente + categoria
    view.add_item(_row(
        _btn("Cargo Suporte", "person", discord.ButtonStyle.primary,
             _abrir_select(SelecionarCargo("cargo_suporte_id"))),
        _btn("Cargo Cliente", "person", discord.ButtonStyle.primary,
             _abrir_select(SelecionarCargo("cargo_cliente_id"))),
        _btn("Cat. Tickets", "box", discord.ButtonStyle.primary,
             _abrir_select(SelecionarCategoria("categoria_tickets_id"))),
    ))
    view.add_item(_row(
        _btn("Logs Tickets", "cloud", discord.ButtonStyle.secondary,
             _abrir_select(SelecionarCanal("canal_logs_tickets_id"))),
        _btn("Logs Vendas", "wallet", discord.ButtonStyle.secondary,
             _abrir_select(SelecionarCanal("canal_logs_vendas_id"))),
        _btn("Logs Saldo", "database", discord.ButtonStyle.secondary,
             _abrir_select(SelecionarCanal("canal_logs_saldo_id"))),
        _btn("Logs Compras", "wallet", discord.ButtonStyle.secondary,
             _abrir_select(SelecionarCanal("canal_logs_compras_id"))),
    ))

    # Linha 2: logs grátis
    view.add_item(_row(
        _btn("Logs Teste", "trophy", discord.ButtonStyle.success,
             _abrir_select(SelecionarCanal("canal_logs_teste_id"))),
        _btn("Logs Bônus", "wallet", discord.ButtonStyle.success,
             _abrir_select(SelecionarCanal("canal_logs_bonus_id"))),
        _btn("Logs Ranking", "trophy", discord.ButtonStyle.success,
             _abrir_select(SelecionarCanal("canal_logs_ranking_id"))),
    ))

    # Linha 3: preço da sala + nick
    view.add_item(_row(
        _btn("Preço por Sala", "dollar", discord.ButtonStyle.danger,
             _abrir_modal_preco),
        _btn("Nick do Bot", "vision", discord.ButtonStyle.secondary,
             _abrir_modal_nick),
    ))

    # Linha 4: edição dos planos + credenciais MisticPay
    view.add_item(_row(
        _btn("Plano Semanal", "dollar", discord.ButtonStyle.primary,
             _abrir_modal_plano("semanal")),
        _btn("Plano Mensal", "dollar", discord.ButtonStyle.primary,
             _abrir_modal_plano("mensal")),
        _btn("MisticPay", "pix", discord.ButtonStyle.danger,
             _abrir_modal_misticpay),
    ))

    return view


# ═══════════════════════════════════════════════════════════════════
# Painel SÓ-TICKET — servidores de fora
# ═══════════════════════════════════════════════════════════════════

def montar_view_config_ticket(config: dict, guild: discord.Guild) -> "ConfigPainelView":
    view = ConfigPainelView()
    container = Container(accent_colour=discord.Colour.from_str("#5865F2"))

    container.add_item(TextDisplay(f"# {CHANNEL} Configurações — Tickets"))
    container.add_item(Separator())

    def mostra(label, valor, tipo="canal"):
        return _mostra(guild, label, valor, tipo)

    container.add_item(TextDisplay(
        f"### {ROLES} Suporte\n"
        f"{mostra('Cargo Suporte', config.get('cargo_suporte_id'), 'cargo')}\n"
        f"{mostra('Cargo Cliente', config.get('cargo_cliente_id'), 'cargo')}\n"
        f"{mostra('Categoria Tickets', config.get('categoria_tickets_id'), 'categoria')}"
    ))

    container.add_item(TextDisplay(
        f"### {CLOUD} Canais de Logs\n"
        f"{mostra('Logs Tickets', config.get('canal_logs_tickets_id'))}\n"
        f"{mostra('Logs Compras', config.get('canal_logs_compras_id'))}"
    ))

    container.add_item(TextDisplay(
        f"### {VISION} Identidade\n"
        f"{PRESENTE} **Nick do bot:** `{guild.me.display_name}`"
    ))

    container.add_item(TextDisplay(
        f"### {DOLLAR} Pagamentos (MisticPay)\n"
        f"{PRESENTE} **Credenciais:** "
        f"`{'configuradas' if config.get('_misticpay_ok') else 'não configuradas'}`"
    ))

    container.add_item(Separator())
    container.add_item(TextDisplay(f"-# {VISION} Use os botões abaixo para alterar"))

    view.add_item(container)

    # Linha 0: cargo suporte + cargo cliente + categoria
    view.add_item(_row(
        _btn("Cargo Suporte", "person", discord.ButtonStyle.primary,
             _abrir_select(SelecionarCargo("cargo_suporte_id"))),
        _btn("Cargo Cliente", "person", discord.ButtonStyle.primary,
             _abrir_select(SelecionarCargo("cargo_cliente_id"))),
        _btn("Cat. Tickets", "box", discord.ButtonStyle.primary,
             _abrir_select(SelecionarCategoria("categoria_tickets_id"))),
    ))

    # Linha 1: logs tickets + logs compras + nick
    view.add_item(_row(
        _btn("Logs Tickets", "cloud", discord.ButtonStyle.secondary,
             _abrir_select(SelecionarCanal("canal_logs_tickets_id"))),
        _btn("Logs Compras", "wallet", discord.ButtonStyle.secondary,
             _abrir_select(SelecionarCanal("canal_logs_compras_id"))),
        _btn("Nick do Bot", "vision", discord.ButtonStyle.secondary,
             _abrir_modal_nick),
    ))

    # Linha 2: credenciais MisticPay
    view.add_item(_row(
        _btn("MisticPay", "pix", discord.ButtonStyle.danger,
             _abrir_modal_misticpay),
    ))

    return view


# ═══════════════════════════════════════════════════════════════════
# Modais
# ═══════════════════════════════════════════════════════════════════

async def _abrir_modal_preco(interaction: discord.Interaction):
    config = await database.get_guild_config(interaction.guild_id)
    atual = float(config.get("preco_sala_discord") or 0.07)
    await interaction.response.send_modal(PrecoSalaModal(atual))


async def _abrir_modal_nick(interaction: discord.Interaction):
    atual = interaction.guild.me.display_name if interaction.guild else ""
    await interaction.response.send_modal(NickBotModal(atual))


class PrecoSalaModal(discord.ui.Modal, title="Preço por Sala (Discord)"):
    preco = discord.ui.TextInput(
        label="Preço em R$ (ex: 0.07)",
        placeholder="0.07",
        required=True,
        max_length=10,
    )

    def __init__(self, atual: float):
        super().__init__()
        self.preco.default = f"{atual:.2f}"

    async def on_submit(self, interaction: discord.Interaction):
        valor_str = self.preco.value.strip().replace(",", ".")
        try:
            valor = float(valor_str)
            if valor <= 0:
                raise ValueError("preço deve ser positivo")
        except ValueError:
            return await interaction.response.send_message(
                f"{AWAITING} Valor inválido. Use formato `0.07` ou `0,07`.",
                ephemeral=True,
            )
        await database.update_guild_config(
            interaction.guild_id, preco_sala_discord=valor
        )
        await interaction.response.send_message(
            f"{SWORD} Preço atualizado: `R$ {valor:.2f}` por sala.",
            ephemeral=True,
        )


class NickBotModal(discord.ui.Modal, title="Nick do Bot neste Servidor"):
    nick = discord.ui.TextInput(
        label="Novo nome (deixe vazio p/ resetar)",
        placeholder="Ex: Suporte FF",
        required=False,
        max_length=32,
    )

    def __init__(self, atual: str):
        super().__init__()
        self.nick.default = atual

    async def on_submit(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                f"{AWAITING} Só funciona dentro de um servidor.", ephemeral=True
            )
        novo = self.nick.value.strip()
        try:
            # nick="" ou None reseta para o nome global do bot
            await guild.me.edit(nick=novo or None, reason=f"Nick alterado por {interaction.user}")
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"{AWAITING} Não tenho permissão pra mudar meu apelido. "
                f"Conceda **Alterar Apelido** (Change Nickname) ao bot.",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.response.send_message(
                f"{AWAITING} Erro ao alterar nick: `{e}`", ephemeral=True
            )
        if novo:
            await interaction.response.send_message(
                f"{SWORD} Nick atualizado para `{novo}`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"{SWORD} Nick resetado para o nome padrão do bot.", ephemeral=True
            )


# ─── Edição dos planos do /planos ─────────────────────────────────

# chave do plano → (campo nome no banco, campo preço no banco, defaults)
_PLANOS_CFG = {
    "semanal": ("plano_semanal_nome", "plano_semanal_preco", "Semanal", "R$ 20,99"),
    "mensal": ("plano_mensal_nome", "plano_mensal_preco", "1 Mês", "R$ 60,00"),
}


def _abrir_modal_plano(chave: str):
    async def cb(interaction: discord.Interaction):
        config = await database.get_guild_config(interaction.guild_id)
        campo_nome, campo_preco, def_nome, def_preco = _PLANOS_CFG[chave]
        nome_atual = config.get(campo_nome) or def_nome
        preco_atual = config.get(campo_preco) or def_preco
        await interaction.response.send_modal(
            PlanoModal(chave, nome_atual, preco_atual)
        )
    return cb


class PlanoModal(discord.ui.Modal):
    nome = discord.ui.TextInput(
        label="Nome do plano",
        placeholder="Ex: Semanal",
        required=True,
        max_length=80,
    )
    preco = discord.ui.TextInput(
        label="Preço (texto livre, ex: R$ 20,99)",
        placeholder="R$ 20,99",
        required=True,
        max_length=30,
    )

    def __init__(self, chave: str, nome_atual: str, preco_atual: str):
        super().__init__(title=f"Editar Plano — {chave.capitalize()}")
        self.chave = chave
        self.nome.default = nome_atual
        self.preco.default = preco_atual

    async def on_submit(self, interaction: discord.Interaction):
        campo_nome, campo_preco, _, _ = _PLANOS_CFG[self.chave]
        novo_nome = self.nome.value.strip()
        novo_preco = self.preco.value.strip()
        if not novo_nome or not novo_preco:
            return await interaction.response.send_message(
                f"{AWAITING} Nome e preço não podem ficar vazios.",
                ephemeral=True,
            )
        await database.update_guild_config(
            interaction.guild_id,
            **{campo_nome: novo_nome, campo_preco: novo_preco},
        )
        await interaction.response.send_message(
            f"{SWORD} Plano atualizado: **{novo_nome}** — `{novo_preco}`.\n"
            f"-# Publique o painel de novo com `/planos` para aplicar.",
            ephemeral=True,
        )


# ─── Credenciais do MisticPay (config global) ─────────────────────

async def _abrir_modal_misticpay(interaction: discord.Interaction):
    ci, cs = await database.get_credenciais_misticpay()
    await interaction.response.send_modal(MisticPayModal(ci or "", cs or ""))


class MisticPayModal(discord.ui.Modal, title="Credenciais MisticPay"):
    client_id = discord.ui.TextInput(
        label="Client ID (ci)",
        placeholder="Cole aqui seu Client ID",
        required=True,
        max_length=200,
    )
    client_secret = discord.ui.TextInput(
        label="Client Secret (cs)",
        placeholder="Cole aqui seu Client Secret",
        required=True,
        max_length=200,
    )

    def __init__(self, ci_atual: str, cs_atual: str):
        super().__init__()
        self.client_id.default = ci_atual
        self.client_secret.default = cs_atual

    async def on_submit(self, interaction: discord.Interaction):
        ci = self.client_id.value.strip()
        cs = self.client_secret.value.strip()
        if not ci or not cs:
            return await interaction.response.send_message(
                f"{AWAITING} Client ID e Client Secret são obrigatórios.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Testa as credenciais antes de salvar
        from utils.misticpay import testar_credenciais
        ok, msg = await testar_credenciais(ci, cs)
        if not ok:
            return await interaction.followup.send(
                f"{AWAITING} Não salvei — {msg}", ephemeral=True
            )

        await database.set_credenciais_misticpay(ci, cs)
        await interaction.followup.send(
            f"{SWORD} Credenciais MisticPay salvas e validadas.\n{msg}",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# View base + helpers de botões
# ═══════════════════════════════════════════════════════════════════

class ConfigPainelView(LayoutView):
    def __init__(self):
        super().__init__(timeout=300)


def _btn(label, emoji_name, style, callback):
    btn = discord.ui.Button(label=label, style=style, emoji=_emoji(emoji_name))
    btn.callback = callback
    return btn


def _row(*botoes) -> ActionRow:
    row = ActionRow()
    for b in botoes:
        row.add_item(b)
    return row


def _abrir_select(child_view):
    async def cb(interaction: discord.Interaction):
        await interaction.response.send_message(
            f"{PRESENTE} Selecione abaixo:",
            view=child_view,
            ephemeral=True,
        )
    return cb


# ═══════════════════════════════════════════════════════════════════
# Selects
# ═══════════════════════════════════════════════════════════════════

class SelecionarCanal(discord.ui.View):
    def __init__(self, campo: str):
        super().__init__(timeout=120)
        self.campo = campo
        self.add_item(self._select())

    def _select(self):
        select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="Escolha o canal...",
            min_values=1,
            max_values=1,
        )
        select.callback = self._callback
        return select

    async def _callback(self, interaction: discord.Interaction):
        canal_id = int(interaction.data["values"][0])
        await database.update_guild_config(interaction.guild_id, **{self.campo: canal_id})
        ch = interaction.guild.get_channel(canal_id)
        await interaction.response.edit_message(
            content=f"{SWORD} Canal configurado: {ch.mention if ch else canal_id}",
            view=None,
        )


class SelecionarCategoria(discord.ui.View):
    def __init__(self, campo: str):
        super().__init__(timeout=120)
        self.campo = campo
        self.add_item(self._select())

    def _select(self):
        select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.category],
            placeholder="Escolha a categoria...",
            min_values=1,
            max_values=1,
        )
        select.callback = self._callback
        return select

    async def _callback(self, interaction: discord.Interaction):
        cat_id = int(interaction.data["values"][0])
        await database.update_guild_config(interaction.guild_id, **{self.campo: cat_id})
        c = interaction.guild.get_channel(cat_id)
        await interaction.response.edit_message(
            content=f"{SWORD} Categoria configurada: `{c.name if c else cat_id}`",
            view=None,
        )


class SelecionarCargo(discord.ui.View):
    def __init__(self, campo: str):
        super().__init__(timeout=120)
        self.campo = campo
        self.add_item(self._select())

    def _select(self):
        select = discord.ui.RoleSelect(
            placeholder="Escolha o cargo...",
            min_values=1,
            max_values=1,
        )
        select.callback = self._callback
        return select

    async def _callback(self, interaction: discord.Interaction):
        role_id = int(interaction.data["values"][0])
        await database.update_guild_config(interaction.guild_id, **{self.campo: role_id})
        r = interaction.guild.get_role(role_id)
        await interaction.response.edit_message(
            content=f"{SWORD} Cargo configurado: {r.mention if r else role_id}",
            view=None,
        )


# ═══════════════════════════════════════════════════════════════════
# COG
# ═══════════════════════════════════════════════════════════════════

class BotConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="botconfig",
        description="Configura o bot neste servidor (suporte, tickets, logs).",
    )
    @guild_app()
    @app_commands.default_permissions(administrator=True)
    async def botconfig(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "Este comando só funciona dentro de um servidor.", ephemeral=True
            )
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "Apenas administradores.", ephemeral=True
            )

        # Em servidores de FORA, só o dono autorizado pode usar o /botconfig.
        # No servidor admin, qualquer administrador continua liberado.
        DONO_AUTORIZADO_ID = 1268379167519408139
        if not is_admin_guild(interaction) and interaction.user.id != DONO_AUTORIZADO_ID:
            return await interaction.response.send_message(
                "Você não tem permissão para usar este comando aqui.",
                ephemeral=True,
            )

        config = await database.get_guild_config(interaction.guild_id)

        # Marca se as credenciais MisticPay já estão configuradas (global).
        _ci, _cs = await database.get_credenciais_misticpay()
        config = dict(config)
        config["_misticpay_ok"] = bool(_ci and _cs)

        # Servidor admin → painel completo; servidores de fora → só ticket.
        if is_admin_guild(interaction):
            view = montar_view_config(config, interaction.guild)
        else:
            view = montar_view_config_ticket(config, interaction.guild)

        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(BotConfigCog(bot))
