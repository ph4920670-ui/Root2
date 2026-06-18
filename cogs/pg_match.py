# cogs/pg_match.py — Listener "pg Nome" pra fila de apostas
#
# Fluxo:
#   1. Jogador digita "pg Gabriel" em qualquer canal/servidor
#   2. Bot pega guild_id da mensagem
#   3. Carrega creds da guild (do banco ativo configurado em /configurar_pix)
#   4. Busca PIX recebidos nos últimos 10 min
#   5. Match por nome (normalizado, similaridade):
#        0 matches  → "❌ Nenhum PIX em seu nome"
#        1 match    → "✅ Confirmado: <nome> R$ <valor>"
#        2+ matches → "⚠️ Tem 2+ PIX com 'Gabriel'. Manda sobrenome"
#   6. Anti-replay: PIX já confirmado fica marcado (não casa de novo)
#   7. Rate-limit: 1 'pg' a cada 5s por usuário

import asyncio
import logging
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from utils import pix_credentials as pc
from utils.pix_consulta import listar_pix_recebidos

_log = logging.getLogger("salasff.pg_match")


# ═══════════════════════════════════════════
#  Constantes
# ═══════════════════════════════════════════

JANELA_MINUTOS = 10           # quanto tempo pra trás procura PIX
RATE_LIMIT_SEC = 2            # tempo mínimo entre 'pg' do mesmo usuário
COOLDOWN_ERRO_SEC = 60        # se API falhar, espera antes de tentar de novo
PG_PREFIX_RE = re.compile(r"^\s*pg\s+(.+)$", re.IGNORECASE)
MAX_TAMANHO_NOME = 80         # nome > 80 chars provavelmente não é nome

# IDs já confirmados (anti-replay): {guild_id: {pix_id, ...}}
# Em produção real, persistir em disco. Por enquanto memória.
_confirmados: dict[int, set[str]] = {}

# Rate limit: {user_id: timestamp_ultima_msg}
_rate: dict[int, float] = {}


# ═══════════════════════════════════════════
#  Normalização e match de nomes
# ═══════════════════════════════════════════

