# cogs/aposta_auto.py — Sistema automático de fila de aposta
#
# Fluxo:
#   1. Sistema do mediador cria canal + posta embed pinada
#      (Partida, Modo, Valor, Jogadores)
#
#   2. Bot detecta canal de aposta (lazy: na 1ª vez que recebe pg_confirmado
#      naquele canal, parseia a embed)
#
#   3. Jogador digita "pg Nome" → cogs/pg_match.py confirma o PIX e
#      dispatcha "pg_confirmado"
#
#   4. ESTE cog escuta pg_confirmado:
#      - Adiciona jogador na fila (com whitelist + valor + anti-duplicata)
#      - Se fila bate max_jogadores → cria sala automaticamente
#      - Posta embed dourado de "Sala criada"
#
#   5. Tasks paralelas:
#      - PIX órfãos: PIX que cai mas ninguém digita pg em 10min
#        → DM pros admins do bot
#      - Limpeza: canais com sala criada há > 24h são removidos do cache

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils import aposta_storage as ast
from utils import aposta_parser as ap
from utils.api import api as _api_salas
from utils.database import registrar_sala, atualizar_sala

_log = logging.getLogger("salasff.aposta_auto")


# ══════════════════════════════════════════════════════════════
#  Constantes
# ══════════════════════════════════════════════════════════════

ORFAO_AVISO_MIN = 10              # avisa admin se PIX órfão > 10min
RETRY_CRIAR_SALA = 3              # tentativas automáticas
RETRY_BACKOFF = [2, 5, 10]        # segundos entre tentativas
LOCK_POS_CRIAR = 10               # cooldown anti-race após criar sala (s)

# Mapa modo → (nome, emoji, salaid da config.py)
_MODO_INFO = {
    1: {"nome": "Normal",    "emoji": "⚔️"},
    2: {"nome": "Infinito",  "emoji": "♾️"},
    3: {"nome": "Full Capa", "emoji": "👑"},
}


def _modo_dados(modo: int) -> dict:
    base = config.MODOS.get(modo) or {}
    return {
        "nome":   base.get("nome",  _MODO_INFO.get(modo, {}).get("nome",  "Sala")),
        "emoji":  base.get("emoji", _MODO_INFO.get(modo, {}).get("emoji", "🎮")),
        "salaid": base.get("salaid", ""),
    }


# ══════════════════════════════════════════════════════════════
#  Embeds
# ══════════════════════════════════════════════════════════════

def _emb_pg_aceito(canal_doc: dict, jogador: dict) -> discord.Embed:
    fila = canal_doc.get("fila_atual") or []
    max_jog = int(canal_doc.get("max_jogadores", 2))
    em = discord.Embed(
        title="✅ Pagamento confirmado",
        color=0x57F287,
        description=(
            f"**{jogador['nome_pagador']}** — R$ {jogador['valor']:.2f}".replace(".", ",") + "\n"
            f"`{len(fila)}/{max_jog}` vagas preenchidas"
            + (f" · faltando **{max_jog - len(fila)}** jogador(es)" if len(fila) < max_jog else " · **fila completa, criando sala...**")
        ),
    )
    return em


def _emb_nao_autorizado(canal_doc: dict) -> discord.Embed:
    autorizados = canal_doc.get("jogadores_autorizados") or []
    mencoes = ", ".join(f"<@{u}>" for u in autorizados) or "*ninguém*"
    return discord.Embed(
        title="🚫 Não autorizado",
        color=0xED4245,
        description=(
            "Este canal de aposta é restrito aos jogadores:\n"
            f"{mencoes}\n\n"
            "Se você não está nessa lista, fala com o admin."
        ),
    )


def _emb_valor_errado(esperado: float, pago: float) -> discord.Embed:
    return discord.Embed(
        title="⚠️ Valor incorreto",
        color=0xFAA61A,
        description=(
            f"Esta partida custa **R$ {esperado:.2f}**".replace(".", ",") +
            f" mas você pagou **R$ {pago:.2f}**.".replace(".", ",") +
            "\n\nPara o pagamento ser aceito, envie o valor exato.\n"
            "Procure o admin pra resolver o estorno."
        ),
    )


