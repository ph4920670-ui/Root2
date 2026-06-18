# cogs/comprar.py — Sistema de compra PIX (v3 + org integration)

import asyncio, io, base64, logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo

import config
_ADMIN_GUILDS = [discord.Object(id=gid) for gid in config.OWNER_GUILD_IDS]
from utils import logs as _logs

_log = logging.getLogger("salasff.comprar")
from utils.emojis import PE, BOT, STATS, SETTINGS, INFO, DOT, ON, OFF, GIFT, CART, TOP, MOBILE, PLAY, MONEY
from utils.pix import criar_cobranca_pix, consultar_cobranca, get_preco_por_sala, get_preco_por_sala_guild, get_banco_ativo
from utils.database import (
    criar_pedido_pix, buscar_pedido_por_txid, confirmar_pedido_pix,
    pedidos_pendentes, criar_keys, salvar_key_pedido, pedidos_por_nome, pedidos_por_id,
    bonus_registrar_compra,
)

_BR = ZoneInfo("America/Sao_Paulo")

def _emb(t="", c=0x5865F2, d=""):
    em = discord.Embed(title=t, color=c)
    if d: em.description = d
    return em
def _ok(t, d=""): return _emb(f"{ON}  {t}", config.COR_SUCESSO, d)
def _err(t, d=""): return _emb(f"❌  {t}", config.COR_ERRO, d)


class ComprarModal(discord.ui.Modal, title="🛒 Comprar Salas"):
    def __init__(self, guild_id=None):
        super().__init__()
        self.guild_id = guild_id
        preco = get_preco_por_sala_guild(guild_id)
        self.quantia = discord.ui.TextInput(label=f"Quantidade de salas  (R$ {preco:.2f}/sala)", placeholder="Mínimo 30 salas", min_length=1, max_length=5)
        self.add_item(self.quantia)

    async def on_submit(self, i):
        await i.response.defer(thinking=True, ephemeral=True)
        try:
            from cogs.botconfig import vendas_ativas
            if not vendas_ativas():
                return await i.followup.send(embed=_err("Vendas desativadas."), ephemeral=True)
        except: pass
        try:
            qtd = int(self.quantia.value.strip())
            if qtd < 1: raise ValueError
        except ValueError:
            return await i.followup.send(embed=_err("Quantidade inválida."), ephemeral=True)

        if qtd < 30:
            return await i.followup.send(embed=_err("Mínimo 30 salas", f"{DOT} A quantidade mínima para compra é **30 salas**."), ephemeral=True)

        gid = self.guild_id or (str(i.guild.id) if i.guild else None)
        preco = get_preco_por_sala_guild(gid)
        valor = round(qtd * preco, 2)

        from utils.pix import MISTIC_VALOR_MINIMO, get_banco_ativo as _get_banco
        _banco_check = _get_banco(qtd)
        if _banco_check != "efi" and valor < MISTIC_VALOR_MINIMO:
            import math
            min_qtd = math.ceil(MISTIC_VALOR_MINIMO / preco)
            return await i.followup.send(embed=_err(
                "Valor mínimo não atingido",
                f"{DOT} O valor mínimo para compra é **R$ {MISTIC_VALOR_MINIMO:.2f}**.\n"
                f"{DOT} Compre pelo menos **{min_qtd} salas** (R$ {round(min_qtd * preco, 2):.2f})."
            ), ephemeral=True)
        try: pix = criar_cobranca_pix(valor, f"{qtd} salas SalasFF", quantidade=qtd)
        except Exception as ex:
            return await i.followup.send(embed=_err(f"Erro PIX: {ex}"), ephemeral=True)

        banco_usado = get_banco_ativo(qtd)
        criar_pedido_pix(str(i.user.id), i.user.display_name, pix["txid"], qtd, valor, banco_usado,
                         guild_id=gid, guild_nome=(i.guild.name if i.guild else None))
        asyncio.create_task(_logs.log_pix_gerado(i.user, qtd, valor, pix["txid"]))

        em = _emb(f"{MONEY}  Pagamento PIX Gerado", 0xA855F7)
        em.description = (
            f"{DOT} **Salas:** {qtd}\n"
            f"{DOT} **Valor:** R$ {valor:.2f}\n\n"
            f"{CART} **PIX Copia e Cola:**\n```{pix['copia_cola']}```\n"
            f"{ON} Após o pagamento seu saldo será creditado automaticamente!\n"
            f"{SETTINGS} O PIX expira em **1 hora**."
        )
        files = []
        if pix.get("qrcode"):
            try:
                img = base64.b64decode(pix["qrcode"].split(",")[-1])
                files.append(discord.File(io.BytesIO(img), "qrcode.png"))
                em.set_image(url="attachment://qrcode.png")
            except: pass
        await i.followup.send(embed=em, files=files, view=PixView(pix['copia_cola']), ephemeral=True)


