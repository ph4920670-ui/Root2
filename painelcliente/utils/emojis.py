"""
emojis.py — Símbolos Unicode minimalistas (fonte única).

Tudo que tem cara de emoji no bot passa por aqui. Pra trocar a estética
do bot inteiro mude UMA flag: `MODO_SIMBOLOS_PUROS`.

Modos:
- True  → bot "limpo": botões SEM emoji (Discord renderiza só o label) e
          textos usam símbolos Unicode finos (◆ ▸ ❖ ✦ etc.).
- False → modo clássico com emojis coloridos nos botões (🏆 💰 🎁 ...).

Como é consumido:
- Texto/embed:  `from utils.emojis import VISION, CHANNEL, ...`
- Botões:       `emoji=EMOJI_IDS["vision"]["unicode"]`  (None quando puro)
"""

# ─── Flag global ───────────────────────────────────────────────────
MODO_SIMBOLOS_PUROS: bool = True


# ─── Símbolos pra USAR EM TEXTO (mensagens, embeds) ────────────────
# Sempre símbolos Unicode finos. Seguros em qualquer lugar.
VISION   = "❖"    # losango decorativo — ver/destaque
SWORD    = "✦"    # estrela 4 pontas — destaque/ranking/conquista
AWAITING = "✧"    # estrela vazada — atenção/aguardando
ROLES    = "▸"    # seta — item de lista/cargo
CHANNEL  = "◆"    # losango cheio — tópico/item
DOLLAR   = "❥"    # coração ornamental — saldo/recompensa
CLOUD    = "✺"    # estrela floral — destaque/info
PRESENTE = "✿"    # flor — brinde/presente

# Aliases (retro-compatibilidade)
PIX      = VISION
GIFT     = PRESENTE
TROPHY   = SWORD
PERSON   = ROLES
WALLET   = DOLLAR
DATABASE = CHANNEL
BOX      = "◇"


# ─── Mapa para BOTÕES (Button.emoji) ───────────────────────────────
# Em modo "símbolos puros" devolvemos None → botão sem emoji.
# Em modo clássico devolvemos o emoji colorido.

_EMOJI_CLASSICO = {
    "vision":   "🔍",
    "trophy":   "🏆",
    "sword":    "🏆",
    "awaiting": "⏳",
    "person":   "👤",
    "roles":    "👥",
    "channel":  "🔹",
    "database": "🔹",
    "wallet":   "💰",
    "dollar":   "💰",
    "cloud":    "🔸",
    "box":      "🎁",
    "presente": "🎁",
    "pix":      "💠",
    "gift":     "🎁",
}


class _EmojiIdsProxy:
    """`EMOJI_IDS[nome]` → dict com 'unicode' (str ou None).

    - Modo símbolos puros: 'unicode' = None → botão fica sem emoji.
    - Modo clássico:       'unicode' = emoji colorido (🏆 💰 ...).
    """

    def __getitem__(self, name: str) -> dict:
        if name not in _EMOJI_CLASSICO:
            raise KeyError(name)
        char = None if MODO_SIMBOLOS_PUROS else _EMOJI_CLASSICO[name]
        return {"unicode": char, "id": None, "animated": False}

    def __contains__(self, name: str) -> bool:
        return name in _EMOJI_CLASSICO


EMOJI_IDS = _EmojiIdsProxy()


def button_emoji(name: str):
    """Atalho: `emoji=button_emoji("trophy")` em qualquer Button.

    Retorna None em modo símbolos puros ou o emoji clássico.
    """
    return EMOJI_IDS[name]["unicode"] if name in EMOJI_IDS else None