def _emb_ja_na_fila() -> discord.Embed:
    return discord.Embed(
        title="ℹ️ Você já está na fila",
        color=0x5865F2,
        description="Seu pagamento já foi registrado pra esta partida.\nAguarde o(s) outro(s) jogador(es).",
    )


def _emb_sala_ja_criada() -> discord.Embed:
    return discord.Embed(
        title="ℹ️ Sala já foi criada",
        color=0x5865F2,
        description="Esta partida já está em andamento. Veja os dados da sala acima ☝️",
    )


def _emb_sala_criada(canal_doc: dict, sala: dict, modo: int) -> discord.Embed:
    md = _modo_dados(modo)
    fila = canal_doc.get("fila_atual") or []
    valor_total = sum(j.get("valor", 0) for j in fila)
    jogs_txt = "\n".join(
        f"> 🔹 <@{j['user_id']}> — **{j['nome_pagador']}** (R$ {j['valor']:.2f}".replace(".", ",") + ")"
        for j in fila
    )
    link = sala.get("link") or ""
    link_line = f"**🔗 Link:** [Entrar na sala]({link})\n" if link else ""
    em = discord.Embed(
        title="🏆 SALA CRIADA AUTOMATICAMENTE",
        color=0xFEE75C,
        description=(
            f"### {md['emoji']} {md['nome']}\n"
            f"**Partida:** `{canal_doc.get('partida_id', '—')}`\n\n"
            f"**🆔 ID:** `{sala.get('id', '—')}`\n"
            f"**🔐 Senha:** `{sala.get('senha', '—')}`\n"
            f"**🗺️ Mapa:** {sala.get('nome', 'BERMUDA')}\n"
            f"{link_line}\n"
            f"**👥 Jogadores:**\n{jogs_txt}\n\n"
            f"**💰 Pote:** R$ {valor_total:.2f}".replace(".", ",")
        ),
    )
    em.set_footer(text=f"FMediador · automático · {len(fila)} jogador(es)")
    return em


def _txt_pg_aceito(canal_doc: dict, jogador: dict) -> str:
    fila = canal_doc.get("fila_atual") or []
    max_jog = int(canal_doc.get("max_jogadores", 2))
    valor_fmt = f"{jogador['valor']:.2f}".replace(".", ",")
    faltam = max_jog - len(fila)
    status = f"faltando **{faltam}** jogador(es)" if faltam > 0 else "**fila completa, criando sala...**"
    return (
        f"## ✅ ⠀**PAGAMENTO CONFIRMADO**\n"
        f"> 🔹 ⠀**Nome** ⠀⠀`{jogador['nome_pagador']}`\n"
        f"> 🔹 ⠀**Valor** ⠀⠀`R$ {valor_fmt}`\n"
        f"-# `{len(fila)}/{max_jog}` vagas · {status}"
    )


def _txt_sala_criada(canal_doc: dict, sala: dict, modo: int) -> str:
    md = _modo_dados(modo)
    fila = canal_doc.get("fila_atual") or []
    sid = sala.get("id", "—")
    sen = sala.get("senha", "—")
    link = sala.get("link") or ""
    mencoes = " ".join(f"<@{j['user_id']}>" for j in fila)
    txt = (
        f"## 🏆 ⠀**SALA CRIADA!** {mencoes}\n"
        f"-# {md['emoji']} ⠀Modo **{md['nome']}**\n"
        f"\n"
        f"> 🔹 ⠀**ID** ⠀⠀⠀⠀⠀⠀`{sid}`\n"
        f"> 🔹 ⠀**Senha** ⠀⠀`{sen}`"
    )
    if link:
        txt += f"\n> 🔹 ⠀**Link** ⠀⠀⠀⠀[Entrar na sala]({link})"
    return txt


