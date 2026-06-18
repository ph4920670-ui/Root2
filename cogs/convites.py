# cogs/convites.py — Sistema de Convites com Aprovação (Components V2)
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

import config
from utils.database import (
    convite_link_get, convite_link_set,
    convidados_resolve_inviter, convidado_registrar, convidado_marcar_saiu,
    convidados_validos_24h, convidados_marcar_aprovados,
    convites_canal_aprovacao_get,
    adicionar_saldo_usuario,
)
from utils.emojis import PE

_log = logging.getLogger("salasff.convites")

SALAS_POR_CONVITE = 50
BRASILIA = timezone(timedelta(hours=-3))


def _em(name: str) -> dict | None:
    """Pega emoji do PE no formato dict pra Components V2."""
    raw = PE.get(name)
    if not raw:
        return None
    if isinstance(raw, str) and raw.startswith("<"):
        try:
            inner = raw.strip("<>")
            animated = inner.startswith("a:")
            if animated:
                inner = inner[2:]
            elif inner.startswith(":"):
                inner = inner[1:]
            n, eid = inner.split(":", 1) if ":" in inner else (None, None)
            if eid:
                return {"id": eid, "name": n, "animated": animated}
        except Exception:
            pass
    return None


def _em_str(name: str) -> str:
    return PE.get(name) or ""


# ─── Cache de invites ───
class InviteCache:
    def __init__(self):
        self.cache: dict[int, dict[str, int]] = {}

    async def init_guild(self, guild: discord.Guild):
        try:
            invites = await guild.invites()
            self.cache[guild.id] = {inv.code: inv.uses for inv in invites}
        except discord.Forbidden:
            _log.warning(f"[invitecache] sem permissão em {guild.id}")
            self.cache[guild.id] = {}
        except Exception as ex:
            _log.warning(f"[invitecache] erro init {guild.id}: {ex}")
            self.cache[guild.id] = {}

    async def detect_used(self, guild: discord.Guild) -> str | None:
        try:
            atuais = await guild.invites()
        except Exception as ex:
            _log.warning(f"[invitecache] erro fetch {guild.id}: {ex}")
            return None
        antigo = self.cache.get(guild.id, {})
        usado = None
        for inv in atuais:
            antes = antigo.get(inv.code, 0)
            if inv.uses > antes:
                usado = inv.code
                break
        self.cache[guild.id] = {inv.code: inv.uses for inv in atuais}
        return usado


_invite_cache = InviteCache()


# ─── Components V2 builders ───
def _build_v2_painel_inicial(uid: int, gid: int) -> dict:
    """Painel inicial — antes do user clicar em Gerar Convite."""
    cor = 0xFEE75C
    info_emj     = _em_str("info")
    megafone_emj = _em_str("megafone") or "🎉"

    btn_gerar = {
        "id": 10, "type": 2, "style": 3,
        "label": "Gerar Convite",
        "custom_id": f"conv:gerar:{gid}:public",
    }
    e_gift = _em("gift")
    if e_gift:
        btn_gerar["emoji"] = e_gift

    components = [{
        "id": 1, "type": 17, "accent_color": cor,
        "components": [
            {
                "id": 2, "type": 10,
                "content": (
                    f"## {megafone_emj}  Convide um Amigo\n"
                    f"-# Convide um amigo **[MEDIADOR] / ADM** e ganhe "
                    f"**{SALAS_POR_CONVITE} salas grátis** por convite."
                ),
            },
            {"id": 3, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 4, "type": 10,
                "content": (
                    f"{info_emj}  **Como funciona?**\n"
                    f"-# Clique em **Gerar Convite** abaixo. O bot cria seu link único.\n"
                    f"-# Após convidar amigos (das últimas 24h), clique em "
                    f"**Pedir Aprovação** pra um admin liberar suas salas."
                ),
            },
            {"id": 5, "type": 14, "divider": True, "spacing": 1},
            {"id": 6, "type": 1, "components": [btn_gerar]},
        ],
    }]
    # Painel PÚBLICO — sem flag 64 (ephemeral). Só flag 32768 (V2).
    return {"flags": 32768, "components": components}


