# utils/logs.py — Logs bonitos no canal de log (v3)

import discord, logging
from datetime import datetime
from zoneinfo import ZoneInfo
from utils.emojis import BOT, STATS, SETTINGS, INFO, DOT, ON, OFF, GIFT, CART, TOP, MOBILE, MONEY, PLAY

_BR  = ZoneInfo("America/Sao_Paulo")
_bot = None
log  = logging.getLogger("salasff.logs")

def init_logger(bot):
    global _bot; _bot = bot

def _agora():
    return datetime.now(_BR).strftime("%d/%m/%Y %H:%M:%S")

async def _canal():
    if not _bot: return None
    try:
        from cogs.botconfig import carregar_cfg
        cid = carregar_cfg().get("canal_log_id")
        if cid: return _bot.get_channel(int(cid))
    except: pass
    return None

async def _send(em, view=None):
    ch = await _canal()
    if ch:
        try:
            if view is not None:
                await ch.send(embed=em, view=view)
            else:
                await ch.send(embed=em)
        except Exception as ex: log.warning(f"[LOG] falha: {ex}")

def _base(t, c=0x5865F2):
    em = discord.Embed(title=t, color=c, timestamp=datetime.now(_BR))
    return em


# ── Bot online ──
async def log_bot_online(bot_user):
    em = _base(f"{ON}  Bot Online", 0x00FF7F)
    em.add_field(name=f"{BOT} Bot", value=f"{bot_user} (`{bot_user.id}`)", inline=False)
    em.description = f"{DOT} Dados carregados. Bot pronto."
    await _send(em)

# ── Sala criada ──
async def log_sala_criada(user, modo_nome, pedidoid, sala, go, guild=None, api_nome=None):
    em = _base(f"{ON}  Sala Criada", 0x00FF7F)
    em.add_field(name=f"{INFO} Usuário", value=f"{user.mention} (`{user.id}`)", inline=True)
    em.add_field(name=f"{MOBILE} Modo", value=modo_nome, inline=True)
    em.add_field(name=f"{SETTINGS} Senha", value=f"`{sala.get('senha','—')}`", inline=True)
    em.add_field(name=f"{STATS} ID Sala", value=str(sala.get("id","—")), inline=True)
    em.add_field(name=f"{DOT} GO", value=f"{go} min", inline=True)
    em.add_field(name=f"{DOT} Servidor", value=guild.name if guild else "DM", inline=True)
    if api_nome:
        em.add_field(name=f"⚙️ API", value=api_nome, inline=True)
    await _send(em)

# ── Sala erro ──
async def log_sala_erro(user, modo_nome, motivo):
    em = _base(f"❌  Erro ao Criar Sala", 0xFF4444)
    em.add_field(name=f"{INFO} Usuário", value=f"{user.mention} (`{user.id}`)", inline=True)
    em.add_field(name=f"{MOBILE} Modo", value=modo_nome, inline=True)
    em.add_field(name="Motivo", value=motivo[:500], inline=False)
    await _send(em)

# ── Key resgatada ──
async def log_key_resgatada(user, code, quantia, usadas):
    saldo = quantia - usadas
    em = _base(f"{GIFT}  Key Resgatada", 0x00D4FF)
    em.add_field(name=f"{INFO} Usuário", value=f"{user.mention}", inline=True)
    em.add_field(name=f"{GIFT} Key", value=f"`{code}`", inline=True)
    em.add_field(name=f"{CART} Saldo", value=f"**{saldo}** salas", inline=True)
    await _send(em)

# ── Key consumida ──
async def log_key_consumida(user, code, restantes):
    em = _base(f"{STATS}  Sala Consumida", 0xFFA500)
    em.add_field(name=f"{INFO} Usuário", value=f"{user.mention}", inline=True)
    em.add_field(name=f"{GIFT} Key", value=f"`{code}`", inline=True)
    em.add_field(name=f"{CART} Restantes", value=str(restantes), inline=True)
    await _send(em)

# ── PIX gerado ──
async def log_pix_gerado(user, qtd, valor, txid):
    em = _base(f"{MONEY}  PIX Gerado", 0xFFD700)
    em.add_field(name=f"{INFO} Usuário", value=f"{user.mention}", inline=True)
    em.add_field(name=f"{MOBILE} Salas", value=str(qtd), inline=True)
    em.add_field(name=f"{MONEY} Valor", value=f"R$ {valor:.2f}", inline=True)
    em.add_field(name=f"{DOT} TXID", value=f"`{txid[:30]}…`", inline=False)
    await _send(em)