async def _enviar_como_user_fila(bot, canal_doc: dict, channel_id: int, texto: str) -> bool:
    """Tenta enviar texto como algum usuário com token mode ativo no servidor.
    Prioriza usuários da fila, depois qualquer ativo.
    Retorna True se conseguiu enviar, False caso contrário."""
    try:
        from utils.database import token_mode_listar_todos, token_mode_get
        from cogs.token_mode import enviar_como_usuario

        # Monta lista: fila primeiro, depois todos os ativos
        fila_ids = [str(j["user_id"]) for j in (canal_doc.get("fila_atual") or [])]
        ativos = await asyncio.to_thread(token_mode_listar_todos)
        # Ordena: fila primeiro
        ordenados = sorted(ativos, key=lambda u: (0 if u["user_id"] in fila_ids else 1))

        for u in ordenados:
            if not u.get("ativo"):
                continue
            cfg = await asyncio.to_thread(token_mode_get, u["user_id"])
            if not cfg.get("token"):
                continue
            msg_id = await enviar_como_usuario(cfg["token"], channel_id, texto)
            if msg_id:
                return True
    except Exception as e:
        _log.warning(f"[token_mode fila] {e}")
    return False


def _emb_falha_criar_sala(motivo: str, tentativas: int) -> discord.Embed:
    return discord.Embed(
        title="❌ Falha ao criar sala",
        color=0xED4245,
        description=(
            f"Tentei **{tentativas}x** mas a API não respondeu.\n"
            f"Motivo: `{motivo}`\n\n"
            "**Os pagamentos foram preservados** — a fila continua trancada.\n"
            "Um admin precisa rodar `/aposta_retentar` ou `/aposta_estornar`."
        ),
    )


# ══════════════════════════════════════════════════════════════
#  Cog
# ══════════════════════════════════════════════════════════════

