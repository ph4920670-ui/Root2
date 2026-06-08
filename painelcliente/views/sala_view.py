"""
Painel de sala criada — Components V2.

Painel principal mostra modo, jogadores e equipes ao vivo.
ID e SENHA NÃO aparecem aqui — o usuário copia pelo menu "Outros".
"""

import asyncio
import discord
from discord.ui import LayoutView, Container, Section, TextDisplay, Separator, ActionRow
from utils import database
from utils import salasff
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, ROLES, SWORD, VISION, button_emoji


# Polling intervalos (segundos)
POLL_ANTES_CRIAR = 3
POLL_DEPOIS_CRIAR = 8
POLL_TIMEOUT_CRIAR = 60


# ═══════════════════════════════════════════════════════════════════
# RENDERIZAÇÃO DO PAINEL
# ═══════════════════════════════════════════════════════════════════

def montar_painel_sala(info: dict, modo_nome: str, criador_id: int, infinito: bool) -> "PainelSalaView":
    """LayoutView com info da sala + botões Iniciar / Outros."""
    view = PainelSalaView(info=info, modo_nome=modo_nome, criador_id=criador_id, infinito=infinito)
    container = Container(accent_colour=discord.Colour.from_str("#FFB300"))

    status = info.get("status", 0)
    sala = info.get("sala") or {}
    pedidoid = info.get("pedidoid", "")

    # Cabeçalho
    titulo = f"{VISION} Sala Free Fire"
    if infinito:
        titulo += f" {CLOUD} `MODO INFINITO`"
    container.add_item(TextDisplay(f"# {titulo}"))
    container.add_item(Separator())

    if status == 2:
        container.add_item(TextDisplay(
            f"{AWAITING} **Criando sala...** aguarde alguns segundos."
        ))
    elif status == 3:
        nome_real = sala.get("nome") or modo_nome
        total_jogadores = salasff.contar_jogadores(info)
        inicio_auto = info.get("inicio_automatico", "")

        container.add_item(TextDisplay(
            f"{PRESENTE} **Modo:** {nome_real}\n"
            f"{ROLES} **Jogadores:** `{total_jogadores}`\n"
            f"{AWAITING} **Início automático:** `{inicio_auto or 'em breve'}`"
        ))

        # Equipes detalhadas
        equipes = sala.get("equipes", [])
        if equipes and any(e.get("jogadores") for e in equipes):
            container.add_item(Separator())
            for eq in equipes:
                jogadores = eq.get("jogadores", [])
                if not jogadores:
                    continue
                linhas = []
                for j in jogadores:
                    nick = j.get("nickname", "?")
                    patente = j.get("patente", "?")
                    pontos = j.get("pontos", 0)
                    emu = f" {CLOUD}" if j.get("emulador") else ""
                    linhas.append(f"{ROLES} `{nick}` · {patente} · {pontos} pts{emu}")
                container.add_item(TextDisplay(
                    f"### {SWORD} Equipe {eq.get('numero', '?')}\n" + "\n".join(linhas)
                ))

        container.add_item(Separator())

        # BOTÕES dentro do container (Sections)
        sec_iniciar = Section(accessory=IniciarSalaButton(pedidoid))
        sec_iniciar.add_item(TextDisplay(f"{SWORD} **Iniciar Sala** — começa a partida"))
        container.add_item(sec_iniciar)

        sec_outros = Section(accessory=OutrosButton(pedidoid))
        sec_outros.add_item(TextDisplay(f"{CLOUD} **Outros** — status, expulsar, atualizar"))
        container.add_item(sec_outros)

        sec_copiar = Section(accessory=CopiarIdSenhaButton(pedidoid))
        sec_copiar.add_item(TextDisplay(f"{ROLES} **Copiar ID/Senha** — recebe no privado"))
        container.add_item(sec_copiar)

        container.add_item(TextDisplay(f"-# Criado por <@{criador_id}>"))

    elif status == 4:
        container.add_item(TextDisplay(
            f"{SWORD} **Sala iniciada!** A partida começou.\n"
            f"{PRESENTE} **Modo:** {modo_nome}"
        ))
        if infinito:
            container.add_item(TextDisplay(
                f"{AWAITING} Criando próxima sala do modo infinito..."
            ))
    else:
        container.add_item(TextDisplay(
            f"{AWAITING} **Status:** `{info.get('msg', 'desconhecido')}`"
        ))

    view.add_item(container)
    return view