def _build_v2_painel_gerado(uid: str, gid: str, codigo: str, n_convidados: int) -> dict:
    """Painel após gerar o link — com link e contagem."""
    cor = 0x57F287
    gift_emj     = _em_str("gift")
    info_emj     = _em_str("info")
    megafone_emj = _em_str("megafone") or "🎉"

    salas = n_convidados * SALAS_POR_CONVITE

    btn_pedir = {
        "id": 11, "type": 2, "style": 3,
        "label": "Pedir Aprovação",
        "custom_id": f"conv:pedir:{gid}:{uid}",
    }
    e_on = _em("on")
    if e_on:
        btn_pedir["emoji"] = e_on

    btn_link = {
        "id": 12, "type": 2, "style": 5,
        "label": "Abrir Convite",
        "url": f"https://discord.gg/{codigo}",
    }

    components = [{
        "id": 1, "type": 17, "accent_color": cor,
        "components": [
            {
                "id": 2, "type": 10,
                "content": (
                    f"## {megafone_emj}  Convide um Amigo\n"
                    f"-# Convide um amigo **[MEDIADOR] / ADM** e ganhe "
                    f"**{SALAS_POR_CONVITE} salas grátis** por convite."
                ),
            },
            {"id": 3, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 4, "type": 10,
                "content": f"{info_emj}  **Seu Link de Convite**\n```\nhttps://discord.gg/{codigo}\n```",
            },
            {"id": 5, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 6, "type": 10,
                "content": (
                    f"{gift_emj}  **Convidados (24h):** `{n_convidados}`\n"
                    f"{gift_emj}  **Salas a Receber:** `{salas}`"
                ),
            },
            {"id": 7, "type": 14, "divider": True, "spacing": 1},
            {"id": 8, "type": 1, "components": [btn_pedir, btn_link]},
        ],
    }]
    return {"flags": 64 | 32768, "components": components}


# ─── PATCH/POST helpers ───
async def _respond_v2_initial(inter_id: int, inter_token: str, payload: dict) -> bool:
    url = f"https://discord.com/api/v10/interactions/{inter_id}/{inter_token}/callback"
    body = {"type": 4, "data": payload}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[v2 respond conv] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 respond conv] {ex}")
        return False


async def _update_v2_message(inter_id: int, inter_token: str, payload: dict) -> bool:
    """type 7 — UPDATE_MESSAGE."""
    url = f"https://discord.com/api/v10/interactions/{inter_id}/{inter_token}/callback"
    body = {"type": 7, "data": payload}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[v2 update conv] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 update conv] {ex}")
        return False


async def _patch_v2(app_id: int, itoken: str, payload: dict) -> bool:
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{itoken}/messages/@original"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.patch(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[v2 patch conv] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[v2 patch conv] {ex}")
        return False


# ─── Painel inicial (chamado pelo /painelglobal) ───
async def abrir_painel_convites(inter: discord.Interaction):
    if not inter.guild:
        return await inter.response.send_message(
            "❌ Use esse comando dentro de um servidor.", ephemeral=True
        )

    payload = _build_v2_painel_inicial(inter.user.id, inter.guild.id)

    if inter.response.is_done():
        await _patch_v2(inter.application_id, inter.token, payload)
    else:
        ok = await _respond_v2_initial(inter.id, inter.token, payload)
        if not ok:
            try:
                if not inter.response.is_done():
                    await inter.response.defer(ephemeral=True)
                await inter.followup.send("❌ Erro ao abrir painel.", ephemeral=True)
            except Exception:
                pass


