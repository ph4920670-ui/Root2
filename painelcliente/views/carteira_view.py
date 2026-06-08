"""
Painel de carteira (saldo de salas) — Components V2.
"""

import discord
from discord.ui import LayoutView, Container, TextDisplay, Separator, ActionRow
from utils.emojis import CHANNEL, CLOUD, DOLLAR, PRESENTE, ROLES, SWORD


def montar_carteira(user: discord.User, saldo: int, saldo_global: int = None) -> "CarteiraView":
    view = CarteiraView()
    container = Container(accent_colour=discord.Colour.from_str("#43B581"))

    container.add_item(TextDisplay(
        f"# {DOLLAR} Carteira"
    ))

    container.add_item(TextDisplay(
        f"{ROLES} **Usuário:** {user.mention}\n"
        f"{CHANNEL} **Saldo:** `{saldo}` sala(s)"
    ))

    container.add_item(Separator())

    container.add_item(TextDisplay(
        f"### {PRESENTE} Como usar\n"
        f"{CLOUD} `/cs` — escolher modo e criar sala\n"
        f"{CLOUD} `/c1` — criar `4X4 SEM CARREGAMENTO` direto\n"
        f"{CLOUD} `/c2` — criar `X1 GELO INF` em modo infinito\n"
        f"{DOLLAR} `/painelcompras` (admin) — gerenciar painel de vendas"
    ))

    if saldo_global is not None:
        container.add_item(TextDisplay(
            f"-# {SWORD} Saldo global da loja: `{saldo_global}` salas"
        ))

    view.add_item(container)
    return view


class CarteiraView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
