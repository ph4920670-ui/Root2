# utils/aposta_parser.py — Parser da embed pinada do canal de aposta
#
# Embed esperada (postada pelo sistema do mediador quando cria o canal):
#
#   Canal de aposta criado ✅
#   Partida:
#   2322541
#   🛡️ Modo:
#   1x1 Mobile Gelo Infinito
#   🎫 Valor:
#   R$ 0,90
#   👑 Jogadores:
#   @Mika❤️🔥, @Matadordecapa
#
# Formatos aceitos pra "Modo":
#   - "1x1 Normal"
#   - "1x1 Mobile Gelo Infinito"
#   - "2x2 Full Capa"
#   - "1x1 Capa"
#   - "4x4 Inf"
#
# Detecção de modo (ordem importa: full capa antes de capa, etc):
#   - "full capa" / "fullcapa"  → 3 (Full Capa)
#   - "infinito" / "gelo" / "inf" → 2 (Infinito)
#   - default                    → 1 (Normal)
#
# Detecção de jogadores: pega "1x1" → 2 jogadores, "2x2" → 4, "4x4" → 8.

import re
import logging
from typing import Optional

import discord

_log = logging.getLogger("salasff.aposta_parser")


# ══════════════════════════════════════════════════════════════
#  Regex compiladas
# ══════════════════════════════════════════════════════════════

_RE_PARTIDA = re.compile(r"partida\s*:?\s*(\d+)", re.IGNORECASE)
_RE_VALOR_RS = re.compile(r"r\$\s*([\d.,]+)", re.IGNORECASE)
_RE_NXN = re.compile(r"(\d+)\s*x\s*\1", re.IGNORECASE)
_RE_MENCAO = re.compile(r"<@!?(\d+)>")


# ══════════════════════════════════════════════════════════════
#  Parsers de campos individuais
# ══════════════════════════════════════════════════════════════

def parse_partida_id(texto: str) -> str | None:
    """Extrai número da partida. Aceita 'Partida: 2322541' ou só '2322541'."""
    m = _RE_PARTIDA.search(texto)
    if m:
        return m.group(1)
    # fallback: se o texto inteiro for só número
    s = texto.strip()
    if s.isdigit():
        return s
    return None


def parse_valor(texto: str) -> float | None:
    """Extrai valor em reais. 'R$ 0,90' → 0.9. 'R$1.234,56' → 1234.56."""
    m = _RE_VALOR_RS.search(texto)
    if not m:
        # pode vir só "0,90" sem o R$
        s = texto.strip()
        if re.match(r"^[\d.,]+$", s):
            v = s
        else:
            return None
    else:
        v = m.group(1)
    # Formato BR: vírgula decimal, ponto pra milhar
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return round(float(v), 2)
    except ValueError:
        return None


def parse_modo(texto: str) -> tuple[int, int, str]:
    """Retorna (modo_id, max_jogadores, label_limpa).

    modo_id: 1=Normal, 2=Infinito, 3=Full Capa
    max_jogadores: 1x1=2, 2x2=4, 4x4=8 (default 2)
    """
    label = texto.strip()
    s = label.lower()

    # Detecta jogadores via NxN
    m = _RE_NXN.search(s)
    if m:
        max_jog = int(m.group(1)) * 2
    else:
        max_jog = 2

    # Detecta modo (mais específico primeiro)
    if "full capa" in s or "fullcapa" in s:
        modo = 3
    elif "infinito" in s or "gelo" in s or " inf" in f" {s}":
        modo = 2
    elif " capa" in f" {s}" or s.startswith("capa"):
        # "capa" sozinho (sem "full") = Full Capa também
        modo = 3
    else:
        modo = 1

    return modo, max_jog, label


def parse_jogadores(texto: str) -> list[str]:
    """Extrai user_ids das menções @user1, @user2."""
    return _RE_MENCAO.findall(texto)


# ══════════════════════════════════════════════════════════════
#  Parser principal — recebe a embed e devolve config completa
# ══════════════════════════════════════════════════════════════