# ── Compra confirmada ──
class LogCompraView(discord.ui.View):
    """View persistente com 2 botões: Copiar ID, Copiar Nome.
    Os dados são lidos do pedido no MongoDB pelo txid.
    """
    def __init__(self):
        super().__init__(timeout=None)

    async def _enviar(self, inter: discord.Interaction, valor: str):
        if not valor:
            return await inter.response.send_message("—", ephemeral=True)
        await inter.response.send_message(valor, ephemeral=True)

    @discord.ui.button(label="Copiar ID", style=discord.ButtonStyle.secondary, custom_id="logc:id", row=0)
    async def b_id(self, inter, btn):
        # custom_id da mensagem fica em inter.message.components — não temos como extrair
        # o txid daqui. Solução: lemos do embed (campo "ID Transação").
        em = inter.message.embeds[0] if inter.message.embeds else None
        valor = ""
        if em:
            for f in em.fields:
                if "ID Transação" in (f.name or ""):
                    valor = (f.value or "").strip().strip("`")
                    break
        await self._enviar(inter, valor)

    @discord.ui.button(label="Copiar Nome", style=discord.ButtonStyle.secondary, custom_id="logc:nome", row=0)
    async def b_nome(self, inter, btn):
        em = inter.message.embeds[0] if inter.message.embeds else None
        valor = ""
        if em:
            for f in em.fields:
                if "Nome do Pagador" in (f.name or ""):
                    # Vem como **NOME**, remove asteriscos
                    valor = (f.value or "").strip().strip("*").strip()
                    break
        await self._enviar(inter, valor)


async def log_compra_confirmada(uid, nome, qtd, valor, txid, key_code, guild_nome=None, nome_pagador=None):
    em = _base(f"{ON}  Compra PIX Confirmada", 0x00FF7F)
    em.add_field(name=f"{INFO} Usuário", value=f"{nome} (`{uid}`)", inline=True)
    em.add_field(name=f"{MOBILE} Salas", value=str(qtd), inline=True)
    em.add_field(name=f"{MONEY} Valor", value=f"R$ {valor:.2f}", inline=True)
    em.add_field(name=f"{GIFT} Key", value=f"`{key_code}`", inline=False)
    em.add_field(name=f"{DOT} ID Transação", value=f"`{txid}`", inline=False)
    if nome_pagador:
        em.add_field(name=f"{INFO} Nome do Pagador", value=f"**{nome_pagador}**", inline=False)
    if guild_nome:
        em.add_field(name=f"{DOT} Servidor de Origem", value=f"**{guild_nome}**", inline=False)
    await _send(em, view=LogCompraView())

# ── /gerasala ──
async def log_gerasala(admin, quantia, qtd_keys, codes):
    em = _base(f"{GIFT}  Keys Geradas", 0xA855F7)
    em.add_field(name=f"{TOP} Admin", value=f"{admin.mention}", inline=True)
    em.add_field(name=f"{MOBILE} Salas/key", value=str(quantia), inline=True)
    em.add_field(name=f"{STATS} Qtd", value=str(qtd_keys), inline=True)
    preview = "\n".join(f"`{c}`" for c in codes[:5])
    if len(codes) > 5: preview += f"\n{DOT} +{len(codes)-5} mais"
    em.add_field(name="Códigos", value=preview, inline=False)
    await _send(em)

# ── /saldoclientes ──
async def log_saldoclientes(admin, total_cli, total_salas, top):
    em = _base(f"{STATS}  /saldocliente", 0x5865F2)
    em.add_field(name=f"{TOP} Admin", value=f"{admin.mention}", inline=True)
    em.add_field(name="Clientes", value=str(total_cli), inline=True)
    em.add_field(name="Salas", value=str(total_salas), inline=True)
    if top:
        lines = "\n".join(f"{DOT} **{d['nome']}** — {d['saldo']} salas" for _, d in top[:5])
        em.add_field(name=f"{TOP} Top 5", value=lines, inline=False)
    await _send(em)

# ── /saldototal ──
async def log_saldototal(admin, s):
    em = _base(f"{STATS}  /saldototal", 0x5865F2)
    em.add_field(name=f"{TOP} Admin", value=f"{admin.mention}", inline=False)
    em.add_field(name=f"{ON} Salas", value=f"Hoje: **{s['salas_hoje']}** | Total: **{s['salas_tot']}**", inline=False)
    em.add_field(name=f"{MONEY} Receita", value=f"Hoje: **R$ {s['receita_hoje']:.2f}** | Total: **R$ {s['receita_tot']:.2f}**", inline=False)
    await _send(em)

# ── /verifica ──
async def log_verifica(admin, alvo, total, pagos, valor_total):
    em = _base(f"{STATS}  /verifica", 0x2ecc71)
    em.add_field(name=f"{TOP} Admin", value=f"{admin.mention}", inline=True)
    em.add_field(name=f"{INFO} Alvo", value=f"{alvo.mention}", inline=True)
    em.add_field(name="Pedidos", value=f"{total} ({pagos} pagos)", inline=True)
    em.add_field(name=f"{MONEY} Total", value=f"R$ {valor_total:.2f}", inline=True)
    await _send(em)

