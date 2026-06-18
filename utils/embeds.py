# utils/embeds.py  –  Funções auxiliares para criar Discord Embeds

import discord
from datetime import datetime
from zoneinfo import ZoneInfo
_BR = ZoneInfo("America/Sao_Paulo")
import config


def _base(cor: int) -> discord.Embed:
    e = discord.Embed(color=cor, timestamp=datetime.now(_BR))
    return e


# ── Modos ─────────────────────────────────────────────────────
def embed_modos(data: dict) -> discord.Embed:
    e = _base(config.COR_INFO)
    e.title = f"{config.EMOJI_SALA}  Modos Disponíveis"
    e.description = f"Total de salas criadas na plataforma: **{data.get('salas', '?')}**"

    # Modos próprios
    modos = data.get("modos", [])
    if modos:
        linhas = "\n".join(f"`{m['salaid']}` — **{m['nome']}** (senha: {m['senha']})" for m in modos)
        e.add_field(name="🔒 Seus Modos", value=linhas, inline=False)

    # Modos públicos
    pub = data.get("modos_Publico", [])
    if pub:
        linhas = "\n".join(f"`{m['salaid']}` — **{m['nome']}**" for m in pub[:10])
        if len(pub) > 10:
            linhas += f"\n…e mais {len(pub)-10} modos"
        e.add_field(name="🌐 Modos Públicos", value=linhas, inline=False)

    return e


# ── Criação de sala ───────────────────────────────────────────
def embed_sala_criando(data: dict) -> discord.Embed:
    e = _base(config.COR_AGUARDO)
    e.title = f"{config.EMOJI_TIMER}  Criando Sala…"
    e.description = "A sala está sendo configurada no Free Fire. Aguarde alguns segundos."
    e.add_field(name="Pedido ID", value=f"`{data.get('pedidoid','?')}`", inline=False)
    return e


def embed_sala_pronta(data: dict) -> discord.Embed:
    sala = data.get("sala", {})
    e = _base(config.COR_SUCESSO)
    e.title = f"{config.EMOJI_SUCESSO}  Sala Criada!"
    e.add_field(name=f"{config.EMOJI_SALA} Nome",    value=sala.get("nome", "?"),   inline=True)
    e.add_field(name=f"{config.EMOJI_SENHA} Senha",  value=f"**{sala.get('senha','?')}**", inline=True)
    e.add_field(name="🆔 ID da Sala",               value=str(sala.get("id","?")), inline=True)
    e.add_field(name="📦 Pedido ID",                value=f"`{data.get('pedidoid','?')}`", inline=False)
    inicio = data.get("inicio_automatico", "")
    if inicio:
        e.add_field(name=f"{config.EMOJI_TIMER} Início Automático", value=inicio, inline=True)
    return e


def embed_sala_iniciada(data: dict) -> discord.Embed:
    sala = data.get("sala", {})
    e = _base(config.COR_INFO)
    e.title = f"{config.EMOJI_INICIAR}  Partida Iniciada!"
    e.description = "A sala passou para **status 4** — partida em andamento."
    e.add_field(name=f"{config.EMOJI_SALA} Nome",   value=sala.get("nome","?"),   inline=True)
    e.add_field(name=f"{config.EMOJI_SENHA} Senha", value=f"**{sala.get('senha','?')}**", inline=True)
    e.add_field(name="🆔 ID",                       value=str(sala.get("id","?")), inline=True)
    e.add_field(name="🕐 Iniciada em",              value=data.get("inicio","?"), inline=False)
    return e


# ── Info / Status ─────────────────────────────────────────────
STATUS_LABELS = {
    1: "⏳ Pedido recebido",
    2: "🔄 Criando sala",
    3: "✅ Sala pronta",
    4: "▶️ Partida iniciada",
}