class CopiarKeyView(discord.ui.View):
    def __init__(self, kc): super().__init__(timeout=None); self.kc = kc
    @discord.ui.button(label="📋 Copiar Key", style=discord.ButtonStyle.primary)
    async def c(self, i, b): await i.response.send_message(self.kc, ephemeral=True)


class PixView(discord.ui.View):
    def __init__(self, cc): super().__init__(timeout=3600); self.cc = cc
    @discord.ui.button(label="Copiar Código PIX", emoji=PE["copiar"], style=discord.ButtonStyle.primary, custom_id="pix:copiar")
    async def c(self, i, b): await i.response.send_message(self.cc, ephemeral=True)


class PainelComprarView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Meu Perfil", emoji=PE["info"], style=discord.ButtonStyle.secondary, custom_id="comprar:perfil", row=0)
    async def perfil(self, i, b):
        # Mesma UI do /c — Components V2 com botões funcionais (Comprar Salas, Outros)
        from utils.database import perfil_usuario, go_config_get
        from utils.pix import get_preco_por_sala_guild
        from utils.emojis import e as _emoji_str
        import aiohttp, logging
        _log_p = logging.getLogger("salasff.comprar")

        try:
            await i.response.defer(ephemeral=True)
        except Exception:
            pass

        alvo = i.user
        uid = str(alvo.id)
        d = await asyncio.to_thread(perfil_usuario, uid)
        _gid = str(i.guild.id) if i.guild else None
        preco = get_preco_por_sala_guild(_gid)
        try:
            go_atual = await asyncio.to_thread(go_config_get, uid)
        except Exception:
            go_atual = 0
        go_label = f"{go_atual} min" if go_atual > 0 else f"{config.DEFAULT_INICIAR_MINUTOS} min (padrão)"

        def _emj(k):
            em = PE[k]
            return {"id": str(em.id), "name": em.name, "animated": em.animated}
        def _sec(idx, txt, btn): return {"id": idx, "type": 9, "components": [{"id": idx+1, "type": 10, "content": txt}], "accessory": btn}
        def _btn(idx, label, cid, style=2, emoji=None, disabled=False):
            b_ = {"id": idx, "type": 2, "style": style, "label": label, "custom_id": cid}
            if emoji: b_["emoji"] = emoji
            if disabled: b_["disabled"] = True
            return b_

        payload = {
            "flags": 64 | 32768,
            "components": [{"id": 1, "type": 17, "components": [
                {"id": 2, "type": 10, "content": f"## {_emoji_str('store')} Perfil de {alvo.mention}"},
                _sec(3,  f"**Comprar Salas**\nR$ {preco:.2f} por sala — mínimo 30 salas",
                         _btn(5,  "Comprar Salas", "cw:comprar", style=3, emoji=_emj("carteira"))),
                _sec(6,  "**Outros**\nVer lucro, config GO, bônus e histórico.",
                         _btn(8,  "Outros",        "cw:outros",  style=1, emoji=_emj("settings"))),
                {"id": 9, "type": 14, "divider": True, "spacing": 1},
                _sec(10, "**Salas Disponíveis**\nTotal de salas que o usuário possui.",
                         _btn(12, f"{d['saldo']} salas",                "cw:saldo",  disabled=True, emoji=_emj("cloud"))),
                _sec(13, "**Salas Gastas**\nTotal de salas criadas pelo usuário.",
                         _btn(15, f"Total de {d['total']} salas gastas","cw:gastas", disabled=True, emoji=_emj("vision"))),
                {"id": 16, "type": 1, "components": [
                    {"id": 17, "type": 2, "style": 2, "label": f"Hoje: {d['hoje']}",    "custom_id": "cw:hoje",   "disabled": True, "emoji": _emj("calendario")},
                    {"id": 18, "type": 2, "style": 2, "label": f"Ontem: {d['ontem']}",  "custom_id": "cw:ontem",  "disabled": True, "emoji": _emj("calendario")},
                    {"id": 19, "type": 2, "style": 2, "label": f"3 dias: {d['3dias']}", "custom_id": "cw:3dias",  "disabled": True, "emoji": _emj("calendario")},
                ]},
                {"id": 20, "type": 1, "components": [
                    {"id": 21, "type": 2, "style": 2, "label": f"Semana: {d['semana']}","custom_id": "cw:semana", "disabled": True, "emoji": _emj("calendario")},
                    {"id": 22, "type": 2, "style": 2, "label": f"Mês: {d.get('mes', 0)}","custom_id": "cw:mes",    "disabled": True, "emoji": _emj("calendario")},
                ]},
            ]}],
        }

        # Envia via webhook do followup (mesma técnica do /c)
        url = f"https://discord.com/api/v10/webhooks/{i.application_id}/{i.token}?wait=true"
        ok = False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(url, json=payload) as r:
                    ok = r.status in (200, 201)
                    if not ok:
                        _log_p.warning(f"[perfil v2] status={r.status} body={(await r.text())[:300]}")
        except Exception as ex:
            _log_p.warning(f"[perfil v2] {ex}")

        # Fallback simples se Components V2 falhar
        if not ok:
            try:
                em = discord.Embed(title=f"{INFO}  Carteira de {alvo.mention}", color=0x5865F2)
                em.set_thumbnail(url=alvo.display_avatar.url)
                em.add_field(name="**Salas Disponíveis**", value=f"> **{d['saldo']} salas**", inline=False)
                em.add_field(name="**Salas Gastas**",
                             value=f"> Hoje: **{d['hoje']}** · 7d: **{d['semana']}** · Total: **{d['total']}**",
                             inline=False)
                em.add_field(name="**Comprar Salas**", value=f"R$ {preco:.2f}/sala — mín. 30", inline=False)
                await i.followup.send(embed=em, ephemeral=True)
            except Exception as ex2:
                _log_p.error(f"[perfil fallback] {ex2}")

    @discord.ui.button(label="Comprar Salas", emoji=PE["stats"], style=discord.ButtonStyle.success, custom_id="comprar:comprar", row=0)
    async def comprar(self, i, b):
        gid = str(i.guild.id) if i.guild else None
        await i.response.send_modal(ComprarModal(gid))