def montar_mensagem_credenciais(info: dict) -> str:
    """Texto da 2ª mensagem ephemeral com ID e SENHA copiáveis."""
    sala = info.get("sala") or {}
    sala_id = sala.get("id", "—")
    senha = sala.get("senha", "—")
    return (
        f"## {VISION} Credenciais\n"
        f"**ID:**\n```\n{sala_id}\n```\n"
        f"**SENHA:**\n```\n{senha}\n```"
    )


# ═══════════════════════════════════════════════════════════════════
# View — agora guarda contexto pros botões
# ═══════════════════════════════════════════════════════════════════

class PainelSalaView(LayoutView):
    def __init__(self, info=None, modo_nome="", criador_id=0, infinito=False):
        super().__init__(timeout=None)
        self.info = info
        self.modo_nome = modo_nome
        self.criador_id = criador_id
        self.infinito = infinito


# ═══════════════════════════════════════════════════════════════════
# Botões do painel
# ═══════════════════════════════════════════════════════════════════

class IniciarSalaButton(discord.ui.Button):
    """Inicia a sala manualmente (chama /iniciar da API)."""
    def __init__(self, pedidoid: str):
        super().__init__(
            label="Iniciar",
            style=discord.ButtonStyle.success,
            emoji=button_emoji("trophy"),
        )
        self.pedidoid = pedidoid

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        resp = await salasff.iniciar_sala(self.pedidoid)
        if resp and resp.get("success"):
            await interaction.followup.send(
                f"{SWORD} Sala iniciada com sucesso!", ephemeral=True
            )
        else:
            msg = (resp or {}).get("msg") \
                  or (resp or {}).get("error") \
                  or "API não respondeu."
            await interaction.followup.send(
                f"{AWAITING} Não consegui iniciar: `{msg}`", ephemeral=True
            )


class OutrosButton(discord.ui.Button):
    """Abre menu com Status / Expulsar / Atualizar."""
    def __init__(self, pedidoid: str):
        super().__init__(
            label="Outros",
            style=discord.ButtonStyle.secondary,
            emoji=button_emoji("cloud"),
        )
        self.pedidoid = pedidoid

    async def callback(self, interaction: discord.Interaction):
        # Components V2 não aceita content+view no mesmo send → só view.
        view = OutrosMenuView(self.pedidoid)
        await interaction.response.send_message(view=view, ephemeral=True)


# ═══════════════════════════════════════════════════════════════════
# Menu "Outros"
# ═══════════════════════════════════════════════════════════════════

class OutrosMenuView(LayoutView):
    def __init__(self, pedidoid: str):
        super().__init__(timeout=300)
        container = Container(accent_colour=discord.Colour.from_str("#5865F2"))
        container.add_item(TextDisplay(f"# {CLOUD} Outros — Sala"))
        container.add_item(Separator())

        sec_status = Section(accessory=StatusButton(pedidoid))
        sec_status.add_item(TextDisplay(f"{VISION} **Status** — quantos jogadores, info ao vivo"))
        container.add_item(sec_status)

        sec_expulsar = Section(accessory=ExpulsarButton(pedidoid))
        sec_expulsar.add_item(TextDisplay(f"{AWAITING} **Expulsar jogador** (por nick/ID)"))
        container.add_item(sec_expulsar)

        sec_atualizar = Section(accessory=AtualizarButton(pedidoid))
        sec_atualizar.add_item(TextDisplay(f"{CHANNEL} **Atualizar** — recarrega info da API"))
        container.add_item(sec_atualizar)

        self.add_item(container)


class StatusButton(discord.ui.Button):
    def __init__(self, pedidoid: str):
        super().__init__(label="Status", style=discord.ButtonStyle.primary, emoji=button_emoji("vision"))
        self.pedidoid = pedidoid

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        info = await salasff.info_sala(self.pedidoid)
        if not info or not info.get("success"):
            return await interaction.followup.send(
                f"{AWAITING} API não respondeu.", ephemeral=True
            )
        sala = info.get("sala") or {}
        total = salasff.contar_jogadores(info)
        await interaction.followup.send(
            f"## {VISION} Status\n"
            f"{PRESENTE} **Modo:** {sala.get('nome', '?')}\n"
            f"{ROLES} **Jogadores:** `{total}`\n"
            f"{AWAITING} **Status:** `{info.get('status', '?')}`\n"
            f"{CLOUD} **Início:** `{info.get('inicio_automatico', '?')}`",
            ephemeral=True,
        )