# ─── Cog ───
class ConvitesCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await _invite_cache.init_guild(guild)
        _log.info(f"[convites] cache inicializado em {len(self.bot.guilds)} guilds")

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild:
            cache = _invite_cache.cache.setdefault(invite.guild.id, {})
            cache[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild:
            cache = _invite_cache.cache.get(invite.guild.id, {})
            cache.pop(invite.code, None)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        codigo = await _invite_cache.detect_used(member.guild)
        if not codigo:
            return
        inviter_id = await asyncio.to_thread(
            convidados_resolve_inviter, codigo, str(member.guild.id)
        )
        if not inviter_id:
            return

        # Checa se o user JÁ entrou antes (re-entrada bloqueia)
        try:
            from utils.database import _col_convites
            doc = await asyncio.to_thread(
                lambda: _col_convites().find_one(
                    {"_id": f"convidado:{member.guild.id}:{member.id}"}
                )
            )
            ja_entrou_antes = bool(doc)
        except Exception:
            ja_entrou_antes = False

        await asyncio.to_thread(
            convidado_registrar,
            str(member.id), str(member.guild.id),
            inviter_id, codigo, True, "",
        )

        if ja_entrou_antes:
            _log.info(f"[convites] ⚠️ {member.id} RE-ENTROU via {inviter_id} — bloqueado")
        else:
            _log.info(f"[convites] ✅ {member.id} entrou via {inviter_id} (código {codigo})")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        await asyncio.to_thread(
            convidado_marcar_saiu, str(member.id), str(member.guild.id)
        )

    @commands.Cog.listener()
    async def on_interaction(self, inter: discord.Interaction):
        if inter.type != discord.InteractionType.component:
            return
        cid = inter.data.get("custom_id", "") if inter.data else ""
        if not cid.startswith("conv:"):
            return

        partes = cid.split(":")
        if len(partes) < 4:
            return
        acao = partes[1]

        if acao in ("aprovar", "recusar"):
            return await self._aprovacao_handler(inter, acao, partes)

        gid = partes[2]
        uid = partes[3]

        # Botão "Gerar Convite" do painel público: qualquer um pode clicar.
        # Cada user recebe SEU painel ephemeral com SEU link.
        if acao == "gerar":
            await self._gerar_convite(inter, gid, str(inter.user.id))
            return

        # Botão "Pedir Aprovação" é do painel ephemeral pessoal — só o dono.
        if str(inter.user.id) != uid:
            try:
                await inter.response.send_message(
                    "❌ Esse painel é de outro usuário.", ephemeral=True
                )
            except Exception:
                pass
            return

        if acao == "pedir":
            await self._pedir_aprovacao(inter, gid, uid)

    async def _gerar_convite(self, inter: discord.Interaction, gid: str, uid: str):
        codigo = await asyncio.to_thread(convite_link_get, uid, gid)

        guild = inter.client.get_guild(int(gid))
        if not guild:
            try:
                await inter.response.send_message("❌ Servidor não encontrado.", ephemeral=True)
            except Exception:
                pass
            return

        if codigo:
            try:
                invites = await guild.invites()
                existe = any(i.code == codigo for i in invites)
            except Exception:
                existe = True
            if not existe:
                codigo = None

        if not codigo:
            canal_invite = guild.system_channel or inter.channel
            try:
                invite = await canal_invite.create_invite(
                    max_age=0, max_uses=0, unique=True,
                    reason=f"Convite pessoal de {inter.user}",
                )
                codigo = invite.code
                await asyncio.to_thread(convite_link_set, uid, gid, codigo)
            except discord.Forbidden:
                try:
                    await inter.response.send_message(
                        "❌ O bot não tem permissão pra criar convites.",
                        ephemeral=True,
                    )
                except Exception:
                    pass
                return
            except Exception as ex:
                _log.error(f"[gerar_convite] {ex}")
                try:
                    await inter.response.send_message(
                        f"❌ Erro ao criar convite: {ex}", ephemeral=True
                    )
                except Exception:
                    pass
                return

        convidados = await asyncio.to_thread(convidados_validos_24h, uid, gid)
        n = len(convidados)

        # Painel ephemeral pessoal (não edita o público)
        payload = _build_v2_painel_gerado(uid, gid, codigo, n)
        await _respond_v2_initial(inter.id, inter.token, payload)

    async def _pedir_aprovacao(self, inter: discord.Interaction, gid: str, uid: str):
        await inter.response.defer(ephemeral=True)

        canal_id = await asyncio.to_thread(convites_canal_aprovacao_get)
        if not canal_id:
            return await inter.followup.send(
                "❌ Sistema de aprovação não foi configurado pelo dono do bot.",
                ephemeral=True,
            )
        canal = inter.client.get_channel(int(canal_id))
        if not canal:
            try:
                canal = await inter.client.fetch_channel(int(canal_id))
            except Exception:
                return await inter.followup.send(
                    "❌ Canal de aprovação não encontrado.", ephemeral=True
                )

        convidados = await asyncio.to_thread(convidados_validos_24h, uid, gid)
        if not convidados:
            return await inter.followup.send(
                "❌ Você ainda não tem convidados nas últimas 24h.\n"
                "Compartilhe seu link e tente de novo!",
                ephemeral=True,
            )

        em = discord.Embed(
            title=f"{_em_str('megafone') or '🎉'}  Pedido de Aprovação — Convites",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc),
        )
        em.add_field(name="👤  Inviter", value=f"<@{uid}> (`{uid}`)", inline=False)
        em.add_field(name="👥  Convidados (24h)", value=f"**{len(convidados)}**", inline=True)
        em.add_field(
            name="🎁  Salas a Liberar",
            value=f"**{len(convidados) * SALAS_POR_CONVITE}**",
            inline=True,
        )
        nomes = [f"• <@{c['user_id']}> — `{c['user_id']}`" for c in convidados]
        valor = "\n".join(nomes)
        if len(valor) > 1000:
            valor = "\n".join(nomes[:20]) + f"\n*... e mais {len(nomes) - 20}*"
        em.add_field(name="📋  Lista de Convidados", value=valor, inline=False)
        em.set_footer(text=f"Inviter: {uid}  •  Guild: {gid}")

        try:
            await canal.send(embed=em, view=AprovacaoConvitesView(uid, gid))
        except discord.Forbidden:
            return await inter.followup.send(
                "❌ Bot sem permissão pra enviar no canal de aprovação.",
                ephemeral=True,
            )

        await inter.followup.send(
            "✅ **Pedido enviado!** Um admin vai revisar em breve.",
            ephemeral=True,
        )

    async def _aprovacao_handler(self, inter: discord.Interaction, acao: str, partes: list):
        if len(partes) < 4:
            return
        gid = partes[2]
        inviter_id = partes[3]

        if not (inter.guild and inter.user.guild_permissions.manage_guild):
            try:
                await inter.response.send_message("❌ Sem permissão.", ephemeral=True)
            except Exception:
                pass
            return

        # Pré-validação SEM defer (pra poder abrir modal no aprovar)
        convidados = await asyncio.to_thread(convidados_validos_24h, inviter_id, gid)
        if not convidados:
            try:
                await inter.response.send_message(
                    "❌ Esse pedido já foi processado ou expirou (24h).",
                    ephemeral=True,
                )
            except Exception:
                pass
            return

        n = len(convidados)
        salas = n * SALAS_POR_CONVITE

        try:
            inviter = await inter.client.fetch_user(int(inviter_id))
            nome = inviter.name
        except Exception:
            nome = f"User {inviter_id}"

        if acao == "aprovar":
            # Abre modal pedindo quantos foram válidos
            try:
                await inter.response.send_modal(
                    AprovarQtdModal(inviter_id, gid, total=n)
                )
            except Exception as ex:
                _log.error(f"[aprovar modal] {ex}")
            return

        elif acao == "recusar":
            await inter.response.defer()
            ids = [c["user_id"] for c in convidados]
            await asyncio.to_thread(convidados_marcar_aprovados, inviter_id, gid, ids)

            em = inter.message.embeds[0] if inter.message.embeds else discord.Embed()
            em.color = 0xFF4757
            em.title = "❌  Pedido Recusado"
            em.add_field(name="🔴  Recusado por", value=inter.user.mention, inline=False)
            view_off = AprovacaoConvitesView(inviter_id, gid)
            for item in view_off.children:
                item.disabled = True
            try:
                await inter.message.edit(embed=em, view=view_off)
            except Exception:
                pass
            try:
                await inter.followup.send("Recusado.", ephemeral=True)
            except Exception:
                pass

            # DM de recusa
            try:
                inviter_obj = await inter.client.fetch_user(int(inviter_id))
                info_emj     = _em_str("info")
                off_emj      = _em_str("off") or "❌"
                megafone_emj = _em_str("megafone") or "🎉"

                payload_dm = {
                    "flags": 32768,
                    "components": [{
                        "id": 1, "type": 17, "accent_color": 0xFF4757,
                        "components": [
                            {
                                "id": 2, "type": 10,
                                "content": (
                                    f"## {off_emj}  Pedido de Convites Recusado\n"
                                    f"-# Seu pedido foi recusado por um administrador."
                                ),
                            },
                            {"id": 3, "type": 14, "divider": True, "spacing": 1},
                            {
                                "id": 4, "type": 10,
                                "content": (
                                    f"{info_emj}  **Convidados no pedido:** `{n}`\n"
                                    f"-# Os convites desse pedido **não dão direito** a salas grátis.\n"
                                    f"-# Possíveis motivos: convidados saíram do servidor, "
                                    f"contas suspeitas, ou alta atividade incomum."
                                ),
                            },
                            {"id": 5, "type": 14, "divider": True, "spacing": 1},
                            {
                                "id": 6, "type": 10,
                                "content": (
                                    f"-# {megafone_emj}  Continue convidando amigos reais "
                                    f"pra ter seus próximos pedidos aprovados!"
                                ),
                            },
                        ],
                    }],
                }
                dm = await inviter_obj.create_dm()
                url = f"https://discord.com/api/v10/channels/{dm.id}/messages"
                headers = {
                    "Authorization": f"Bot {inter.client.http.token}",
                    "Content-Type": "application/json",
                }
                async with aiohttp.ClientSession() as s:
                    async with s.post(url, json=payload_dm, headers=headers,
                                      timeout=aiohttp.ClientTimeout(total=10)) as r:
                        if r.status not in (200, 201):
                            em_dm = discord.Embed(
                                title="❌  Pedido de Convites Recusado",
                                color=0xFF4757,
                                description=(
                                    f"Seu pedido com **{n}** convidados foi recusado.\n"
                                    f"Continue convidando amigos pra ter seus próximos pedidos aprovados!"
                                ),
                            )
                            await inviter_obj.send(embed=em_dm)
            except Exception:
                pass


