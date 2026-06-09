"""
Views Components V2 dos canais grátis:
- PainelTesteView   → #sala-teste (botão "Quero ser Cliente" → +10 salas)
- PainelBonusView   → #sala-bonus (a cada 10 criadas, +2 — botão resgatar)
- PainelRankingView → #ranking (top 3 do dia, reset 00:00 BRT)

Detecção de User Install: o bot só aparece como Application em interactions
quando o usuário tem ele instalado na conta. Verificamos pelo
`interaction.authorizing_integration_owners` (ApplicationIntegrationType.user_install).
"""

import discord
from discord.ui import LayoutView, Container, Section, TextDisplay, Separator, ActionRow
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, ROLES, SWORD, VISION, button_emoji
from utils import database
from utils import logs as _logs


# Alias local mantido pra compatibilidade com chamadas antigas.
_emoji = button_emoji


def _tem_user_install(interaction: discord.Interaction) -> bool:
    """
    Verifica se o usuário instalou o bot na conta dele (User Install).

    Quando o bot é instalado como User App, o Discord adiciona
    ApplicationIntegrationType.user_install em authorizing_integration_owners.
    """
    try:
        owners = interaction.authorizing_integration_owners or {}
        # IntegrationType: 0=guild_install, 1=user_install
        return discord.AppInstallationType.user in owners or "1" in owners
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
# SALA TESTE — botão "Quero Participar" → +10 salas
# ═══════════════════════════════════════════════════════════════════

class PainelTesteView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = Container(accent_colour=discord.Colour.from_str("#43B581"))

        container.add_item(TextDisplay(f"# {SWORD} Salas Teste"))
        container.add_item(Separator())
        container.add_item(TextDisplay(
            f"{CLOUD} **Está indeciso em comprar salas com nossa equipe?**\n"
            f"{ROLES} Teste agora! Receba salas grátis, ganhe bônus por compra "
            f"e aproveite o melhor preço do mercado."
        ))
        container.add_item(Separator())

        # Section com botão dentro do container
        sec = Section(accessory=QueroParticiparButton())
        sec.add_item(TextDisplay(
            f"{ROLES} **Receba 10 salas grátis** diretamente na sua conta."
        ))
        container.add_item(sec)

        container.add_item(TextDisplay(
            f"-# F Applications · Disponibilidade por tempo limitado"
        ))

        self.add_item(container)


class QueroParticiparButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Quero Participar",
            style=discord.ButtonStyle.success,
            emoji=button_emoji("vision"),
            custom_id="teste:quero_cliente",
        )

    async def callback(self, interaction: discord.Interaction):
        # Tenta resgatar direto — sem verificar User Install.
        # Clicou no botão = ganha as 10 salas (cada conta só uma vez).
        gid = interaction.guild_id or 0
        sucesso, qtd = await database.pegar_sala_teste(gid, interaction.user.id)
        if not sucesso:
            return await interaction.response.send_message(
                f"{AWAITING} Você já resgatou suas salas teste antes!\n"
                f"{CLOUD} Cada conta pode pegar só **uma vez**.",
                ephemeral=True,
            )

        saldo = await database.get_saldo(gid, interaction.user.id)

        # 3. Logs
        try:
            await _logs.log_sala_teste(
                interaction.client,
                guild_id=gid,
                user_id=interaction.user.id,
                qtd=qtd,
            )
            await _logs.log_saldo(
                interaction.client,
                guild_id=gid,
                user_id=interaction.user.id,
                motivo="Resgate de sala teste",
                variacao=qtd,
                novo_saldo=saldo,
            )
        except Exception as e:
            print(f"⚠️ Erro log sala teste: {e}")

        await interaction.response.send_message(
            f"{SWORD} **Parabéns!** Você recebeu **{qtd}** salas teste grátis!\n"
            f"{DOLLAR} Seu saldo agora: `{saldo}` sala(s).\n"
            f"{PRESENTE} Use `/c1`, `/c2` ou `/cs` para criar suas salas.",
            ephemeral=True,
        )


def _mention_channel(slug_completo: str) -> str:
    slug = slug_completo.lower().replace("｜", "-").replace(" ", "-")
    return f"`#{slug}`"


# ═══════════════════════════════════════════════════════════════════
# SALA BÔNUS — a cada 10 criadas, +2 salas
# ═══════════════════════════════════════════════════════════════════

class PainelBonusView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = Container(accent_colour=discord.Colour.from_str("#FAA61A"))

        container.add_item(TextDisplay(
            f"# {DOLLAR} Sala Bônus"
        ))

        container.add_item(Separator())

        container.add_item(TextDisplay(
            f"{SWORD} **A cada 10 salas criadas você ganha 2 salas bônus!**\n"
            f"{PRESENTE} Não importa o modo — `/c1`, `/c2` e `/cs` contam todas."
        ))

        container.add_item(TextDisplay(
            f"### {CLOUD} Como funciona\n"
            f"{CHANNEL} Crie 10 salas → ganhe **+2** automaticamente\n"
            f"{CHANNEL} Crie 20 salas → mais **+2** acumula\n"
            f"{CHANNEL} Crie 30 → mais **+2**, e por aí vai!\n"
            f"{AWAITING} Use o botão abaixo pra resgatar todo o bônus disponível."
        ))

        self.add_item(container)
        row = ActionRow()
        row.add_item(VerBonusButton())
        row.add_item(ResgatarBonusButton())
        self.add_item(row)


class VerBonusButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ver Meu Bônus",
            style=discord.ButtonStyle.primary,
            custom_id="bonus:ver",
            emoji=_emoji("vision"),
        )

    async def callback(self, interaction: discord.Interaction):
        stats = await database.get_user_stats(interaction.guild_id, interaction.user.id)
        total = int(stats.get("total_criadas", 0))
        bonus = int(stats.get("bonus_disponivel", 0))
        faltam = database.BONUS_A_CADA_N_SALAS - (total % database.BONUS_A_CADA_N_SALAS)
        if faltam == database.BONUS_A_CADA_N_SALAS:
            faltam = 0

        await interaction.response.send_message(
            f"{CHANNEL} **Salas criadas:** `{total}`\n"
            f"{DOLLAR} **Bônus disponível:** `{bonus}` sala(s)\n"
            f"{AWAITING} **Faltam** `{faltam}` salas pro próximo bônus.",
            ephemeral=True,
        )


class ResgatarBonusButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Resgatar Bônus",
            style=discord.ButtonStyle.success,
            custom_id="bonus:resgatar",
            emoji=_emoji("trophy"),
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        qtd = await database.resgatar_bonus(interaction.guild_id, interaction.user.id)
        if qtd <= 0:
            return await interaction.followup.send(
                f"{AWAITING} Você não tem nenhum bônus disponível.\n"
                f"{PRESENTE} Crie mais salas para ganhar bônus a cada 10.",
                ephemeral=True,
            )
        saldo = await database.get_saldo(interaction.guild_id, interaction.user.id)
        stats = await database.get_user_stats(interaction.guild_id, interaction.user.id)

        # Logs
        try:
            await _logs.log_sala_bonus(
                interaction.client,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                qtd=qtd,
                total_criadas=int(stats.get("total_criadas", 0)),
            )
            await _logs.log_saldo(
                interaction.client,
                guild_id=interaction.guild_id,
                user_id=interaction.user.id,
                motivo="Resgate de bônus (10→2)",
                variacao=qtd,
                novo_saldo=saldo,
            )
        except Exception as e:
            print(f"⚠️ Erro log bônus: {e}")

        await interaction.followup.send(
            f"{SWORD} **Resgatado!** Você recebeu **{qtd}** sala(s) bônus.\n"
            f"{DOLLAR} Saldo atual: `{saldo}` sala(s).",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# RANKING DIÁRIO — top 1: 100 · top 2: 70 · top 3: 50
# ═══════════════════════════════════════════════════════════════════

RANKING_PREMIOS = {1: 100, 2: 50, 3: 30}


def montar_view_ranking(top: list[dict], guild: discord.Guild) -> "PainelRankingView":
    """Monta uma view com o ranking atualizado. NÃO é persistente."""
    view = PainelRankingView()

    container = Container(accent_colour=discord.Colour.from_str("#FFD700"))

    container.add_item(TextDisplay(
        f"# {SWORD} Ranking Diário"
    ))

    container.add_item(Separator())

    container.add_item(TextDisplay(
        f"{PRESENTE} **Prêmios diários** (reset todo dia 00:00 BRT):\n"
        f"{SWORD} **1º lugar** — `100` salas\n"
        f"{SWORD} **2º lugar** — `50` salas\n"
        f"{SWORD} **3º lugar** — `30` salas"
    ))

    container.add_item(Separator())

    # Top atual
    if not top:
        container.add_item(TextDisplay(
            f"### {AWAITING} Ainda ninguém criou salas hoje\n"
            f"{CLOUD} Seja o primeiro! Use `/c1`, `/c2` ou `/cs`."
        ))
    else:
        linhas = []
        for i, entry in enumerate(top[:10], start=1):
            user_id = entry["user_id"]
            qtd = entry["salas_no_dia"]
            membro = guild.get_member(user_id)
            nome = membro.mention if membro else f"<@{user_id}>"

            medalha = ""
            if i == 1:
                medalha = f"{SWORD} "
            elif i == 2:
                medalha = f"{SWORD} "
            elif i == 3:
                medalha = f"{SWORD} "
            else:
                medalha = f"{PRESENTE} "

            premio = RANKING_PREMIOS.get(i, 0)
            premio_txt = f" · prêmio `{premio}` salas" if premio else ""
            linhas.append(f"{medalha}**{i}º** — {nome} · `{qtd}` salas{premio_txt}")

        container.add_item(TextDisplay(
            f"### {CHANNEL} Top do dia\n" + "\n".join(linhas)
        ))

    container.add_item(Separator())
    container.add_item(TextDisplay(
        f"-# {CLOUD} Atualizado · Reset diário à meia-noite (BRT)"
    ))

    view.add_item(container)
    row = ActionRow()
    row.add_item(AtualizarRankingButton())
    view.add_item(row)
    return view


class PainelRankingView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)


class AtualizarRankingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Atualizar Ranking",
            style=discord.ButtonStyle.primary,
            custom_id="ranking:atualizar",
            emoji=_emoji("vision"),
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False, thinking=True)
        top = await database.top_ranking_dia(interaction.guild_id)
        view = montar_view_ranking(top, interaction.guild)
        try:
            await interaction.message.edit(view=view)
            await interaction.followup.send(
                f"{SWORD} Ranking atualizado!", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"{AWAITING} Erro ao atualizar: `{e}`", ephemeral=True
            )


# ═══════════════════════════════════════════════════════════════════
# Painel novo de ranking — 2 embeds clássicos + botões
# Usado pelo .setar quando publica o canal 🏆｜ranking
# ═══════════════════════════════════════════════════════════════════

class PainelRankingFixoView(LayoutView):
    """View persistente publicada no canal 🏆｜ranking — V2 com botões na borda."""

    def __init__(self):
        super().__init__(timeout=None)

        # Container 1 — info do ranking
        c1 = Container(accent_colour=discord.Colour.from_str("#5865F2"))
        c1.add_item(TextDisplay(f"# {SWORD} Ranking Diário — Top Criadores"))
        c1.add_item(Separator())
        c1.add_item(TextDisplay(
            f"Quem criar mais salas no dia entra no top — "
            f"**Top 3** recebe prêmios todo dia à meia-noite (BRT)."
        ))
        c1.add_item(Separator())

        # Sections com botões DENTRO do container
        sec_ranking = Section(accessory=VerRankingButton())
        sec_ranking.add_item(TextDisplay(f"{SWORD} **Ranking** — ver tabela completa"))
        c1.add_item(sec_ranking)

        sec_perfil = Section(accessory=MeuPerfilRankingButton())
        sec_perfil.add_item(TextDisplay(f"{VISION} **Meu Perfil** — ver minha posição"))
        c1.add_item(sec_perfil)

        self.add_item(c1)

        # Container 2 — prêmios
        c2 = Container(accent_colour=discord.Colour.from_str("#FFD700"))
        c2.add_item(TextDisplay(f"# {PRESENTE} Prêmios do Dia"))
        c2.add_item(Separator())
        c2.add_item(TextDisplay(
            f"{SWORD} **Top 1** — `100` salas\n"
            f"{SWORD} **Top 2** — `50` salas\n"
            f"{SWORD} **Top 3** — `30` salas"
        ))
        c2.add_item(TextDisplay(
            f"-# {CLOUD} Reset diário à meia-noite (Brasília)"
        ))
        self.add_item(c2)


class VerRankingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Ranking",
            style=discord.ButtonStyle.primary,
            emoji=button_emoji("trophy"),
            custom_id="ranking_fixo:ver",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        top = await database.top_ranking_dia(interaction.guild_id or 0)
        if not top:
            return await interaction.followup.send(
                f"{AWAITING} Ainda ninguém criou salas hoje. "
                f"Seja o primeiro com `/c1`, `/c2` ou `/cs`!",
                ephemeral=True,
            )

        linhas = []
        for i, entry in enumerate(top[:10], start=1):
            user_id = entry["user_id"]
            qtd = entry["salas_no_dia"]
            membro = interaction.guild.get_member(user_id) if interaction.guild else None
            nome = membro.mention if membro else f"<@{user_id}>"
            premio = RANKING_PREMIOS.get(i, 0)
            premio_txt = f" · {PRESENTE} `{premio}`" if premio else ""
            linhas.append(f"**{i}º** — {nome} · `{qtd}` salas{premio_txt}")

        await interaction.followup.send(
            f"## {SWORD} Top do Dia\n\n" + "\n".join(linhas) +
            f"\n\n-# Atualizado · Reset à meia-noite (BRT)",
            ephemeral=True,
        )


class MeuPerfilRankingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Meu Perfil",
            style=discord.ButtonStyle.secondary,
            emoji=button_emoji("vision"),
            custom_id="ranking_fixo:perfil",
        )

    async def callback(self, interaction: discord.Interaction):
        from views.carteira_view import montar_carteira
        from utils import salasff
        guild_id = interaction.guild_id or 0
        saldo = await database.get_saldo(guild_id, interaction.user.id)
        try:
            saldo_global = await salasff.saldo_global()
        except Exception:
            saldo_global = 0
        view = montar_carteira(interaction.user, saldo, saldo_global)
        await interaction.response.send_message(view=view, ephemeral=True)