class ExpulsarButton(discord.ui.Button):
    def __init__(self, pedidoid: str):
        super().__init__(label="Expulsar", style=discord.ButtonStyle.danger, emoji=button_emoji("awaiting"))
        self.pedidoid = pedidoid

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ExpulsarModal(self.pedidoid))


class ExpulsarModal(discord.ui.Modal, title="Expulsar Jogador"):
    jogador_id = discord.ui.TextInput(
        label="ID do jogador",
        placeholder="Cole o ID do jogador",
        required=True,
        max_length=30,
    )

    def __init__(self, pedidoid: str):
        super().__init__()
        self.pedidoid = pedidoid

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        resp = await salasff.expulsar_jogador(self.pedidoid, self.jogador_id.value.strip())
        if resp and resp.get("success"):
            await interaction.followup.send(
                f"{SWORD} Jogador `{self.jogador_id.value}` expulso!", ephemeral=True
            )
        else:
            msg = (resp or {}).get("msg") or (resp or {}).get("error") or "API não respondeu."
            await interaction.followup.send(
                f"{AWAITING} Erro: `{msg}`", ephemeral=True
            )


class AtualizarButton(discord.ui.Button):
    """Recarrega info da API e mostra resumo atualizado."""
    def __init__(self, pedidoid: str):
        super().__init__(label="Atualizar", style=discord.ButtonStyle.secondary, emoji=button_emoji("channel"))
        self.pedidoid = pedidoid

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        info = await salasff.info_sala(self.pedidoid)
        if not info or not info.get("success"):
            return await interaction.followup.send(
                f"{AWAITING} API não respondeu.", ephemeral=True
            )
        sala = info.get("sala") or {}
        total = salasff.contar_jogadores(info)

        # Equipes
        equipes_txt = ""
        for eq in sala.get("equipes", []):
            jogadores = eq.get("jogadores", [])
            if not jogadores:
                continue
            linhas = []
            for j in jogadores:
                nick = j.get("nickname", "?")
                patente = j.get("patente", "?")
                pontos = j.get("pontos", 0)
                emu = f" {CLOUD}" if j.get("emulador") else ""
                linhas.append(f"{ROLES} `{nick}` · {patente} · {pontos} pts{emu}")
            equipes_txt += f"\n### {SWORD} Equipe {eq.get('numero', '?')}\n" + "\n".join(linhas)

        await interaction.followup.send(
            f"## {CHANNEL} Atualizado\n"
            f"{PRESENTE} **Modo:** {sala.get('nome', '?')}\n"
            f"{ROLES} **Jogadores:** `{total}`\n"
            f"{AWAITING} **Início:** `{info.get('inicio_automatico', '?')}`"
            f"{equipes_txt}",
            ephemeral=True,
        )


class CopiarIdSenhaButton(discord.ui.Button):
    """Manda ID e SENHA em uma mensagem ephemeral simples pro user copiar."""
    def __init__(self, pedidoid: str):
        super().__init__(label="Copiar ID/Senha", style=discord.ButtonStyle.secondary, emoji=button_emoji("roles"))
        self.pedidoid = pedidoid

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        info = await salasff.info_sala(self.pedidoid)
        if not info or not info.get("success"):
            return await interaction.followup.send(
                f"{AWAITING} API não respondeu.", ephemeral=True
            )
        sala = info.get("sala") or {}
        sala_id = sala.get("id", "—")
        senha = sala.get("senha", "—")
        # Formato pedido: só ID e senha, sem rótulos, em bloco copiável.
        await interaction.followup.send(
            f"```\n{sala_id}\n{senha}\n```",
            ephemeral=True,
        )


# ═══════════════════════════════════════════════════════════════════
# WORKER DE POLLING (atualiza a mensagem do painel ao vivo)
# ═══════════════════════════════════════════════════════════════════