# ═══════════════════════════════════════════
#  Avaliação & Sugestão — DM após pagamento
# ═══════════════════════════════════════════

class AvaliacaoModal(discord.ui.Modal, title="⭐ Avaliar Compra"):
    nota = discord.ui.TextInput(
        label="Nota (1 a 5 estrelas)",
        placeholder="Digite um número de 1 a 5",
        min_length=1,
        max_length=1,
    )
    comentario = discord.ui.TextInput(
        label="Comentário (opcional)",
        style=discord.TextStyle.paragraph,
        placeholder="O que achou do serviço? (pode deixar em branco)",
        required=False,
        max_length=500,
    )

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        try:
            estrelas = int(self.nota.value.strip())
            if estrelas < 1 or estrelas > 5:
                raise ValueError
        except ValueError:
            em = discord.Embed(title="❌  Nota inválida!", color=0xFF4444)
            em.description = "> Digite apenas um número de **1 a 5**."
            return await inter.followup.send(embed=em, ephemeral=True)

        comentario = self.comentario.value.strip() if self.comentario.value else ""
        estrelas_txt = "⭐" * estrelas + "✩" * (5 - estrelas)

        em = discord.Embed(title="✅  Avaliação Enviada!", color=0x00FF7F)
        em.description = (
            f"{estrelas_txt}\n\n"
            f"> Obrigado pelo seu feedback! Isso nos ajuda a melhorar."
        )
        await inter.followup.send(embed=em, ephemeral=True)

        # Envia pro canal de log
        avatar = inter.user.display_avatar.url if inter.user.display_avatar else None
        asyncio.create_task(_logs.log_pub_avaliacao(
            str(inter.user.id), inter.user.display_name,
            estrelas, comentario, avatar
        ))


class SugestaoModal(discord.ui.Modal, title="💡 Enviar Sugestão"):
    sugestao = discord.ui.TextInput(
        label="Sua sugestão",
        style=discord.TextStyle.paragraph,
        placeholder="Escreva aqui sua sugestão para melhorarmos o serviço...",
        min_length=10,
        max_length=1000,
    )

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        em = discord.Embed(title="✅  Sugestão Enviada!", color=0xA855F7)
        em.description = "> Obrigado! Sua sugestão foi enviada e será analisada."
        await inter.followup.send(embed=em, ephemeral=True)

        avatar = inter.user.display_avatar.url if inter.user.display_avatar else None
        asyncio.create_task(_logs.log_pub_sugestao(
            str(inter.user.id), inter.user.display_name,
            self.sugestao.value.strip(), avatar
        ))


class AvaliacaoSugestaoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Avaliar Compra", emoji=PE["clockcheck"], style=discord.ButtonStyle.success, row=0)
    async def btn_avaliar(self, inter, btn):
        await inter.response.send_modal(AvaliacaoModal())

    @discord.ui.button(label="Dar Sugestão", emoji=PE["url"], style=discord.ButtonStyle.primary, row=0)
    async def btn_sugestao(self, inter, btn):
        await inter.response.send_modal(SugestaoModal())


class ComprarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.verificar_pagamentos.start()
        self.backup_periodico.start()

    def cog_unload(self):
        self.verificar_pagamentos.cancel()
        self.backup_periodico.cancel()

    # ── Listener: PIX recebido via site Fmed (Nubank/MacroDroid) ──
    # Disparado pelo loop pix_site_task em main.py.
    # Estrutura do evento:
    #   pix = {
    #     "id":         "ab12cd34...",
    #     "valor":      "5,50",         # string com vírgula
    #     "nome":       "Gabriel Fernando",
    #     "raw":        "Você recebeu R$ 5,50 de Gabriel Fernando via Pix",
    #     "recebidoEm": "2026-04-28T03:21:15.597Z",
    #   }
    #
    # Por padrão só registra log. Pra plugar na fila de salas / pedidos,
    # implemente abaixo o match (por valor + nome) com seus pedidos pendentes.
    @commands.Cog.listener()
    async def on_pix_recebido(self, pix: dict):
        import logging as _lg
        _log_site = _lg.getLogger("salasff.pix_site")
        _log_site.info(
            f"[pix_site] recebido: R$ {pix.get('valor')} de "
            f"{pix.get('nome')} (id={pix.get('id')})"
        )

    @tasks.loop(seconds=5, reconnect=True)
    async def verificar_pagamentos(self):
        try:
            from utils.database import expirar_pedidos_velhos
            await asyncio.to_thread(expirar_pedidos_velhos)
            pend = await asyncio.to_thread(pedidos_pendentes)
            if not pend: return
            from utils.pix import consultar_cobranca_async
            async def chk(p):
                try:
                    c = await consultar_cobranca_async(p["txid"], p.get("banco"))
                    if c.get("status") == "CONCLUIDA":
                        await self._aprovar(p, c)
                except Exception as ex:
                    import logging as _lg
                    _lg.getLogger("salasff.pix").warning(f"[VERIFICAR] txid={p.get('txid')} erro: {ex}")
            for j in range(0, len(pend), 5):
                await asyncio.gather(*[chk(p) for p in pend[j:j+5]])
        except Exception as ex:
            import logging as _lg
            _lg.getLogger("salasff.pix").error(f"[VERIFICAR LOOP] Erro geral: {ex}")

    async def _aprovar(self, p, consulta=None):
        # Pega nome do pagador e endToEndId real da MisticPay.
        # A rota /transactions/check NÃO retorna esses dados — só o estado.
        # Então buscamos via /users/transactions/list filtrando por clientTransactionId.
        nome_pagador = None
        txid_real = None
        try:
            from utils.pix import consultar_detalhes_pagador_async
            det = await asyncio.wait_for(
                consultar_detalhes_pagador_async(p["txid"], p.get("banco")),
                timeout=10,
            )
            if det:
                nome_pagador = (det.get("clientName") or "").strip() or None
                txid_real = (det.get("endToEndId") or det.get("transactionId") or "").strip() or None
        except Exception:
            pass

        # Marca como pago já com os extras (pra aparecer em /usuarioconfig e Meu Perfil)
        await asyncio.to_thread(
            confirmar_pedido_pix, p["txid"], nome_pagador=nome_pagador, endtoend=txid_real
        )

        # ID que aparece no log: endToEndId real se disponível, senão o txid local
        txid_log = txid_real or p["txid"]

        # Verifica se é compra de servidor (user_id começa com "guild_")
        uid = str(p["user_id"])
        if uid.startswith("guild_"):
            # Compra de servidor
            from utils.database import guild_adicionar_saldo
            guild_id = uid.replace("guild_", "")
            novo_saldo = await asyncio.to_thread(guild_adicionar_saldo, guild_id, p["quantia"])
            await asyncio.to_thread(salvar_key_pedido, p["txid"], f"guild:{guild_id}")

            # Tenta notificar no servidor
            try:
                guild = self.bot.get_guild(int(guild_id))
                if guild:
                    # Busca o primeiro canal de texto que o bot pode mandar
                    for ch in guild.text_channels:
                        if ch.permissions_for(guild.me).send_messages:
                            em = discord.Embed(
                                title=f"{ON}  Salas Compradas!",
                                color=0x00FF7F,
                            )
                            em.description = (
                                f"**{p['quantia']} sala(s)** foram adicionadas ao servidor!\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
                            )
                            em.add_field(name=f"{CART}  Saldo Atual", value=f"> **{novo_saldo}** salas", inline=True)
                            em.add_field(name=f"{MONEY}  Valor", value=f"> R$ {p['valor']:.2f}", inline=True)
                            await ch.send(embed=em)
                            break
            except:
                pass

            _gname = guild.name if guild else (p.get("guild_nome") or None)
            asyncio.create_task(_logs.log_compra_confirmada(uid, p["user_nome"], p["quantia"], p["valor"], txid_log, f"guild:{guild_id}", _gname, nome_pagador))
            asyncio.create_task(_logs.log_pub_compra(uid, p["user_nome"], p["quantia"], p["valor"], _gname, txid_log, nome_pagador))

            # Contabiliza lucro da org (só se lucro_centavos_sala > 0)
            try:
                from utils.database import guild_config_get as _gcg2, guild_config_set as _gcs2
                gcfg_lucro = _gcg2(guild_id)
                lucro_cent = int(gcfg_lucro.get("lucro_centavos_sala", 0) or 0)
                if lucro_cent > 0:
                    ganho = round(p["quantia"] * (lucro_cent / 100.0), 4)
                    total_atual = float(gcfg_lucro.get("lucro_total", 0.0) or 0.0)
                    contadas_atual = int(gcfg_lucro.get("lucro_salas_contadas", 0) or 0)
                    _gcs2(guild_id, {
                        "lucro_total": round(total_atual + ganho, 4),
                        "lucro_salas_contadas": contadas_atual + p["quantia"],
                    })
                    import logging as _lg_lucro
                    _lg_lucro.getLogger("salasff.lucroorg").info(
                        f"[lucro-org] guild={guild_id} +R${ganho:.2f} ({p['quantia']} salas × {lucro_cent}c) → total=R${total_atual+ganho:.2f}"
                    )
            except Exception as _ex_lucro:
                import logging as _lg_lucro
                _lg_lucro.getLogger("salasff.lucroorg").warning(f"[lucro-org] erro: {_ex_lucro}")

            # ── INTEGRAÇÃO ORG: registrar venda da org de revenda (compra de saldo do servidor) ──
            try:
                from utils.database_orgs import org_registrar_venda as _org_venda
                await asyncio.to_thread(_org_venda, guild_id, float(p["valor"]), int(p["quantia"]))
            except Exception as _ex_org:
                import logging as _lg_org
                _lg_org.getLogger("salasff.org").warning(f"[org-venda-guild] erro: {_ex_org}")

            # Envia log no canal configurado pelo /dev > Logs de Compras
            try:
                from utils.database import guild_config_get as _gcg
                gcfg = _gcg(guild_id)
                canal_id = gcfg.get("canal_compras_id")
                if canal_id:
                    canal = self.bot.get_channel(int(canal_id))
                    if canal:
                        em_log = discord.Embed(
                            title=f"{ON}  Nova Compra — {guild.name if guild else guild_id}",
                            color=0x00FF7F,
                        )
                        em_log.add_field(name=f"{CART}  Salas Compradas", value=f"> **{p['quantia']}**", inline=True)
                        em_log.add_field(name=f"{MONEY}  Valor Pago",     value=f"> **R$ {p['valor']:.2f}**", inline=True)
                        em_log.add_field(name=f"{STATS}  Saldo Atual",    value=f"> **{novo_saldo}**", inline=True)
                        await canal.send(embed=em_log)
            except Exception:
                pass

            await self._backup("pós-compra-servidor")
            return

        # Compra normal de usuário
        from utils.database import adicionar_saldo_usuario
        code = await asyncio.to_thread(adicionar_saldo_usuario, uid, p["user_nome"], p["quantia"])
        await asyncio.to_thread(salvar_key_pedido, p["txid"], code)

        # OBS: o bônus NÃO é mais contado por compra — agora é contado por
        # salas CRIADAS (ver _criar_sala_flow em cogs/main.py). A cada N salas
        # criadas o usuário ganha +X grátis automaticamente.

        # Dá salas bônus automaticamente só se evento estiver ativo no /mod
        try:
            from cogs.botconfig import get_evento_ativo
            from utils.database import adicionar_saldo_usuario as _add_saldo
            evento = get_evento_ativo()
            if evento in ("10+1", "10+2"):
                bonus_por_dez = 1 if evento == "10+1" else 2
                bonus_salas = (p["quantia"] // 10) * bonus_por_dez
                if bonus_salas > 0:
                    await asyncio.to_thread(_add_saldo, uid, p["user_nome"], bonus_salas)
                    asyncio.create_task(_logs.log_compra_confirmada(
                        uid, p["user_nome"], bonus_salas, 0.0,
                        f"EVENTO_{evento}", f"bonus_evento_{evento}"
                    ))
        except Exception:
            pass

        try:
            from cogs.botconfig import dar_cargo_comprador
            # Prioridade: guild onde a compra foi feita (do pedido) + suas guilds principais
            gids_tentar = []
            pedido_gid = p.get("guild_id")
            if pedido_gid:
                try:
                    gids_tentar.append(int(pedido_gid))
                except (TypeError, ValueError):
                    pass
            # Suas guilds (fallback)
            for _g in config.GUILD_IDS:
                if _g not in gids_tentar:
                    gids_tentar.append(_g)
            # Fallback extra: todas as guilds onde o user é membro
            try:
                for guild_obj in self.bot.guilds:
                    if guild_obj.id in gids_tentar:
                        continue
                    if guild_obj.get_member(int(uid)):
                        gids_tentar.append(guild_obj.id)
            except Exception:
                pass

            for gid in gids_tentar:
                try:
                    await dar_cargo_comprador(self.bot, gid, int(uid), p["quantia"])
                except Exception:
                    pass
            # Cargo de saldo
            try:
                from cogs.botconfig import aplicar_cargo_saldo
                await aplicar_cargo_saldo(self.bot, int(uid))
            except Exception:
                pass
        except: pass

        # DM bonito pro comprador
        try:
            user = await self.bot.fetch_user(int(uid))
            em = discord.Embed(
                title=f"{ON}  Pagamento Confirmado!",
                color=0x00FF7F,
            )
            em.description = (
                f"Olá **{p['user_nome']}**! Seu pagamento foi aprovado.\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            em.add_field(
                name=f"{CART}  Saldo Adicionado",
                value=f"> **{p['quantia']} sala(s)** já estão no seu saldo!",
                inline=False,
            )
            em.add_field(
                name=f"{MONEY}  Valor Pago",
                value=f"> R$ {p['valor']:.2f}",
                inline=True,
            )
            em.add_field(
                name=f"{DOT}  Salas",
                value=f"> {p['quantia']} unidades",
                inline=True,
            )
            em.add_field(
                name=f"{STATS}  Como Usar",
                value=(
                    f"> `/c1` — Criar sala **Normal**\n"
                    f"> `/c2` — Criar sala **Infinito**\n"
                    f"> `/c3` — Criar sala **Full Capa**\n"
                    f"> `/c` — Ver sua carteira"
                ),
                inline=False,
            )
            # Mostra bônus de evento na DM
            try:
                from cogs.botconfig import get_evento_ativo
                evento = get_evento_ativo()
                if evento in ("10+1", "10+2"):
                    bonus_por_dez = 1 if evento == "10+1" else 2
                    bonus_ganho = (p["quantia"] // 10) * bonus_por_dez
                    if bonus_ganho > 0:
                        em.add_field(
                            name=f"🎉  Bônus de Evento — {evento}!",
                            value=(
                                f"> **+{bonus_ganho} sala(s) grátis** foram adicionadas!\n"
                                f"> *(A cada 10 salas, +{bonus_por_dez} grátis)*"
                            ),
                            inline=False,
                        )
            except Exception:
                pass
            await user.send(embed=em, view=AvaliacaoSugestaoView())
        except: pass

        _gname = p.get("guild_nome")
        asyncio.create_task(_logs.log_compra_confirmada(p["user_id"], p["user_nome"], p["quantia"], p["valor"], txid_log, code, _gname, nome_pagador))
        asyncio.create_task(_logs.log_pub_compra(p["user_id"], p["user_nome"], p["quantia"], p["valor"], _gname, txid_log, nome_pagador))
        asyncio.create_task(_logs.log_snapshot_saldo())

        # Contabiliza lucro da org (compra pessoal feita DENTRO da org)
        try:
            pedido_guild_id = p.get("guild_id")
            if pedido_guild_id:
                from utils.database import guild_config_get as _gcg3, guild_config_set as _gcs3
                gcfg_lucro = _gcg3(pedido_guild_id)
                lucro_cent = int(gcfg_lucro.get("lucro_centavos_sala", 0) or 0)
                if lucro_cent > 0:
                    ganho = round(p["quantia"] * (lucro_cent / 100.0), 4)
                    total_atual = float(gcfg_lucro.get("lucro_total", 0.0) or 0.0)
                    contadas_atual = int(gcfg_lucro.get("lucro_salas_contadas", 0) or 0)
                    _gcs3(pedido_guild_id, {
                        "lucro_total": round(total_atual + ganho, 4),
                        "lucro_salas_contadas": contadas_atual + p["quantia"],
                    })
                    import logging as _lg_lucro
                    _lg_lucro.getLogger("salasff.lucroorg").info(
                        f"[lucro-org-pessoal] guild={pedido_guild_id} user={uid} +R${ganho:.2f} ({p['quantia']} salas × {lucro_cent}c)"
                    )
        except Exception as _ex_lucro:
            import logging as _lg_lucro
            _lg_lucro.getLogger("salasff.lucroorg").warning(f"[lucro-org-pessoal] erro: {_ex_lucro}")

        # ── INTEGRAÇÃO ORG: registrar venda da org de revenda (compra pessoal dentro de org) ──
        try:
            pedido_guild_id = p.get("guild_id")
            if pedido_guild_id:
                from utils.database_orgs import org_registrar_venda as _org_venda2
                await asyncio.to_thread(_org_venda2, pedido_guild_id, float(p["valor"]), int(p["quantia"]))
        except Exception as _ex_org2:
            import logging as _lg_org2
            _lg_org2.getLogger("salasff.org").warning(f"[org-venda-pessoal] erro: {_ex_org2}")

        await self._backup("pós-compra")

    async def _backup(self, motivo="automático"):
        canal = self.bot.get_channel(config.BACKUP_CHANNEL_ID)
        if not canal: return
        import os, json as _json
        base = os.path.join(os.path.dirname(__file__), "..")

        def _ler():
            result = []
            for nome, label in [("keys.json","🗝️ Keys"),("pedidos_pix.json","💳 Pedidos"),("salas.json","🎮 Salas")]:
                path = os.path.join(base, nome)
                if not os.path.exists(path): continue
                try:
                    tam = os.path.getsize(path)
                    with open(path,"r",encoding="utf-8") as f: data = _json.load(f)
                    result.append((nome, label, len(data), tam))
                except: pass
            return result
        infos = await asyncio.get_event_loop().run_in_executor(None, _ler)
        if not infos: return
        resumo = [f"{lb}: **{q}** ({t//1024}kb)" for _,lb,q,t in infos]
        agora = datetime.now(_BR).strftime("%Y-%m-%d %H:%M")
        files = [discord.File(os.path.join(base, n), filename=n) for n, _, _, _ in infos if os.path.exists(os.path.join(base, n))]
        for tent in range(1,3):
            try:
                await canal.send(content=f"💾 **Backup {motivo}** — `{agora}`\n"+"\n".join(resumo), files=files)
                return
            except:
                if tent==1:
                    await asyncio.sleep(15)
                    files = [discord.File(os.path.join(base, n), filename=n) for n, _, _, _ in infos if os.path.exists(os.path.join(base, n))]

    @tasks.loop(minutes=30)
    async def backup_periodico(self):
        await self._backup("periódico")
        asyncio.create_task(_logs.log_snapshot_saldo())

    @backup_periodico.before_loop
    async def bb(self): await self.bot.wait_until_ready()
    @verificar_pagamentos.before_loop
    async def bv(self): await self.bot.wait_until_ready()


def _embed_painel_gratis() -> discord.Embed:
    """Embed do Painel Grátis."""
    em = discord.Embed(color=0x00FF7F)
    em.description = (
        f"-# {PE['bot']} Equipe F Applications\n\n"
        f"## {PE['swordbattle']} Salas Teste\n\n"
        f"> {PE['on']} **Está indeciso em comprar salas com nossa equipe?**\n"
        f"> {PE['stats']} **Teste agora! Receba salas grátis, ganhe bônus por compra e aproveite o melhor preço do mercado.**\n\n"
        f"{PE['click']} **Basta clicar no botão abaixo e já recebe diretamente na sua conta.**"
    )
    em.set_footer(text="F Applications • Disponibilidade por tempo limitado")
    return em


class PainelGratisView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(
        label="Quero Participar",
        emoji=PE["click"],
        style=discord.ButtonStyle.success,
        custom_id="gratis:quero_participar",
        row=0,
    )
    async def btn_quero(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from cogs.botconfig import carregar_cfg, salvar_cfg

        uid = str(inter.user.id)
        cfg = carregar_cfg()
        resgatados = cfg.get("gratis_resgatados", [])

        if uid in resgatados:
            em = discord.Embed(title=f"{PE['off']}  Resgate Bloqueado", color=config.COR_ERRO)
            em.description = (
                f"> Sua conta **já resgatou** o Painel Grátis anteriormente.\n"
                f"> Cada conta Discord pode resgatar apenas **1 vez**.\n\n"
                f"> {PE['off']} Remover o bot **não** redefine o limite.\n"
                f"> {PE['info']} Registro vinculado ao seu **ID Discord** permanentemente."
            )
            em.set_footer(text=f"ID bloqueado: {uid}")
            return await inter.followup.send(embed=em, ephemeral=True)

        # Gera link OAuth2
        client_id = str(inter.client.application_id or "1481499372750635038")
        redirect   = cfg.get("gratis_oauth2_redirect", "").strip()
        import urllib.parse
        if redirect:
            oauth_url = (
                f"https://discord.com/oauth2/authorize"
                f"?client_id={client_id}"
                f"&integration_type=1"
                f"&scope=applications.commands+identify"
                f"&response_type=code"
                f"&redirect_uri={urllib.parse.quote(redirect, safe='')}"
                f"&state={uid}_{inter.channel_id}"
            )
        else:
            oauth_url = (
                f"https://discord.com/oauth2/authorize"
                f"?client_id={client_id}"
                f"&integration_type=1"
                f"&scope=applications.commands"
            )

        qtd_gratis = int(cfg.get("salas_gratis", 10))
        em = discord.Embed(title=f"{PE['vision']}  Adicionar F Applications", color=0x5865F2)
        em.description = (
            f"{PE['bot']} Para receber as **{qtd_gratis} salas teste**, **adicione o bot à sua conta**.\n\n"
            f"> {PE['click']} Clique no botão abaixo e depois em **Autorizar**.\n"
            f"> {PE['on']} As salas serão adicionadas **automaticamente** após a autorização.\n"
            f"> {PE['off']} Este link é de **uso único** — não compartilhe."
        )
        em.set_footer(text="F Applications 2026 • Resgate único por conta")

        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(
            label="Adicionar à Minha Conta",
            emoji=PE["adduser"],
            style=discord.ButtonStyle.link,
            url=oauth_url,
            row=0,
        ))
        await inter.followup.send(embed=em, view=view, ephemeral=True)

    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.command(name="simular", description="[ADMIN] Simula compra com botão de aprovar.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.describe(quantia="Quantidade de salas")
    async def cmd_simular(self, i, quantia: app_commands.Range[int, 1, 99999]):
        if i.user.id not in config.ADMIN_IDS:
            return await i.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await i.response.defer(ephemeral=True)
        gid = str(i.guild.id) if i.guild else None
        preco = get_preco_por_sala_guild(gid)
        valor = round(quantia * preco, 2)

        em = _emb(f"{MONEY}  Simulação de Compra", 0xA855F7)
        em.description = (
            f"{DOT} **Salas:** {quantia}\n"
            f"{DOT} **Valor:** R$ {valor:.2f}\n"
            f"{DOT} **Preço/sala:** R$ {preco:.2f}\n\n"
            f"{SETTINGS} Clique em **Aprovar** para simular o pagamento\n"
            f"e creditar **{quantia} salas** no seu saldo."
        )
        await i.followup.send(embed=em, view=SimularAprovarView(str(i.user.id), i.user.display_name, quantia, valor), ephemeral=True)


class SimularAprovarView(discord.ui.View):
    def __init__(self, uid, nome, qtd, valor):
        super().__init__(timeout=300)
        self.uid = uid
        self.nome = nome
        self.qtd = qtd
        self.valor = valor

    @discord.ui.button(label="Aprovar Pagamento", emoji=PE["on"], style=discord.ButtonStyle.success, row=0)
    async def aprovar(self, i, btn):
        if str(i.user.id) not in config.ADMIN_IDS and i.user.id not in config.ADMIN_IDS:
            return await i.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await i.response.defer(ephemeral=True)

        # Credita saldo
        from utils.database import adicionar_saldo_usuario
        code = await asyncio.to_thread(adicionar_saldo_usuario, self.uid, self.nome, self.qtd)

        # Dá cargos
        try:
            from cogs.botconfig import dar_cargo_comprador
            gids_tentar = list(config.GUILD_IDS)
            # Tenta também todas as guilds onde o user é membro
            try:
                for guild_obj in i.client.guilds:
                    if guild_obj.id in gids_tentar:
                        continue
                    if guild_obj.get_member(int(self.uid)):
                        gids_tentar.append(guild_obj.id)
            except Exception:
                pass
            for gid in gids_tentar:
                try:
                    await dar_cargo_comprador(i.client, gid, int(self.uid), self.qtd)
                except Exception:
                    pass
            # Cargo de saldo
            try:
                from cogs.botconfig import aplicar_cargo_saldo
                await aplicar_cargo_saldo(i.client, int(self.uid))
            except Exception:
                pass
        except: pass

        # Desabilita botões
        for item in self.children:
            item.disabled = True
        btn.label = "Aprovado!"

        em_ok = _ok("Pagamento Aprovado!")
        em_ok.description = (
            f"{CART} **{self.qtd} sala(s)** creditadas para **{self.nome}**\n"
            f"{DOT} Valor: R$ {self.valor:.2f}\n"
            f"{GIFT} Key: `{code}`"
        )
        await i.edit_original_response(embed=em_ok, view=self)

        # Log
        asyncio.create_task(_logs.log_compra_confirmada(self.uid, self.nome, self.qtd, self.valor, "SIMULACAO", code, i.guild.name if i.guild else None))

    @discord.ui.button(label="Cancelar", emoji=PE["off"], style=discord.ButtonStyle.danger, row=0)
    async def cancelar(self, i, btn):
        for item in self.children:
            item.disabled = True
        await i.response.edit_message(embed=_err("Simulação cancelada."), view=self)


async def setup(bot):
    await bot.add_cog(ComprarCog(bot))
    # Persistent views — botões continuam funcionando após reiniciar o bot
    bot.add_view(PainelComprarView())
    bot.add_view(PainelGratisView())
    from utils.logs import LogCompraView
    bot.add_view(LogCompraView())