class ApostaAutoCog(commands.Cog):
    """Sistema automático de fila de aposta + criação de sala."""

    def __init__(self, bot):
        self.bot = bot
        # Cache de "canais já tentados parsear" pra não ficar buscando embed em todo canal aleatório
        self._tentados: set[str] = set()
        # Inicia tasks
        self.task_orfaos.start()

    def cog_unload(self):
        self.task_orfaos.cancel()

    # ──────────────────────────────────────────────────────────
    #  Listener principal — chamado por cogs/pg_match.py
    # ──────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_pg_confirmado(self, ctx: dict):
        """Disparado quando pg_match.py confirma um PIX.
        ctx = {
            "guild_id": int,
            "channel_id": int,
            "user_id": int,
            "user_name": str,
            "nome_pagador": str,
            "valor": float,
            "pix_id": str,
            "banco": str,
        }
        """
        try:
            channel = self.bot.get_channel(int(ctx["channel_id"]))
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                return
        except Exception:
            return

        # 1) Garante que o canal tem config (lazy load — parseia embed se for 1ª vez)
        canal_doc = ast.canal_get(channel.id)
        if not canal_doc and str(channel.id) not in self._tentados:
            self._tentados.add(str(channel.id))
            try:
                canal_doc = await ap.detectar_e_cachear(channel)
            except Exception as e:
                _log.warning(f"[on_pg_confirmado] erro detectar canal {channel.id}: {e}")

        if not canal_doc:
            # Não é canal de aposta. pg_match.py já respondeu o PIX, só não cria fila.
            # Marca como órfão pra possível aviso a admin (se não tiver canal)
            ast.orfao_registrar(
                pix_id=ctx["pix_id"],
                guild_id=ctx["guild_id"],
                channel_id=channel.id,
                valor=float(ctx["valor"]),
                nome_pagador=ctx["nome_pagador"],
                recebido_em=datetime.now(timezone.utc).isoformat(),
            )
            return

        # 2) Valida valor
        valor_esp = float(canal_doc.get("valor_esperado", 0))
        valor_pago = float(ctx["valor"])
        if valor_esp > 0 and valor_pago < valor_esp - 0.005:
            # Pagou menos que o esperado — recusa
            try:
                await channel.send(
                    f"<@{ctx['user_id']}>",
                    embed=_emb_valor_errado(valor_esp, valor_pago),
                )
            except Exception:
                pass
            return

        # 3) Adiciona na fila (com lock)
        ok, motivo, canal_atual = ast.fila_adicionar(
            channel_id=channel.id,
            user_id=ctx["user_id"],
            user_name=ctx["user_name"],
            nome_pagador=ctx["nome_pagador"],
            valor=valor_pago,
            pix_id=ctx["pix_id"],
        )

        if not ok:
            await self._responder_recusa(channel, ctx, motivo, canal_atual or canal_doc)
            return

        # PIX consumido → não é mais órfão
        ast.orfao_marcar_consumido(ctx["pix_id"])

        # 4) Posta confirmação
        novo_jogador = {
            "user_id": ctx["user_id"],
            "user_name": ctx["user_name"],
            "nome_pagador": ctx["nome_pagador"],
            "valor": valor_pago,
        }
        canal_atual_recarregado = ast.canal_get(channel.id) or canal_atual
        tok_enviou = await _enviar_como_user_fila(
            self.bot, canal_atual_recarregado, channel.id,
            _txt_pg_aceito(canal_atual_recarregado, novo_jogador),
        )
        if not tok_enviou:
            try:
                await channel.send(
                    f"<@{ctx['user_id']}>",
                    embed=_emb_pg_aceito(canal_atual_recarregado, novo_jogador),
                )
            except Exception as e:
                _log.warning(f"[on_pg_confirmado] erro send: {e}")

        # 5) Fila cheia? → cria sala
        if ast.fila_esta_cheia(canal_atual_recarregado):
            asyncio.create_task(self._criar_sala_automatica(channel, canal_atual_recarregado))

    async def _responder_recusa(self, channel, ctx: dict, motivo: str, canal_doc: dict):
        try:
            if motivo == "nao_autorizado":
                await channel.send(f"<@{ctx['user_id']}>", embed=_emb_nao_autorizado(canal_doc))
            elif motivo == "ja_na_fila":
                await channel.send(f"<@{ctx['user_id']}>", embed=_emb_ja_na_fila())
            elif motivo == "fila_cheia" or motivo == "sala_ja_criada":
                await channel.send(f"<@{ctx['user_id']}>", embed=_emb_sala_ja_criada())
            elif motivo == "lock":
                pass  # cooldown silencioso, evita spam
            else:
                _log.warning(f"[recusa] motivo desconhecido: {motivo}")
        except Exception as e:
            _log.warning(f"[recusa] erro send: {e}")

    # ──────────────────────────────────────────────────────────
    #  Criação automática da sala (com retry 3x)
    # ──────────────────────────────────────────────────────────
    async def _criar_sala_automatica(self, channel, canal_doc: dict):
        """Tenta criar sala via API com retry 3x. Mantém fila travada se falhar tudo."""
        # Lock o canal por 10s pra evitar 2 criações simultâneas
        ast.lock_canal_segundos(channel.id, LOCK_POS_CRIAR)

        modo = int(canal_doc.get("modo", 1))
        md = _modo_dados(modo)
        salaid = md.get("salaid")
        if not salaid:
            _log.error(f"[criar_sala] modo {modo} sem salaid em config.MODOS")
            try:
                await channel.send(embed=_emb_falha_criar_sala("modo inválido", 0))
            except Exception:
                pass
            return

        # Mensagem de "criando..."
        try:
            wait_msg = await channel.send(
                embed=discord.Embed(
                    title="⏳ Criando sala...",
                    color=0x5865F2,
                    description=f"Modo **{md['nome']}** — aguarde alguns segundos",
                )
            )
        except Exception:
            wait_msg = None

        # Retry loop
        ultimo_erro = "desconhecido"
        for tentativa in range(1, RETRY_CRIAR_SALA + 1):
            ast.incrementar_tentativa_criar_sala(channel.id)
            try:
                data = await _api_salas.criar_sala(
                    salaid=str(salaid),
                    iniciar=config.DEFAULT_INICIAR_MINUTOS,
                    modo=modo,
                )
            except Exception as e:
                ultimo_erro = str(e)
                _log.warning(f"[criar_sala] t{tentativa} exception: {e}")
                data = None

            pid = (data or {}).get("pedidoid")
            if pid:
                # Sucesso (ou pelo menos chegou status 3 / SALA_CRIADA)
                if data.get("status") != 3:
                    # Aguarda ficar pronta
                    data = await _api_salas.aguardar_sala_pronta(pid)
                if data.get("status") == 3:
                    sala = data.get("sala") or {}
                    await self._publicar_sala_criada(channel, canal_doc, sala, modo, wait_msg)
                    return

            ultimo_erro = (data or {}).get("msg", ultimo_erro) or ultimo_erro
            _log.warning(f"[criar_sala] t{tentativa}/{RETRY_CRIAR_SALA} falhou: {ultimo_erro}")

            if tentativa < RETRY_CRIAR_SALA:
                delay = RETRY_BACKOFF[min(tentativa - 1, len(RETRY_BACKOFF) - 1)]
                # Atualiza wait_msg
                if wait_msg:
                    try:
                        await wait_msg.edit(embed=discord.Embed(
                            title=f"⏳ Tentando criar sala... ({tentativa}/{RETRY_CRIAR_SALA})",
                            color=0xFAA61A,
                            description=f"API falhou, tentando de novo em {delay}s...",
                        ))
                    except Exception:
                        pass
                await asyncio.sleep(delay)

        # Tudo falhou — devolve erro mas mantém fila travada
        try:
            if wait_msg:
                await wait_msg.edit(embed=_emb_falha_criar_sala(ultimo_erro, RETRY_CRIAR_SALA))
            else:
                await channel.send(embed=_emb_falha_criar_sala(ultimo_erro, RETRY_CRIAR_SALA))
        except Exception:
            pass
        # Avisa admins do bot via DM
        await self._dm_admins(
            f"⚠️ **Falha ao criar sala automática**\n"
            f"Canal: <#{channel.id}> ({channel.guild.name})\n"
            f"Partida: `{canal_doc.get('partida_id', '?')}`\n"
            f"Modo: {md['nome']}\n"
            f"Jogadores na fila: {len(canal_doc.get('fila_atual', []))}\n"
            f"Erro: `{ultimo_erro}`\n\n"
            f"Use `/aposta_retentar` no canal pra tentar de novo, ou `/aposta_estornar` pra cancelar."
        )

    async def _publicar_sala_criada(self, channel, canal_doc: dict, sala: dict, modo: int, wait_msg):
        """Edita a mensagem de espera (ou posta nova) com o embed de sala criada."""
        sala_info = {
            "id": str(sala.get("id", "—")),
            "senha": str(sala.get("senha", "—")),
            "mapa": sala.get("nome", "BERMUDA"),
            "link": sala.get("link") or "",
            "modo": modo,
        }
        ast.marcar_sala_criada(channel.id, sala_info)

        # Recarrega doc atualizado pra usar fila final
        canal_atual = ast.canal_get(channel.id) or canal_doc
        em = _emb_sala_criada(canal_atual, sala, modo)

        tok_enviou = await _enviar_como_user_fila(
            self.bot, canal_atual, channel.id,
            _txt_sala_criada(canal_atual, sala, modo),
        )
        if not tok_enviou:
            try:
                if wait_msg:
                    await wait_msg.edit(embed=em)
                else:
                    await channel.send(embed=em)
            except Exception as e:
                _log.warning(f"[publicar_sala] erro: {e}")
        elif wait_msg:
            # Token enviou a mensagem nova — deleta o "⏳ Criando sala..."
            try:
                await wait_msg.delete()
            except Exception:
                pass

        # Registra no histórico geral do bot (cada jogador como criador da própria entrada)
        for j in canal_atual.get("fila_atual", []):
            try:
                await asyncio.to_thread(
                    registrar_sala,
                    str(j["user_id"]),
                    j["user_name"],
                    modo,
                    str(channel.guild.id),
                    "aposta_auto",
                )
            except Exception as e:
                _log.warning(f"[publicar_sala] registrar_sala erro: {e}")

    async def _dm_admins(self, mensagem: str):
        """Manda DM pros ADMIN_IDS do config."""
        for admin_id in config.ADMIN_IDS:
            try:
                user = self.bot.get_user(admin_id) or await self.bot.fetch_user(admin_id)
                if user:
                    await user.send(mensagem)
            except Exception as e:
                _log.warning(f"[dm_admin] erro user {admin_id}: {e}")

    # ──────────────────────────────────────────────────────────
    #  Task: PIX órfãos — avisa admins via DM
    # ──────────────────────────────────────────────────────────
    @tasks.loop(minutes=2)
    async def task_orfaos(self):
        try:
            orfaos = ast.orfaos_pendentes_avisar(idade_minutos=ORFAO_AVISO_MIN)
            if not orfaos:
                return
            for o in orfaos:
                pix_id = o["_id"]
                guild_id = o.get("guild_id", "?")
                channel_id = o.get("channel_id")
                msg = (
                    f"⚠️ **PIX órfão detectado**\n"
                    f"PIX `{pix_id}` está parado há mais de {ORFAO_AVISO_MIN}min sem ninguém digitar `pg`.\n\n"
                    f"💰 Valor: **R$ {float(o.get('valor', 0)):.2f}**\n"
                    f"👤 Pagador: **{o.get('nome_pagador', '?')}**\n"
                    f"📍 Servidor: `{guild_id}`\n"
                )
                if channel_id:
                    msg += f"📺 Canal: <#{channel_id}>\n"
                msg += f"\nUse `/aposta_orfaos` pra ver todos os pendentes."
                await self._dm_admins(msg)
                ast.orfao_marcar_avisado(pix_id)
        except Exception as e:
            _log.error(f"[task_orfaos] erro: {e}")

    @task_orfaos.before_loop
    async def before_orfaos(self):
        await self.bot.wait_until_ready()

    # ──────────────────────────────────────────────────────────
    #  Comandos slash de gerenciamento
    # ──────────────────────────────────────────────────────────
    @app_commands.command(
        name="aposta_status",
        description="Ver status da fila de aposta deste canal.",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_status(self, inter: discord.Interaction):
        if not inter.guild:
            return await inter.response.send_message("Use em um canal de servidor.", ephemeral=True)
        canal_doc = ast.canal_get(inter.channel_id)
        if not canal_doc:
            # Tenta detectar agora
            try:
                canal_doc = await ap.detectar_e_cachear(inter.channel)
            except Exception:
                pass
        if not canal_doc:
            return await inter.response.send_message(
                embed=discord.Embed(
                    title="❓ Não é canal de aposta",
                    color=0x5865F2,
                    description="Não encontrei a embed de aposta neste canal.",
                ),
                ephemeral=True,
            )

        md = _modo_dados(int(canal_doc.get("modo", 1)))
        fila = canal_doc.get("fila_atual") or []
        max_jog = int(canal_doc.get("max_jogadores", 2))
        autorizados = canal_doc.get("jogadores_autorizados") or []

        em = discord.Embed(
            title=f"{md['emoji']} Status da aposta",
            color=0x5865F2,
            description=(
                f"**Partida:** `{canal_doc.get('partida_id', '—')}`\n"
                f"**Modo:** {md['nome']} ({canal_doc.get('modo_label', '?')})\n"
                f"**Valor:** R$ {float(canal_doc.get('valor_esperado', 0)):.2f}".replace(".", ",") + "\n"
                f"**Vagas:** {len(fila)}/{max_jog}\n"
                f"**Sala criada:** {'✅' if canal_doc.get('sala_criada') else '⏳ aguardando'}"
            ),
        )
        if autorizados:
            em.add_field(
                name="👥 Jogadores autorizados",
                value=", ".join(f"<@{u}>" for u in autorizados),
                inline=False,
            )
        if fila:
            em.add_field(
                name="✅ Já pagaram",
                value="\n".join(
                    f"• <@{j['user_id']}> — **{j['nome_pagador']}** (R$ {j['valor']:.2f}".replace(".", ",") + ")"
                    for j in fila
                ),
                inline=False,
            )
        if canal_doc.get("sala_criada") and canal_doc.get("sala_info"):
            si = canal_doc["sala_info"]
            em.add_field(
                name="🎮 Sala",
                value=f"ID `{si.get('id', '?')}` · Senha `{si.get('senha', '?')}`",
                inline=False,
            )
        await inter.response.send_message(embed=em, ephemeral=True)

    @app_commands.command(
        name="aposta_retentar",
        description="[ADMIN] Tentar criar sala de novo (após falha).",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_retentar(self, inter: discord.Interaction):
        if inter.user.id not in config.ADMIN_IDS and not inter.user.guild_permissions.manage_guild:
            return await inter.response.send_message("Sem permissão.", ephemeral=True)
        canal_doc = ast.canal_get(inter.channel_id)
        if not canal_doc:
            return await inter.response.send_message("Não é canal de aposta.", ephemeral=True)
        if canal_doc.get("sala_criada"):
            return await inter.response.send_message("Sala já foi criada.", ephemeral=True)
        if not ast.fila_esta_cheia(canal_doc):
            return await inter.response.send_message(
                f"Fila ainda não está cheia ({len(canal_doc.get('fila_atual', []))}/{canal_doc.get('max_jogadores', 2)}).",
                ephemeral=True,
            )
        await inter.response.send_message("⏳ Tentando criar sala...", ephemeral=True)
        asyncio.create_task(self._criar_sala_automatica(inter.channel, canal_doc))

    @app_commands.command(
        name="aposta_limpar_fila",
        description="[ADMIN] Limpar fila do canal (não estorna).",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_limpar(self, inter: discord.Interaction):
        if inter.user.id not in config.ADMIN_IDS and not inter.user.guild_permissions.manage_guild:
            return await inter.response.send_message("Sem permissão.", ephemeral=True)
        ast.fila_limpar(inter.channel_id)
        await inter.response.send_message("✅ Fila limpa.", ephemeral=True)

    @app_commands.command(
        name="aposta_orfaos",
        description="[ADMIN] Ver PIX órfãos (caíram mas ninguém deu pg).",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_orfaos(self, inter: discord.Interaction):
        if inter.user.id not in config.ADMIN_IDS and not inter.user.guild_permissions.manage_guild:
            return await inter.response.send_message("Sem permissão.", ephemeral=True)
        if not inter.guild:
            return await inter.response.send_message("Use em servidor.", ephemeral=True)
        orfaos = ast.orfaos_listar_guild(inter.guild_id, apenas_pendentes=True)
        if not orfaos:
            return await inter.response.send_message(
                embed=discord.Embed(title="✅ Nada pendente", description="Sem PIX órfãos.", color=0x57F287),
                ephemeral=True,
            )
        linhas = []
        for o in orfaos[:15]:
            ch = f"<#{o.get('channel_id')}>" if o.get("channel_id") else "*(sem canal)*"
            linhas.append(
                f"• **{o.get('nome_pagador', '?')}** R$ {float(o.get('valor', 0)):.2f} · {ch}"
            )
        em = discord.Embed(
            title=f"⚠️ PIX órfãos pendentes ({len(orfaos)})",
            color=0xFAA61A,
            description="\n".join(linhas),
        )
        await inter.response.send_message(embed=em, ephemeral=True)

    @app_commands.command(
        name="aposta_recachear",
        description="[ADMIN] Re-parsear a embed pinada deste canal.",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_recachear(self, inter: discord.Interaction):
        if inter.user.id not in config.ADMIN_IDS and not inter.user.guild_permissions.manage_guild:
            return await inter.response.send_message("Sem permissão.", ephemeral=True)
        await inter.response.defer(ephemeral=True, thinking=True)
        ast.canal_remover(inter.channel_id)
        cfg = await ap.detectar_e_cachear(inter.channel)
        if not cfg:
            return await inter.followup.send(
                embed=discord.Embed(
                    title="❌ Não achei embed válida",
                    color=0xED4245,
                    description="Verifique se a mensagem com a aposta está fixada e tem os campos Partida, Modo, Valor, Jogadores.",
                ),
                ephemeral=True,
            )
        md = _modo_dados(cfg["modo"])
        await inter.followup.send(
            embed=discord.Embed(
                title="✅ Canal cacheado",
                color=0x57F287,
                description=(
                    f"**Partida:** `{cfg['partida_id']}`\n"
                    f"**Modo:** {md['emoji']} {md['nome']} (`{cfg['modo_label']}`)\n"
                    f"**Vagas:** {cfg['max_jogadores']}\n"
                    f"**Valor:** R$ {cfg['valor_esperado']:.2f}".replace(".", ",") + "\n"
                    f"**Jogadores:** {len(cfg['jogadores_autorizados'])} autorizados"
                ),
            ),
            ephemeral=True,
        )


async def setup(bot):
    # Inicializa coleções/índices
    try:
        ast.init_apostas()
    except Exception as e:
        _log.warning(f"[setup] init_apostas: {e}")
    await bot.add_cog(ApostaAutoCog(bot))