def _normalizar(s: str) -> str:
    """lowercase, sem acento, sem espaço duplicado, sem pontuação extra."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)  # remove pontuação
    s = re.sub(r"\s+", " ", s)       # espaços únicos
    return s.strip()


def _tokens(s: str) -> set[str]:
    """Split em tokens >= 2 chars."""
    return {t for t in _normalizar(s).split() if len(t) >= 2}


def _match_score(busca: str, nome_pix: str) -> float:
    """Score de similaridade entre nome buscado e nome do PIX.
    
    Estratégia:
    - Se busca tem só 1 token (1 nome): match se aquele token existe em nome_pix
    - Se busca tem 2+ tokens: match se TODOS os tokens da busca existem em nome_pix
    
    Retorna score 0..1. >= 0.5 é considerado match.
    """
    tb = _tokens(busca)
    tp = _tokens(nome_pix)
    if not tb or not tp:
        return 0.0
    intersect = tb & tp
    if len(intersect) < len(tb):
        # falta algum token → não casa
        return 0.0
    # todos os tokens da busca estão no nome do PIX
    # quanto mais específica a busca (mais tokens), maior o score
    return min(1.0, 0.5 + 0.25 * len(tb))


def _filtrar_recentes(pix_list: list[dict], janela_min: int = JANELA_MINUTOS) -> list[dict]:
    """Mantém só PIX dentro da janela de tempo."""
    if not pix_list:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=janela_min)
    out = []
    for p in pix_list:
        h = p.get("horario", "")
        if not h:
            # sem timestamp, considera recente (melhor falso positivo que falso negativo)
            out.append(p)
            continue
        try:
            # aceita "2026-04-28T...Z" ou com offset
            t = datetime.fromisoformat(h.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if t >= cutoff:
                out.append(p)
        except Exception:
            out.append(p)  # sem parse, mantém
    return out


# ═══════════════════════════════════════════
#  Helpers de embed
# ═══════════════════════════════════════════

def _emb_ok(nome: str, valor: float, banco: str) -> discord.Embed:
    em = discord.Embed(
        title="✅  Pagamento confirmado",
        color=0x57F287,
        description=f"**{nome}** — R$ {valor:.2f}".replace(".", ","),
    )
    em.set_footer(text=f"via {pc.BANCOS_LABEL.get(banco, banco)}")
    return em


def _emb_nada(nome: str, janela: int) -> discord.Embed:
    return discord.Embed(
        title="❌  Nenhum PIX encontrado",
        color=0xED4245,
        description=(
            f"Não achei nenhum PIX recebido em nome de **{nome}** "
            f"nos últimos {janela} minutos.\n\n"
            "Verifica se já enviou o PIX e tenta de novo em alguns segundos."
        ),
    )


def _emb_ambiguo(nome_busca: str, candidatos: list[dict]) -> discord.Embed:
    nomes = ", ".join(f"**{c['nome']}**" for c in candidatos[:5])
    return discord.Embed(
        title="⚠️  Vários PIX encontrados",
        color=0xFAA61A,
        description=(
            f"Achei {len(candidatos)} PIX com '**{nome_busca}**':\n{nomes}\n\n"
            f"Manda o nome completo, tipo: `pg {candidatos[0]['nome']}`"
        ),
    )


def _emb_sem_config() -> discord.Embed:
    return discord.Embed(
        title="⚙️  Banco não configurado",
        color=0x5865F2,
        description=(
            "O admin do servidor ainda não configurou o banco PIX.\n"
            "Use `/configurar_pix` (admin only)."
        ),
    )


def _emb_api_off() -> discord.Embed:
    return discord.Embed(
        title="🔌  Banco indisponível",
        color=0xED4245,
        description="Não consegui conectar no banco agora. Tenta de novo em ~1 min.",
    )


# ═══════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════

class PgMatchCog(commands.Cog):
    """Listener de 'pg Nome' em todos os canais."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        # Filtros básicos
        if msg.author.bot:
            return
        if not msg.guild:
            return  # ignora DM
        if not msg.content:
            return

        m = PG_PREFIX_RE.match(msg.content)
        if not m:
            return

        _log.info(f"[pg_match] DETECTOU pg em guild={msg.guild.id} canal={msg.channel.id} nome_canal={getattr(msg.channel, 'name', '?')}")

        # Em THREAD (privada ou pública): processa qualquer nome — o nome muda
        # ao longo do fluxo (aguardando-X → fila-X → confirmada-X). Se a thread
        # existe e o bot foi adicionado, é contexto válido.
        # Em CANAL top-level: mantém filtro startswith("fila") pra não confundir
        # com chat geral.
        if not isinstance(msg.channel, (discord.TextChannel, discord.Thread)):
            _log.info(f"[pg_match] IGNORADO: canal não é text/thread (tipo={type(msg.channel).__name__})")
            return
        canal_nome = msg.channel.name or ""
        if isinstance(msg.channel, discord.TextChannel) and not canal_nome.lower().startswith("fila"):
            _log.info(f"[pg_match] IGNORADO: canal top-level '{canal_nome}' não começa com 'fila'")
            return

        nome_busca = m.group(1).strip()
        if len(nome_busca) < 2 or len(nome_busca) > MAX_TAMANHO_NOME:
            _log.info(f"[pg_match] IGNORADO: nome '{nome_busca}' tamanho inválido")
            return

        # Rate-limit por usuário
        agora = time.time()
        ult = _rate.get(msg.author.id, 0)
        if agora - ult < RATE_LIMIT_SEC:
            return  # silencia spam
        _rate[msg.author.id] = agora

        # Banco configurado?
        banco = pc.get_banco_ativo(msg.guild.id)
        creds = pc.get_creds_guild(msg.guild.id, banco) if banco else {}
        _log.info(f"[pg_match] guild banco={banco} tem_creds={bool(creds)}")

        # Sem banco nesta guild → tenta fallback de outra guild
        if not banco or not creds:
            banco, creds = await asyncio.to_thread(pc.get_creds_fallback)
            _log.info(f"[pg_match] FALLBACK banco={banco} tem_creds={bool(creds)}")

        if not banco or not creds:
            _log.warning(f"[pg_match] SEM BANCO em guild={msg.guild.id}")
            try:
                await msg.reply(embed=_emb_sem_config(), mention_author=False)
            except Exception as e:
                _log.warning(f"[pg_match] erro reply sem_config: {e}")
            return

        # EFI: precisa cert.pem
        if banco == "efi":
            if not pc.has_cert_efi(msg.guild.id):
                try:
                    em = discord.Embed(
                        title="⚙️  Falta certificado EFI",
                        color=0xED4245,
                        description="Use `/configurar_pix_efi_cert` pra fazer upload do `cert.pem`.",
                    )
                    await msg.reply(embed=em, mention_author=False)
                except Exception:
                    pass
                return
            creds = {**creds, "cert_path": pc.cert_path_efi(msg.guild.id)}

        # Busca PIX
        try:
            pix_list = await self._buscar_pix(banco, creds, msg.guild.id)
        except Exception as e:
            _log.error(f"[pg_match] guild={msg.guild.id} banco={banco} erro busca: {e}")
            try:
                await msg.channel.send(embed=_emb_api_off())
            except Exception as e2:
                _log.error(f"[pg_match] falha ao responder erro: {e2}")
            return

        # Filtra por janela de tempo (Gmail usa janela maior pois email pode atrasar)
        janela = 30 if banco == "gmail" else JANELA_MINUTOS
        recentes = _filtrar_recentes(pix_list, janela)

        # Filtra anti-replay (já confirmados nesta guild)
        confs = _confirmados.setdefault(msg.guild.id, set())
        disponiveis = [p for p in recentes if p.get("id") not in confs]

        # Match por nome
        candidatos = []
        for p in disponiveis:
            score = _match_score(nome_busca, p.get("nome", ""))
            if score >= 0.5:
                candidatos.append({**p, "_score": score})

        # Resultado
        if not candidatos:
            try:
                await msg.channel.send(embed=_emb_nada(nome_busca, janela))
            except Exception as e:
                _log.warning(f"[pg_match] falha ao responder nada: {e}")
            return

        if len(candidatos) > 1:
            # Ordena por score desc → ambiguidade real
            candidatos.sort(key=lambda c: c["_score"], reverse=True)
            try:
                await msg.channel.send(embed=_emb_ambiguo(nome_busca, candidatos))
            except Exception as e:
                _log.warning(f"[pg_match] falha ao responder ambiguo: {e}")
            return

        # 1 match exato → confirma
        c = candidatos[0]
        confs.add(c.get("id", ""))

        try:
            valor = float(c.get("valor", 0))
        except Exception:
            valor = 0.0

        # Verifica se há algum usuário com token mode ativo no canal
        _tok_enviou = False
        try:
            from utils.database import token_mode_listar_todos, token_mode_get
            from cogs.token_mode import enviar_como_usuario
            valor_fmt = f"{valor:.2f}".replace(".", ",")
            txt_pg = (
                f"## ✅ ⠀**PAGAMENTO CONFIRMADO**\n"
                f"> 🔹 ⠀**Nome** ⠀⠀`{c.get('nome', '?')}`\n"
                f"> 🔹 ⠀**Valor** ⠀⠀`R$ {valor_fmt}`"
            )
            # Pega todos com token ativo e tenta um que esteja no canal
            ativos = await asyncio.to_thread(token_mode_listar_todos)
            canal = msg.channel
            for u in ativos:
                if not u.get("ativo"):
                    continue
                cfg_tok = await asyncio.to_thread(token_mode_get, u["user_id"])
                if not cfg_tok.get("token"):
                    continue
                # Verifica se o membro está no canal
                membro = canal.guild.get_member(int(u["user_id"]))
                if not membro:
                    continue
                enviado = await enviar_como_usuario(cfg_tok["token"], canal.id, txt_pg)
                if enviado:
                    _tok_enviou = True
                    break
        except Exception as _tex:
            _log.warning(f"[pg_match token_mode] {_tex}")

        # Fallback: bot responde normalmente se token mode não enviou
        if not _tok_enviou:
            try:
                await msg.reply(
                    embed=_emb_ok(c.get("nome", "?"), valor, banco),
                    mention_author=True,
                )
            except Exception as e:
                _log.warning(f"[pg_match] falha ao responder: {e}")

        # Hook pra outros cogs reagirem (criar sala, contabilizar, etc)
        self.bot.dispatch(
            "pg_confirmado",
            {
                "guild_id":  msg.guild.id,
                "channel_id": msg.channel.id,
                "user_id":   msg.author.id,
                "user_name": str(msg.author),
                "nome_pagador": c.get("nome", ""),
                "valor":     valor,
                "pix_id":    c.get("id", ""),
                "banco":     banco,
            },
        )

    async def _buscar_pix(self, banco: str, creds: dict, guild_id: int) -> list[dict]:
        """Busca PIX recebidos via adapter do banco."""
        return await listar_pix_recebidos(banco, creds, guild_id=guild_id)


async def setup(bot):
    await bot.add_cog(PgMatchCog(bot))