async def acompanhar_sala(
    bot: discord.Client,
    pedidoid: str,
    salaid: str,
    modo_nome: str,
    channel_id: int,
    message_id: int,
    user_id: int,
    guild_id: int,
    infinito: bool,
):
    """
    Faz polling da API. Se message_id != 0, atualiza a mensagem.
    Se infinito=True, cria nova sala automaticamente quando a atual iniciar.
    """
    # Como o painel agora é ephemeral (message_id=0), não atualizamos a UI.
    # Mantemos só o polling pra modo infinito disparar nova sala.
    message = None
    if message_id and channel_id:
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
        except Exception:
            message = None

    # Fase 1: aguardar status 3
    tentativas = 0
    max_tentativas = POLL_TIMEOUT_CRIAR // POLL_ANTES_CRIAR

    while tentativas < max_tentativas:
        info = await salasff.info_sala(pedidoid)
        if info is None:
            tentativas += 1
            await asyncio.sleep(POLL_ANTES_CRIAR)
            continue

        if message is not None:
            try:
                view = montar_painel_sala(info, modo_nome, user_id, infinito)
                await message.edit(view=view)
            except Exception as e:
                print(f"⚠️ Erro ao atualizar painel: {e}")

        if info.get("status") == 3:
            break

        if info.get("status", 0) >= 4:
            await database.finalizar_sala(pedidoid)
            return

        tentativas += 1
        await asyncio.sleep(POLL_ANTES_CRIAR)
    else:
        # Timeout — sala não foi criada
        try:
            timeout_view = PainelSalaView()
            container = Container(accent_colour=discord.Colour.red())
            container.add_item(TextDisplay(
                f"# {AWAITING} Falha ao criar sala\n"
                f"A sala não foi criada em {POLL_TIMEOUT_CRIAR}s. Tente novamente."
            ))
            timeout_view.add_item(container)
            if message is not None:
                await message.edit(view=timeout_view)
        except Exception:
            pass
        await database.finalizar_sala(pedidoid)
        return

    # Fase 2: monitorar até iniciar (status 4)
    while True:
        await asyncio.sleep(POLL_DEPOIS_CRIAR)
        info = await salasff.info_sala(pedidoid)
        if info is None:
            continue

        status = info.get("status", 0)

        try:
            if message is not None:
                view = montar_painel_sala(info, modo_nome, user_id, infinito)
                await message.edit(view=view)
        except Exception as e:
            print(f"⚠️ Erro ao atualizar painel: {e}")

        if status >= 4:
            await database.finalizar_sala(pedidoid)
            break

    # Fase 3: se infinito, cria a próxima
    if infinito:
        await criar_proxima_sala_infinita(bot, salaid, modo_nome, channel_id, user_id, guild_id)


async def criar_proxima_sala_infinita(
    bot: discord.Client,
    salaid: str,
    modo_nome: str,
    channel_id: int,
    user_id: int,
    guild_id: int,
):
    """Cria a próxima sala do loop infinito (consome saldo do usuário)."""
    # Verifica saldo
    saldo = await database.get_saldo(guild_id, user_id)
    if saldo < 1:
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            sem_saldo = PainelSalaView()
            container = Container(accent_colour=discord.Colour.red())
            container.add_item(TextDisplay(
                f"# {DOLLAR} Saldo insuficiente\n"
                f"<@{user_id}>, seu saldo acabou. O modo infinito foi encerrado.\n"
                f"Compre mais salas usando `/cs` ou o painel de compras."
            ))
            sem_saldo.add_item(container)
            await channel.send(view=sem_saldo)
        except Exception:
            pass
        return

    if not await database.consumir_saldo(guild_id, user_id, 1):
        return

    resp = await salasff.criar_sala(salaid=salaid)
    if not resp or not resp.get("success"):
        # Devolve saldo se a API falhou
        await database.adicionar_saldo(guild_id, user_id, 1)
        return

    pedidoid_novo = resp.get("pedidoid")
    if not pedidoid_novo:
        await database.adicionar_saldo(guild_id, user_id, 1)
        return

    # Envia novo painel
    try:
        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        view = montar_painel_sala(resp, modo_nome, user_id, infinito=True)
        msg = await channel.send(view=view)

        await database.registrar_sala(
            pedidoid=pedidoid_novo,
            guild_id=guild_id,
            user_id=user_id,
            channel_id=channel_id,
            message_id=msg.id,
            salaid=salaid,
            infinito=True,
        )

        # Stats: registra a nova sala criada
        await database.registrar_sala_criada(guild_id, user_id)

        # Continua o loop
        asyncio.create_task(acompanhar_sala(
            bot=bot,
            pedidoid=pedidoid_novo,
            salaid=salaid,
            modo_nome=modo_nome,
            channel_id=channel_id,
            message_id=msg.id,
            user_id=user_id,
            guild_id=guild_id,
            infinito=True,
        ))
    except Exception as e:
        print(f"⚠️ Erro ao criar próxima sala infinita: {e}")
        await database.adicionar_saldo(guild_id, user_id, 1)