class AprovarQtdModal(discord.ui.Modal):
    """Modal pedindo quantos convidados foram válidos."""
    def __init__(self, inviter_id: str, guild_id: str, total: int):
        super().__init__(title=f"✅ Aprovar Convites ({total} no total)")
        self.inviter_id = inviter_id
        self.guild_id = guild_id
        self.total = total
        self.qtd_input = discord.ui.TextInput(
            label=f"Quantos foram válidos? (0 a {total})",
            placeholder=f"Ex: {total}",
            default=str(total),
            min_length=1, max_length=4,
            required=True,
        )
        self.add_item(self.qtd_input)

    async def on_submit(self, inter: discord.Interaction):
        # Valida número
        try:
            n_validos = int(self.qtd_input.value.strip())
        except ValueError:
            return await inter.response.send_message(
                "❌ Digite um número válido.", ephemeral=True
            )

        if n_validos < 0 or n_validos > self.total:
            return await inter.response.send_message(
                f"❌ Deve ser entre 0 e {self.total}.", ephemeral=True
            )

        await inter.response.defer()

        # Recarrega convidados
        convidados = await asyncio.to_thread(
            convidados_validos_24h, self.inviter_id, self.guild_id
        )
        if not convidados:
            return await inter.followup.send(
                "❌ Esse pedido já foi processado.", ephemeral=True
            )

        salas = n_validos * SALAS_POR_CONVITE
        total_pedido = len(convidados)

        # Pega nome do inviter
        try:
            inviter = await inter.client.fetch_user(int(self.inviter_id))
            nome = inviter.name
        except Exception:
            nome = f"User {self.inviter_id}"

        # Adiciona saldo (só se tiver válidos)
        if n_validos > 0:
            try:
                await asyncio.to_thread(
                    adicionar_saldo_usuario, self.inviter_id, nome, salas, "indicacao"
                )
            except Exception as ex:
                _log.error(f"[aprovar saldo] {ex}")
                return await inter.followup.send(f"❌ Erro: {ex}", ephemeral=True)

        # RESETA TODOS os convidados desse pedido (válidos + inválidos)
        # — marca todos como aprovados pra não voltarem em futuros pedidos.
        ids = [c["user_id"] for c in convidados]
        await asyncio.to_thread(
            convidados_marcar_aprovados, self.inviter_id, self.guild_id, ids
        )

        # Edita mensagem de aprovação no canal
        try:
            em = inter.message.embeds[0] if inter.message.embeds else discord.Embed()
            em.color = 0x57F287 if n_validos > 0 else 0xFEE75C
            em.title = "✅  Convites Aprovados" if n_validos > 0 else "⚠️  Pedido Processado"
            em.add_field(
                name="🟢  Aprovado por",
                value=(
                    f"{inter.user.mention}\n"
                    f"`{n_validos}/{total_pedido}` válidos\n"
                    f"**+{salas} salas**"
                ),
                inline=False,
            )
            view_off = AprovacaoConvitesView(self.inviter_id, self.guild_id)
            for item in view_off.children:
                item.disabled = True
            await inter.message.edit(embed=em, view=view_off)
        except Exception:
            pass

        try:
            await inter.followup.send(
                f"✅ Aprovado! `{n_validos}/{total_pedido}` válidos → "
                f"**+{salas} salas** pra <@{self.inviter_id}>.",
                ephemeral=True,
            )
        except Exception:
            pass

        # DM
        try:
            inviter_obj = await inter.client.fetch_user(int(self.inviter_id))
            gift_emj     = _em_str("gift")
            on_emj       = _em_str("on") or "✅"
            megafone_emj = _em_str("megafone") or "🎉"

            invalidos = total_pedido - n_validos
            extra = ""
            if invalidos > 0:
                extra = f"\n-# ⚠️ `{invalidos}` convidado(s) foram considerados inválidos."

            payload_dm = {
                "flags": 32768,
                "components": [{
                    "id": 1, "type": 17,
                    "accent_color": 0x57F287 if n_validos > 0 else 0xFEE75C,
                    "components": [
                        {
                            "id": 2, "type": 10,
                            "content": (
                                f"## {on_emj}  Convites Aprovados!\n"
                                f"-# Seu pedido foi processado por um administrador."
                            ),
                        },
                        {"id": 3, "type": 14, "divider": True, "spacing": 1},
                        {
                            "id": 4, "type": 10,
                            "content": (
                                f"{gift_emj}  **Convidados válidos:** `{n_validos}/{total_pedido}`\n"
                                f"{gift_emj}  **Salas recebidas:** `{salas}`"
                                f"{extra}"
                            ),
                        },
                        {"id": 5, "type": 14, "divider": True, "spacing": 1},
                        {
                            "id": 6, "type": 10,
                            "content": (
                                f"-# {megafone_emj}  Continue convidando amigos pra ganhar mais salas!"
                            ),
                        },
                    ],
                }],
            }
            dm = await inviter_obj.create_dm()
            url = f"https://discord.com/api/v10/channels/{dm.id}/messages"
            headers = {
                "Authorization": f"Bot {inter.client.http.token}",
                "Content-Type": "application/json",
            }
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    url, json=payload_dm, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    if r.status not in (200, 201):
                        em_dm = discord.Embed(
                            title="🎉  Convites Aprovados!",
                            color=0x57F287,
                            description=(
                                f"`{n_validos}/{total_pedido}` convidados válidos.\n"
                                f"🎁 Você recebeu **{salas} salas grátis**."
                            ),
                        )
                        await inviter_obj.send(embed=em_dm)
        except Exception:
            pass


class AprovacaoConvitesView(discord.ui.View):
    """View persistente — botões de aprovação."""
    def __init__(self, inviter_id: str = "default", guild_id: str = "default"):
        super().__init__(timeout=None)
        btn_aprovar = discord.ui.Button(
            label="Aprovar", emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"conv:aprovar:{guild_id}:{inviter_id}",
        )
        btn_recusar = discord.ui.Button(
            label="Recusar", emoji="❌",
            style=discord.ButtonStyle.danger,
            custom_id=f"conv:recusar:{guild_id}:{inviter_id}",
        )
        btn_aprovar.callback = self._noop
        btn_recusar.callback = self._noop
        self.add_item(btn_aprovar)
        self.add_item(btn_recusar)

    async def _noop(self, inter: discord.Interaction):
        pass


async def setup(bot):
    await bot.add_cog(ConvitesCog(bot))
    bot.add_view(AprovacaoConvitesView())