def parse_embed_aposta(embed: discord.Embed) -> dict | None:
    """Parseia uma embed de canal de aposta e retorna a config.

    Retorna None se a embed não parecer ser de aposta válida.

    Suporta dois layouts:
      A) Tudo em description (texto corrido com "Partida:", "Modo:", etc)
      B) Cada item em um field separado (name="🛡️ Modo:", value="1x1 ...")
    """
    if not embed:
        return None

    titulo = (embed.title or "")
    desc = (embed.description or "")

    # Verificação rápida: parece ser de aposta?
    txt_full = f"{titulo}\n{desc}".lower()
    if "aposta" not in txt_full and "partida" not in txt_full:
        # Tenta também nos fields
        fields_txt = " ".join(
            f"{f.name} {f.value}"
            for f in embed.fields
        ).lower()
        if "aposta" not in fields_txt and "partida" not in fields_txt:
            return None

    # ── Estratégia: extrai cada campo procurando primeiro em fields, depois em description ──

    partida_id = None
    modo_label = None
    valor_str = None
    jogadores_str = None

    # Layout B: fields
    for f in embed.fields:
        nome_lower = (f.name or "").lower()
        valor = (f.value or "").strip()
        if "partida" in nome_lower and not partida_id:
            partida_id = parse_partida_id(valor)
        elif "modo" in nome_lower and not modo_label:
            modo_label = valor
        elif "valor" in nome_lower and not valor_str:
            valor_str = valor
        elif ("jogador" in nome_lower or "👑" in nome_lower) and not jogadores_str:
            jogadores_str = valor

    # Layout A: description com labels — pega trecho após cada label
    if desc:
        if not partida_id:
            partida_id = parse_partida_id(desc)
        if not modo_label:
            m = re.search(r"modo\s*:?\s*\n?\s*([^\n]+)", desc, re.IGNORECASE)
            if m:
                modo_label = m.group(1).strip()
        if not valor_str:
            m = re.search(r"valor\s*:?\s*\n?\s*([^\n]+)", desc, re.IGNORECASE)
            if m:
                valor_str = m.group(1).strip()
        if not jogadores_str:
            m = re.search(r"jogadores?\s*:?\s*\n?\s*([^\n]+)", desc, re.IGNORECASE)
            if m:
                jogadores_str = m.group(1).strip()

    # Validação mínima
    if not modo_label or not valor_str:
        _log.debug(f"[parse_embed] faltam campos: modo={modo_label!r} valor={valor_str!r}")
        return None

    valor = parse_valor(valor_str)
    if valor is None or valor <= 0:
        _log.debug(f"[parse_embed] valor inválido: {valor_str!r}")
        return None

    modo, max_jog, modo_clean = parse_modo(modo_label)
    jogadores = parse_jogadores(jogadores_str or "")

    return {
        "partida_id": partida_id or "0",
        "modo": modo,
        "modo_label": modo_clean,
        "max_jogadores": max_jog,
        "valor_esperado": valor,
        "jogadores_autorizados": jogadores,
    }


async def buscar_embed_canal(channel: discord.TextChannel) -> Optional[discord.Embed]:
    """Procura a embed de aposta do canal. Estratégia:
    1) Mensagens fixadas (pinned) — primeiro candidato
    2) Últimas 20 mensagens — fallback
    Retorna a embed que parser conseguiu decodificar.
    """
    # Tenta pinned primeiro
    try:
        pins = await channel.pins()
        for msg in pins:
            for em in msg.embeds:
                cfg = parse_embed_aposta(em)
                if cfg:
                    return em
    except discord.Forbidden:
        _log.warning(f"[buscar_embed] sem permissão pra ver pins do canal {channel.id}")
    except Exception as e:
        _log.warning(f"[buscar_embed] erro pins canal {channel.id}: {e}")

    # Fallback: histórico
    try:
        async for msg in channel.history(limit=20, oldest_first=True):
            for em in msg.embeds:
                cfg = parse_embed_aposta(em)
                if cfg:
                    return em
    except discord.Forbidden:
        _log.warning(f"[buscar_embed] sem permissão de histórico canal {channel.id}")
    except Exception as e:
        _log.warning(f"[buscar_embed] erro history canal {channel.id}: {e}")

    return None


async def detectar_e_cachear(channel: discord.TextChannel, salvar_se_achar: bool = True) -> dict | None:
    """Procura embed no canal, parseia, e (se salvar=True) salva no Mongo.
    Retorna a config parseada ou None.
    """
    em = await buscar_embed_canal(channel)
    if not em:
        return None
    cfg = parse_embed_aposta(em)
    if not cfg:
        return None
    if salvar_se_achar:
        from utils.aposta_storage import canal_criar
        canal_criar(
            channel_id=channel.id,
            guild_id=channel.guild.id,
            partida_id=cfg["partida_id"],
            modo=cfg["modo"],
            modo_label=cfg["modo_label"],
            max_jogadores=cfg["max_jogadores"],
            valor_esperado=cfg["valor_esperado"],
            jogadores_autorizados=cfg["jogadores_autorizados"],
        )
        _log.info(
            f"[aposta_parser] canal {channel.id} cacheado: "
            f"partida={cfg['partida_id']} modo={cfg['modo']} "
            f"max_jog={cfg['max_jogadores']} valor=R${cfg['valor_esperado']:.2f} "
            f"jogadores={len(cfg['jogadores_autorizados'])}"
        )
    return cfg