def embed_info_sala(data: dict) -> discord.Embed:
    status = data.get("status", 0)
    sala   = data.get("sala") or {}
    cor    = config.COR_SUCESSO if status == 3 else (config.COR_INFO if status == 4 else config.COR_AGUARDO)
    e      = _base(cor)
    label  = STATUS_LABELS.get(status, f"Status {status}")
    e.title = f"{config.EMOJI_INFO}  Status da Sala — {label}"

    if sala:
        e.add_field(name=f"{config.EMOJI_SALA} Nome",   value=sala.get("nome","?"),  inline=True)
        e.add_field(name=f"{config.EMOJI_SENHA} Senha", value=f"**{sala.get('senha','?')}**", inline=True)
        e.add_field(name="🆔 ID",                       value=str(sala.get("id","?")), inline=True)

        equipes = sala.get("equipes", [])
        for eq in equipes:
            jogadores = eq.get("jogadores", [])
            if jogadores:
                nomes = "\n".join(f"`{j['id']}` {j['nickname']}" for j in jogadores)
            else:
                nomes = "_Nenhum jogador_"
            e.add_field(
                name=f"{config.EMOJI_EQUIPE} Equipe {eq.get('numero','?')}",
                value=nomes,
                inline=True,
            )

    e.add_field(name="📦 Pedido ID",   value=f"`{data.get('pedidoid','?')}`", inline=False)
    e.add_field(name="🕐 Atualizado", value=data.get("atualizado","?"),       inline=True)
    inicio = data.get("inicio_automatico","")
    if inicio:
        e.add_field(name=f"{config.EMOJI_TIMER} Início Auto", value=inicio, inline=True)
    return e


# ── Listar salas ──────────────────────────────────────────────
def embed_listar_salas(data: dict) -> list[discord.Embed]:
    salas = data.get("salas", [])
    if not salas:
        e = _base(config.COR_AVISO)
        e.title = f"{config.EMOJI_LISTAR}  Nenhuma Sala Ativa"
        e.description = "Não há salas em status 2 ou 3 no momento."
        return [e]

    embeds = []
    for idx, s in enumerate(salas, 1):
        sala  = s.get("sala") or {}
        status = s.get("status", 0)
        cor   = config.COR_SUCESSO if status == 3 else config.COR_AGUARDO
        e     = _base(cor)
        e.title = f"{config.EMOJI_LISTAR}  Sala {idx}/{len(salas)} — {STATUS_LABELS.get(status, f'Status {status}')}"
        e.add_field(name=f"{config.EMOJI_SALA} Nome",   value=sala.get("nome","?"),  inline=True)
        e.add_field(name=f"{config.EMOJI_SENHA} Senha", value=f"**{sala.get('senha','?')}**", inline=True)
        e.add_field(name="🆔 ID",                       value=str(sala.get("id","?")), inline=True)
        e.add_field(name="📦 Pedido ID",               value=f"`{s.get('pedidoid','?')}`", inline=False)
        e.add_field(name="🕐 Atualizado",              value=s.get("atualizado","?"), inline=True)
        inicio_auto = s.get("inicio_automatico","")
        if inicio_auto:
            e.add_field(name=f"{config.EMOJI_TIMER} Início Auto", value=inicio_auto, inline=True)

        equipes = sala.get("equipes", [])
        for eq in equipes:
            jogadores = eq.get("jogadores", [])
            nomes = ", ".join(j["nickname"] for j in jogadores) if jogadores else "Vazio"
            e.add_field(name=f"{config.EMOJI_EQUIPE} Equipe {eq.get('numero','?')}", value=nomes, inline=False)

        embeds.append(e)
    return embeds


# ── Expulsar ──────────────────────────────────────────────────
def embed_expulsar(data: dict, jogadorid: str) -> discord.Embed:
    e = _base(config.COR_SUCESSO)
    e.title = f"{config.EMOJI_EXPULSAR}  Jogador Expulso"
    e.add_field(name="Jogador ID", value=f"`{jogadorid}`", inline=True)
    e.add_field(name="Pedido ID",  value=f"`{data.get('pedidoid','?')}`", inline=True)
    e.add_field(name="Mensagem",   value=data.get("msg","?"), inline=False)
    return e


# ── Erro genérico ─────────────────────────────────────────────
def embed_erro(msg: str, codigo: int = None) -> discord.Embed:
    e = _base(config.COR_ERRO)
    e.title = f"{config.EMOJI_ERRO}  Erro"
    e.description = msg
    if codigo:
        e.add_field(name="Código", value=str(codigo), inline=True)
    return e


# ── Sucesso genérico ──────────────────────────────────────────
def embed_ok(titulo: str, descricao: str) -> discord.Embed:
    e = _base(config.COR_SUCESSO)
    e.title = f"{config.EMOJI_SUCESSO}  {titulo}"
    e.description = descricao
    return e