# ── /verificakey ──
async def log_verificakey(admin, code, dono, dono_id, saldo, usadas, total):
    em = _base(f"{GIFT}  /verificakey", 0xA855F7)
    em.add_field(name=f"{TOP} Admin", value=f"{admin.mention}", inline=True)
    em.add_field(name=f"{GIFT} Key", value=f"`{code}`", inline=True)
    em.add_field(name=f"{INFO} Dono", value=f"{dono}", inline=True)
    em.add_field(name=f"{CART} Saldo", value=f"**{saldo}**/{total}", inline=True)
    await _send(em)

# ── /voltasaldo ──
async def log_voltasaldo(admin, usuario, quantia, key_code):
    em = _base(f"{ON}  Saldo Restaurado", 0x00D4FF)
    em.add_field(name=f"{TOP} Admin", value=f"{admin.mention}", inline=True)
    em.add_field(name=f"{INFO} Usuário", value=f"{usuario.mention}", inline=True)
    em.add_field(name=f"{CART} Salas", value=f"**{quantia}**", inline=True)
    em.add_field(name=f"{GIFT} Key", value=f"`{key_code}`", inline=False)
    await _send(em)

# ── /removersalas ──
async def log_removersalas(admin, usuario, removidas, saldo_final):
    em = _base(f"{OFF}  Salas Removidas", 0xFF8C00)
    em.add_field(name=f"{TOP} Admin", value=f"{admin.mention}", inline=True)
    em.add_field(name=f"{INFO} Usuário", value=f"{usuario.mention}", inline=True)
    em.add_field(name="Removidas", value=f"**{removidas}**", inline=True)
    em.add_field(name="Saldo final", value=f"**{saldo_final}**", inline=True)
    await _send(em)

# ── Snapshot saldo ──
async def log_snapshot_saldo():
    from utils.database import todas_keys_com_saldo
    keys = todas_keys_com_saldo()
    if not keys: return
    cl = {}
    for k in keys:
        uid=k["dono_id"]; s=k["quantia"]-k["salas_usadas"]
        if uid not in cl: cl[uid]={"nome":k["dono_nome"]or"?","saldo":0}
        cl[uid]["saldo"]+=s
    ords = sorted(cl.items(), key=lambda x:x[1]["saldo"], reverse=True)
    total = sum(v["saldo"] for v in cl.values())
    em = _base(f"{STATS}  Snapshot — {len(cl)} clientes", 0x5865F2)
    lines = [f"`{v['nome']}` — **{v['saldo']}** salas" for _,v in ords[:15]]
    if len(ords)>15: lines.append(f"{DOT} +{len(ords)-15} mais")
    em.description = "\n".join(lines)
    em.add_field(name=f"{TOP} Total", value=f"**{total}** salas", inline=False)
    await _send(em)


async def log_bonus_resgatado(user, salas):
    em = _base(f"🎁 Bônus Resgatado — {user.display_name}", 0xFFD700)
    em.description = f"{user.mention} resgatou **{salas} sala(s) bônus**"
    await _send(em)


async def log_bonus_automatico(user_id, user_nome, salas):
    """Log interno de bônus creditado AUTOMATICAMENTE após uma compra."""
    em = _base(f"🎁 Bônus Automático — {user_nome}", 0xFFD700)
    em.description = (
        f"<@{user_id}> (`{user_id}`) recebeu **{salas} sala(s) bônus** "
        f"automaticamente ao completar o ciclo de compras."
    )
    await _send(em)


# ══════════════════════════════════════════════════════════════
#  LOGS PÚBLICOS — canais configurados pelo /logs
# ══════════════════════════════════════════════════════════════

async def _canal_pub(cfg_key):
    """Busca canal público pelo campo em botconfig.json."""
    if not _bot:
        return None
    try:
        from cogs.botconfig import carregar_cfg
        cid = carregar_cfg().get(cfg_key)
        if cid:
            return _bot.get_channel(int(cid))
    except Exception:
        pass
    return None


