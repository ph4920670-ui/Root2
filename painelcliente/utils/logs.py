"""
Helper pra postar logs nos canais configurados via /botconfig.

Cada função recebe o bot, o guild_id e os dados do evento, lê o canal
configurado em guild_config e posta um Component V2 lá.
"""

import discord
from datetime import datetime
from discord.ui import LayoutView, Container, TextDisplay, Separator
from utils import database
from utils.emojis import AWAITING, CHANNEL, CLOUD, DOLLAR, PRESENTE, ROLES, SWORD


def _agora() -> str:
    return datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")


async def _get_canal_log(bot, guild_id: int, campo: str):
    """Retorna o canal de log configurado, ou None."""
    if not guild_id:
        return None
    config = await database.get_guild_config(guild_id)
    canal_id = config.get(campo)
    if not canal_id:
        return None
    canal = bot.get_channel(canal_id)
    if canal is None:
        try:
            canal = await bot.fetch_channel(canal_id)
        except Exception:
            return None
    return canal


# ═══════════════════════════════════════════════════════════════════
# LOG DE VENDA (PIX confirmado)
# ═══════════════════════════════════════════════════════════════════

async def log_venda(bot, guild_id: int, user_id: int, produto_nome: str,
                    valor: float, salas: int, txid: str):
    canal = await _get_canal_log(bot, guild_id, "canal_logs_vendas_id")
    if canal is None:
        return

    view = LayoutView(timeout=None)
    c = Container(accent_colour=discord.Colour.from_str("#43B581"))
    c.add_item(TextDisplay(f"# {DOLLAR} Venda Confirmada"))
    c.add_item(Separator())
    c.add_item(TextDisplay(
        f"{ROLES} **Cliente:** <@{user_id}>\n"
        f"{PRESENTE} **Produto:** `{produto_nome}`\n"
        f"{CHANNEL} **Salas creditadas:** `{salas}`\n"
        f"{DOLLAR} **Valor:** `R$ {valor:.2f}`\n"
        f"{CLOUD} **TXID:** `{txid}`\n"
        f"{AWAITING} **Data:** `{_agora()}`"
    ))
    view.add_item(c)
    try:
        await canal.send(view=view)
    except Exception as e:
        print(f"⚠️ Erro ao postar log de venda: {e}")


# ═══════════════════════════════════════════════════════════════════
# LOG DE SALDO (mudanças no saldo do usuário)
# ═══════════════════════════════════════════════════════════════════

async def log_saldo(bot, guild_id: int, user_id: int, motivo: str,
                    variacao: int, novo_saldo: int):
    canal = await _get_canal_log(bot, guild_id, "canal_logs_saldo_id")
    if canal is None:
        return

    sinal = "+" if variacao >= 0 else ""
    cor = "#43B581" if variacao >= 0 else "#ED4245"

    view = LayoutView(timeout=None)
    c = Container(accent_colour=discord.Colour.from_str(cor))
    c.add_item(TextDisplay(f"# {CHANNEL} Movimentação de Saldo"))
    c.add_item(Separator())
    c.add_item(TextDisplay(
        f"{ROLES} **Usuário:** <@{user_id}>\n"
        f"{CLOUD} **Motivo:** `{motivo}`\n"
        f"{DOLLAR} **Variação:** `{sinal}{variacao}` sala(s)\n"
        f"{CHANNEL} **Saldo atual:** `{novo_saldo}` sala(s)\n"
        f"{AWAITING} **Data:** `{_agora()}`"
    ))
    view.add_item(c)
    try:
        await canal.send(view=view)
    except Exception as e:
        print(f"⚠️ Erro ao postar log de saldo: {e}")


# ═══════════════════════════════════════════════════════════════════
# LOG SALA TESTE (resgate de 10 salas)
# ═══════════════════════════════════════════════════════════════════

async def log_sala_teste(bot, guild_id: int, user_id: int, qtd: int):
    canal = await _get_canal_log(bot, guild_id, "canal_logs_teste_id")
    if canal is None:
        return

    view = LayoutView(timeout=None)
    c = Container(accent_colour=discord.Colour.from_str("#43B581"))
    c.add_item(TextDisplay(f"# {SWORD} Sala Teste Resgatada"))
    c.add_item(Separator())
    c.add_item(TextDisplay(
        f"{ROLES} **Cliente:** <@{user_id}>\n"
        f"{PRESENTE} **Quantidade:** `{qtd}` salas grátis\n"
        f"{AWAITING} **Data:** `{_agora()}`"
    ))
    view.add_item(c)
    try:
        await canal.send(view=view)
    except Exception as e:
        print(f"⚠️ Erro ao postar log de sala teste: {e}")


# ═══════════════════════════════════════════════════════════════════
# LOG SALA BÔNUS (resgate)
# ═══════════════════════════════════════════════════════════════════

async def log_sala_bonus(bot, guild_id: int, user_id: int, qtd: int, total_criadas: int):
    canal = await _get_canal_log(bot, guild_id, "canal_logs_bonus_id")
    if canal is None:
        return

    view = LayoutView(timeout=None)
    c = Container(accent_colour=discord.Colour.from_str("#FAA61A"))
    c.add_item(TextDisplay(f"# {DOLLAR} Sala Bônus Resgatada"))
    c.add_item(Separator())
    c.add_item(TextDisplay(
        f"{ROLES} **Cliente:** <@{user_id}>\n"
        f"{PRESENTE} **Bônus resgatado:** `{qtd}` sala(s)\n"
        f"{CHANNEL} **Total já criadas:** `{total_criadas}`\n"
        f"{AWAITING} **Data:** `{_agora()}`"
    ))
    view.add_item(c)
    try:
        await canal.send(view=view)
    except Exception as e:
        print(f"⚠️ Erro ao postar log de bônus: {e}")


# ═══════════════════════════════════════════════════════════════════
# LOG RANKING (premiação diária)
# ═══════════════════════════════════════════════════════════════════

async def log_ranking(bot, guild_id: int, dia: str, premiados: list):
    """premiados = [(posicao, user_id, qtd_salas, premio), ...]"""
    canal = await _get_canal_log(bot, guild_id, "canal_logs_ranking_id")
    if canal is None:
        return

    if not premiados:
        return

    linhas = []
    for pos, uid, qtd, premio in premiados:
        linhas.append(
            f"{SWORD} **{pos}º** <@{uid}> — `{qtd}` salas → recebeu `{premio}` salas"
        )

    view = LayoutView(timeout=None)
    c = Container(accent_colour=discord.Colour.from_str("#FFD700"))
    c.add_item(TextDisplay(f"# {SWORD} Premiação do Ranking — `{dia}`"))
    c.add_item(Separator())
    c.add_item(TextDisplay("\n".join(linhas)))
    c.add_item(TextDisplay(f"-# {CLOUD} Premiação automática · {_agora()}"))
    view.add_item(c)
    try:
        await canal.send(view=view)
    except Exception as e:
        print(f"⚠️ Erro ao postar log de ranking: {e}")