async def log_pub_compra(user_id, user_nome, quantia, valor, guild_nome=None, txid=None, nome_pagador=None):
    """Envia embed público de compra no canal configurado."""
    ch = await _canal_pub("canal_compras_pub_id")
    if not ch:
        return
    agora = datetime.now(_BR)
    em = discord.Embed(color=0x00FF7F, timestamp=agora)
    em.title = f"{MONEY}  Nova Compra!"

    # Layout em campos pra LogCompraView conseguir extrair os valores
    em.add_field(name=f"{INFO} Usuário", value=f"{user_nome} (`{user_id}`)", inline=False)
    em.add_field(name=f"{CART} Comprou",  value=f"**{quantia} sala(s)**", inline=True)
    em.add_field(name=f"{MONEY} Valor",   value=f"R$ {valor:.2f}",        inline=True)
    if guild_nome:
        em.add_field(name=f"{DOT} Servidor", value=f"**{guild_nome}**", inline=False)
    if txid:
        em.add_field(name=f"{DOT} ID Transação", value=f"`{txid}`", inline=False)
    if nome_pagador:
        em.add_field(name=f"{INFO} Nome do Pagador", value=f"**{nome_pagador}**", inline=False)
    em.add_field(name=f"{DOT} Horário", value=agora.strftime("%d/%m/%Y às %H:%M:%S"), inline=False)

    try:
        # Só anexa a view se houver pelo menos um dos dois (ID ou Nome) pra copiar
        if txid or nome_pagador:
            await ch.send(embed=em, view=LogCompraView())
        else:
            await ch.send(embed=em)
    except Exception as ex:
        log.warning(f"[LOG_PUB_COMPRA] falha: {ex}")


async def log_pub_avaliacao(user_id, user_nome, estrelas: int, comentario: str, avatar_url=None):
    """Envia embed de avaliação no canal configurado."""
    ch = await _canal_pub("canal_avaliacao_pub_id")
    if not ch:
        return
    agora = datetime.now(_BR)
    estrelas_txt = "⭐" * estrelas + "✩" * (5 - estrelas)
    cor = [0xFF4444, 0xFF8C00, 0xFFD700, 0x00D4FF, 0x00FF7F][estrelas - 1]
    em = discord.Embed(color=cor, timestamp=agora)
    em.title = f"⭐  Nova Avaliação — {estrelas}/5"
    em.description = f"{estrelas_txt}"
    em.add_field(name=f"{INFO}  Usuário", value=f"{user_nome} (`{user_id}`)", inline=True)
    em.add_field(name=f"⭐  Nota", value=f"**{estrelas}/5**", inline=True)
    if comentario:
        em.add_field(name=f"💬  Comentário", value=f"> {comentario[:500]}", inline=False)
    em.set_footer(text=f"Avaliação enviada em {agora.strftime('%d/%m/%Y às %H:%M')}")
    if avatar_url:
        em.set_thumbnail(url=avatar_url)
    try:
        await ch.send(embed=em)
    except Exception as ex:
        log.warning(f"[LOG_PUB_AVALIACAO] falha: {ex}")


async def log_pub_sugestao(user_id, user_nome, sugestao: str, avatar_url=None):
    """Envia embed de sugestão no canal público configurado E no canal de log interno."""
    agora = datetime.now(_BR)
    em = discord.Embed(color=0xA855F7, timestamp=agora)
    em.title = f"💡  Nova Sugestão"
    em.add_field(name=f"{INFO}  Usuário", value=f"{user_nome} (`{user_id}`)", inline=False)
    em.add_field(name=f"💡  Sugestão", value=f"> {sugestao[:1000]}", inline=False)
    em.set_footer(text=f"Sugestão enviada em {agora.strftime('%d/%m/%Y às %H:%M')}")
    if avatar_url:
        em.set_thumbnail(url=avatar_url)

    # 1) Canal público configurado no /logs → Setar Sugestões
    ch_pub = await _canal_pub("canal_sugestao_pub_id")
    if ch_pub:
        try:
            await ch_pub.send(embed=em)
        except Exception as ex:
            log.warning(f"[LOG_PUB_SUGESTAO] canal público falhou: {ex}")
    else:
        log.info("[LOG_PUB_SUGESTAO] canal_sugestao_pub_id não configurado")

    # 2) Canal de log interno (sempre que estiver definido)
    try:
        await _send(em)
    except Exception as ex:
        log.warning(f"[LOG_PUB_SUGESTAO] canal interno falhou: {ex}")


async def log_pub_bonus(user_id, user_nome, salas_bonus, avatar_url=None, automatico=False):
    """Envia embed público de bônus resgatado no canal configurado."""
    ch = await _canal_pub("canal_bonus_pub_id")
    if not ch:
        return
    agora = datetime.now(_BR)
    em = discord.Embed(color=0xFFD700, timestamp=agora)
    em.title = f"{GIFT}  Bônus Recebido!" if automatico else f"{GIFT}  Bônus Resgatado!"
    _label = "Ganhou (automático)" if automatico else "Resgatou"
    em.description = (
        f"{INFO}  **Usuário:** {user_nome} (`{user_id}`)\n\n"
        f"{GIFT}  **{_label}:** **{salas_bonus} sala(s) bônus**\n\n"
        f"{DOT}  **Horário:** {agora.strftime('%d/%m/%Y às %H:%M:%S')}"
    )
    if avatar_url:
        em.set_thumbnail(url=avatar_url)
    try:
        await ch.send(embed=em)
    except Exception as ex:
        log.warning(f"[LOG_PUB_BONUS] falha: {ex}")
