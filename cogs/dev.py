# cogs/dev.py — Central de Configurações do Servidor (/dev)

import asyncio, io, base64
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime
from zoneinfo import ZoneInfo

import config
from utils import logs as _logs
from utils.emojis import PE, BOT, STATS, SETTINGS, INFO, DOT, ON, OFF, GIFT, CART, TOP, MOBILE, MONEY, ROLES, DOLLAR, TRASH, ADDUSER, CLOUD, PIX, CLOCKCHECK, URL, SWORD, CHANNEL, RELOADING, BAN, RAGE
from utils.pix import criar_cobranca_pix, get_preco_por_sala
from utils.database import (
    guild_config_get, guild_config_set, guild_adicionar_saldo,
    guild_consumir_sala, guild_reverter_sala, guild_set_cargo_sala,
    stats_guild,
)

import logging
_log = logging.getLogger("salasff.dev")
_BR = ZoneInfo("America/Sao_Paulo")

def _ts():
    return datetime.now(_BR)

def _emb(t="", c=0x5865F2, d=""):
    em = discord.Embed(title=t, color=c)
    if d: em.description = d
    return em

def _ok(t, d=""): return _emb(f"{ON}  {t}", config.COR_SUCESSO, d)
def _err(t, d=""): return _emb(f"❌  {t}", config.COR_ERRO, d)
def _info(t, d=""): return _emb(f"{INFO}  {t}", config.COR_INFO, d)


# ═══════════════════════════════════════════
#  Embed principal do /dev
# ═══════════════════════════════════════════

def _dev_embed(guild, cfg):
    saldo = cfg.get("saldo", 0)
    cargo_id = cfg.get("cargo_sala_id")
    cargo_txt = f"<@&{cargo_id}>" if cargo_id else f"{OFF} *Não definido*"

    em = discord.Embed(
        title=f"{SETTINGS}  Central de Configurações — {guild.name}",
        color=0x5865F2,
        
    )

    if guild.icon:
        em.set_thumbnail(url=guild.icon.url)

    em.add_field(name=f"{STATS}  Salas", value=f"> **{saldo}**", inline=True)
    em.add_field(name=f"{ROLES}  Cargo Sala", value=f"> {cargo_txt}", inline=True)

    return em


# ═══════════════════════════════════════════
#  Select Menu principal
# ═══════════════════════════════════════════

class DevSelectView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        self.guild = guild

    @discord.ui.select(
        placeholder="Selecione o que deseja configurar",
        options=[
            discord.SelectOption(
                label="Comprar Salas",
                description="Adquira novas salas para o servidor",
                emoji=discord.PartialEmoji(name="foguete", id=1388278180552376431),
                value="comprar",
            ),
            discord.SelectOption(
                label="Cargo Sala",
                description="Gerencie cargos que podem criar salas",
                emoji=discord.PartialEmoji(name="user_branco", id=1438851366314311730),
                value="cargo",
            ),
            discord.SelectOption(
                label="Usuário Sala",
                description="Adicione ou remova salas de um usuário",
                emoji=PE["adduser"],
                value="usuario_sala",
            ),
            discord.SelectOption(
                label="Perfil do Bot",
                description="Altere o nick e a foto do bot neste servidor",
                emoji=PE["bot"],
                value="perfil_bot",
            ),


        ],
    )
    async def select_callback(self, inter, sel):
        choice = sel.values[0]

        if choice == "comprar":
            await self._comprar_salas(inter)
        elif choice == "cargo":
            await self._cargo_sala(inter)
        elif choice == "usuario_sala":
            await self._usuario_sala(inter)

        elif choice == "valor_sala":
            from utils.pix import get_preco_por_sala
            preco_atual = get_preco_por_sala()
            em = discord.Embed(
                title=f"{DOLLAR}  Valor da Sala",
                color=0x5865F2,
                
            )
            em.add_field(name=f"{DOLLAR}  Preço Atual", value=f"> **{round(preco_atual*100)}** centavos — R$ {preco_atual:.2f}/sala", inline=False)
            em.add_field(name=f"{INFO}  Como alterar", value=f"> {DOT} Clique em **Alterar Preço** e digite os centavos\n> {DOT} Ex: `9` = R$ 0,09 por sala", inline=False)
            await inter.response.send_message(embed=em, view=ValorSalaMenuView(), ephemeral=True)
        elif choice == "perfil_bot":
            await self._perfil_bot(inter)


    async def _comprar_salas(self, inter):
        preco = get_preco_por_sala()
        gid = str(self.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)
        saldo = cfg.get("saldo", 0)

        em = discord.Embed(
            title=f"{CART}  Comprar Salas — {self.guild.name}",
            color=0x1abc9c,
            
        )

        em.add_field(name=f"{MONEY}  Preço/Sala", value=f"> **R$ {preco:.2f}**", inline=True)
        em.add_field(name=f"{STATS}  Saldo Atual", value=f"> **{saldo}**", inline=True)
        em.add_field(name=f"{ON}  Pagamento", value=f"> PIX Instantâneo", inline=True)
        em.add_field(
            name=f"{INFO}  Como funciona",
            value=(
                f"> {DOT} Clique em **Comprar Salas** e informe a quantidade\n"
                f"> {DOT} Pague o PIX gerado\n"
                f"> {DOT} Salas creditadas automaticamente ao servidor"
            ),
            inline=False,
        )

        await inter.response.send_message(embed=em, view=ComprarServidorBtnView(self.guild), ephemeral=True)

    async def _cargo_sala(self, inter):
        gid = str(self.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)
        cargo_id = cfg.get("cargo_sala_id")
        saldo = cfg.get("saldo", 0)
        cargo_off = cfg.get("cargo_off", False)

        if cargo_off:
            cargo_txt = f"{OFF} **Desabilitado** — nenhum cargo pode criar salas"
            status_txt = f"{OFF} **OFF** — criação por cargo desabilitada"
        elif cargo_id:
            cargo_txt = f"{ON} <@&{cargo_id}>"
            status_txt = f"{ON} **ON** — cargo ativo"
        else:
            cargo_txt = f"{OFF} *Nenhum cargo definido*"
            status_txt = f"{OFF} *Sem cargo*"

        em = discord.Embed(
            title=f"{ROLES}  Cargo Sala — {self.guild.name}",
            color=0xFF4444 if cargo_off else 0x5865F2,
            
        )
        em.add_field(name=f"{ROLES}  Cargo Atual", value=f"> {cargo_txt}", inline=True)
        em.add_field(name=f"{INFO}  Status", value=f"> {status_txt}", inline=True)
        em.add_field(name=f"{STATS}  Saldo", value=f"> **{saldo} salas**", inline=True)

        if cargo_off:
            em.add_field(
                name=f"{RAGE}  Cargo Desabilitado",
                value=(
                    f"> {DOT} Nenhum cargo pode criar salas pelo servidor\n"
                    f"> {DOT} O saldo do servidor está mantido\n"
                    f"> {DOT} Clique em **Cargo OFF** novamente para reativar"
                ),
                inline=False,
            )
        else:
            em.add_field(
                name=f"{SETTINGS}  Como funciona",
                value=(
                    f"> {DOT} **Adicionar Cargo** — define qual cargo pode criar salas\n"
                    f"> {DOT} **Remover Cargo** — remove o cargo atual\n"
                    f"> {DOT} **Cargo OFF** — desabilita criação por cargo temporariamente\n"
                    f"> {DOT} Quando OFF, nenhum cargo poderá criar salas até ser habilitado novamente"
                ),
                inline=False,
            )

        await inter.response.send_message(embed=em, view=CargoSalaView(self.guild), ephemeral=True)

    async def _usuario_sala(self, inter):
        gid = str(self.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)
        saldo = cfg.get("saldo", 0)

        em = discord.Embed(
            title=f"{ADDUSER}  Usuário Sala — {self.guild.name}",
            color=0x5865F2,
            
        )
        em.add_field(name=f"{STATS}  Saldo do Servidor", value=f"> **{saldo} salas**", inline=False)
        em.add_field(
            name=f"{SETTINGS}  Opções",
            value=(
                f"> {DOT} **Adicionar Salas** — adiciona salas do servidor a um usuário\n"
                f"> {DOT} **Remover Salas** — remove salas de um usuário\n\n"
                f"> {INFO} As salas são descontadas/devolvidas do saldo do servidor"
            ),
            inline=False,
        )
        await inter.response.send_message(embed=em, view=UsuarioSalaView(self.guild), ephemeral=True)

    async def _logs_servidor(self, inter):
        await inter.response.defer(ephemeral=True)
        gid = str(self.guild.id)
        s = await asyncio.to_thread(stats_guild, gid)

        medalhas = [TOP, "🥈", "🥉"]
        top_txt = ""
        for idx, c in enumerate(s["top3"]):
            top_txt += f"> {medalhas[idx]}  **{c['nome']}** — {c['total']} sala{'s' if c['total'] != 1 else ''}\n"
        if not top_txt:
            top_txt = f"> {OFF} *Nenhuma sala criada ainda*"

        em = discord.Embed(
            title=f"{STATS}  Logs — {self.guild.name}",
            color=0x5865F2,
            
        )

        em.add_field(name=f"{ON}  Hoje",    value=f"> **{s['hoje']}**",   inline=True)
        em.add_field(name=f"{OFF}  Ontem",   value=f"> **{s['ontem']}**",  inline=True)
        em.add_field(name=f"{STATS}  3 dias", value=f"> **{s['3dias']}**",  inline=True)
        em.add_field(name=f"{STATS}  Semana", value=f"> **{s['semana']}**", inline=True)
        em.add_field(name=f"{STATS}  Mês",    value=f"> **{s['mes']}**",    inline=True)
        em.add_field(name=f"{STATS}  Total",  value=f"> **{s['total']}**",  inline=True)
        em.add_field(name=f"{TOP}  Top Criadores", value=top_txt.strip(), inline=False)

        await inter.followup.send(embed=em, ephemeral=True)

    async def _perfil_bot(self, inter):
        guild = self.guild
        me = guild.me
        nick_atual = me.nick or me.name
        avatar_url = me.display_avatar.url

        em = discord.Embed(
            title=f"{BOT}  Perfil do Bot — {guild.name}",
            color=0x5865F2,
            
        )
        em.set_thumbnail(url=avatar_url)
        em.add_field(name=f"{ROLES}  Nick Atual", value=f"> **{nick_atual}**", inline=True)
        em.add_field(
            name=f"{SETTINGS}  Personalizar",
            value=(
                f"> {DOT} **Alterar Nick** — muda o nome do bot neste servidor\n"
                f"> {DOT} **Alterar Foto** — muda o avatar do bot só neste servidor\n"
                f"> {DOT} **Alterar Banner** — muda o banner do bot só neste servidor"
            ),
            inline=False,
        )
        await inter.response.send_message(embed=em, view=PerfilBotView(guild, inter.client), ephemeral=True)

    async def _logs_compras(self, inter):
        gid = str(self.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)
        canal_id = cfg.get("canal_compras_id")
        canal_txt = f"<#{canal_id}>" if canal_id else f"{OFF} *Não configurado*"

        em = discord.Embed(
            title=f"{CART}  Logs de Compras — {self.guild.name}",
            color=0x5865F2,
            
        )
        em.add_field(name=f"{INFO}  Canal Atual", value=f"> {canal_txt}", inline=False)
        em.add_field(
            name=f"{SETTINGS}  Como funciona",
            value=(
                f"> {DOT} Quando alguém comprar salas para este servidor\n"
                f"> {DOT} O bot envia um log no canal selecionado\n"
                f"> {DOT} Selecione o canal abaixo para configurar"
            ),
            inline=False,
        )
        await inter.response.send_message(embed=em, view=LogsComprasView(self.guild), ephemeral=True)


# ═══════════════════════════════════════════
#  Valor da Sala
# ═══════════════════════════════════════════

class ValorSalaView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Alterar Preço", style=discord.ButtonStyle.primary)
    async def btn_alterar(self, inter, btn):
        await inter.response.send_modal(ValorSalaModal())


class ValorSalaMenuView(discord.ui.View):
    """View usada dentro do /mod — edita a mensagem então usa send_modal direto."""
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.button(label="Preço Global", style=discord.ButtonStyle.primary, row=0)
    async def btn_alterar(self, inter, btn):
        await inter.response.send_modal(ValorSalaModal())

    @discord.ui.button(label="Preço por Servidor", style=discord.ButtonStyle.secondary, row=0)
    async def btn_preco_servidor(self, inter, btn):
        await inter.response.send_modal(ValorSalaServidorModal())


class ValorSalaModal(discord.ui.Modal, title="Alterar Valor da Sala"):
    centavos = discord.ui.TextInput(
        label="Valor em centavos por sala",
        placeholder="Ex: 9  (= R$ 0,09)   15  (= R$ 0,15)",
        min_length=1, max_length=6,
    )

    async def on_submit(self, inter):
        from utils.database import botconfig_load, botconfig_save, get_db
        try:
            cts = int(self.centavos.value.strip())
            if cts <= 0: raise ValueError
            novo = round(cts / 100, 2)
        except ValueError:
            return await inter.response.send_message(
                embed=_mod_emb("❌  Valor inválido.", config.COR_ERRO, "Digite só números inteiros. Ex: `9` para R$ 0,09"),
                ephemeral=True,
            )

        await inter.response.defer(ephemeral=True)
        try:
            # 1. Atualiza preço global no Supabase botconfig
            cfg = await asyncio.to_thread(botconfig_load)
            cfg["preco_por_sala"] = novo
            await asyncio.to_thread(botconfig_save, cfg)

            # 2. Limpa preco_sala individual de todos os servidores
            count = 0
            try:
                res = await asyncio.to_thread(
                    lambda: get_db().table("guild_config").update({"preco_sala": None}).not_.is_("preco_sala", "null").execute()
                )
                count = len(res.data or [])
            except Exception:
                pass

            em = _mod_emb(f"{ON}  Valor Global Atualizado!", config.COR_SUCESSO)
            em.add_field(name=f"{DOLLAR}  Novo Preço", value=f"> **{cts} centavos** — R$ {novo:.2f} por sala", inline=False)
            if count > 0:
                em.add_field(name=f"{INFO}  Servidores", value=f"> Preço individual removido de **{count}** servidor(es)", inline=False)
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as _ex:
            _log.error(f"[ValorSalaModal] {_ex}", exc_info=True)
            await inter.followup.send(embed=_mod_emb("❌ Erro", config.COR_ERRO, f"`{_ex}`"), ephemeral=True)


class ValorSalaServidorModal(discord.ui.Modal, title="Preço por Servidor"):
    guild_id = discord.ui.TextInput(
        label="ID do servidor",
        placeholder="Ex: 123456789012345678",
        min_length=15, max_length=20,
    )
    centavos = discord.ui.TextInput(
        label="Valor em centavos por sala",
        placeholder="Ex: 9  (= R$ 0,09)   15  (= R$ 0,15)",
        min_length=1, max_length=6,
    )

    async def on_submit(self, inter):
        from utils.database import guild_config_set
        try:
            gid = self.guild_id.value.strip()
            cts = int(self.centavos.value.strip())
            if cts <= 0: raise ValueError
            novo = round(cts / 100, 2)
        except ValueError:
            return await inter.response.send_message(
                embed=_mod_emb("❌  Dados inválidos.", config.COR_ERRO, "ID do servidor deve ser numérico e centavos deve ser > 0."),
                ephemeral=True,
            )

        await inter.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(guild_config_set, gid, {"preco_sala": novo})
            guild_obj = inter.client.get_guild(int(gid)) if gid.isdigit() else None
            nome = guild_obj.name if guild_obj else f"Servidor `{gid}`"
            em = _mod_emb(f"{ON}  Preço do Servidor Atualizado!", config.COR_SUCESSO)
            em.add_field(name=f"{INFO}  Servidor", value=f"> {nome}", inline=False)
            em.add_field(name=f"{DOLLAR}  Novo Preço", value=f"> **{cts} centavos** — R$ {novo:.2f} por sala", inline=False)
            em.add_field(name=f"{SETTINGS}  Observação", value=f"> Este preço sobrepõe o global apenas para este servidor.", inline=False)
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as _ex:
            _log.error(f"[ValorSalaServidorModal] {_ex}", exc_info=True)
            await inter.followup.send(embed=_mod_emb("❌ Erro", config.COR_ERRO, f"`{_ex}`"), ephemeral=True)


# ═══════════════════════════════════════════
#  Logs de Compras — ChannelSelect
# ═══════════════════════════════════════════

class LogsComprasView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de logs de compras",
        channel_types=[discord.ChannelType.text],
        min_values=1,
        max_values=1,
    )
    async def channel_select(self, inter, sel):
        canal = sel.values[0]
        gid = str(self.guild.id)
        await asyncio.to_thread(guild_config_set, gid, {"canal_compras_id": canal.id})

        em = discord.Embed(
            title=f"{ON}  Canal de Logs Configurado!",
            color=config.COR_SUCESSO,
            
        )
        em.add_field(name=f"{INFO}  Canal", value=f"> {canal.mention}", inline=True)
        em.add_field(
            name=f"{SETTINGS}  O que acontece agora",
            value=(
                f"> {DOT} Toda compra de salas para **{self.guild.name}** será registrada em {canal.mention}\n"
                f"> {DOT} Para alterar, use `/dev` novamente"
            ),
            inline=False,
        )
        await inter.response.edit_message(embed=em, view=None)


# ═══════════════════════════════════════════
#  Perfil do Bot no Servidor
# ═══════════════════════════════════════════

class PerfilBotView(discord.ui.View):
    def __init__(self, guild, bot=None):
        super().__init__(timeout=300)
        self.guild = guild
        self.bot = bot

    @discord.ui.button(label="Alterar Nick", emoji=PE["roles"], style=discord.ButtonStyle.primary, row=0)
    async def btn_nick(self, inter, btn):
        await inter.response.send_modal(ModalNickBot(self.guild))

    @discord.ui.button(label="Alterar Foto", emoji=PE["bot"], style=discord.ButtonStyle.success, row=0)
    async def btn_foto(self, inter, btn):
        await self._upload_asset(inter, campo="avatar", titulo="Foto")

    @discord.ui.button(label="Alterar Banner", emoji=PE["vision"], style=discord.ButtonStyle.secondary, row=0)
    async def btn_banner(self, inter, btn):
        await self._upload_asset(inter, campo="banner", titulo="Banner")

    async def _upload_asset(self, inter, campo: str, titulo: str):
        if not inter.user.guild_permissions.administrator and inter.user.id not in config.ADMIN_IDS:
            return await inter.response.send_message(
                embed=_err("Sem permissão", f"{OFF} Apenas administradores podem alterar o {titulo.lower()} do bot."),
                ephemeral=True,
            )
        await inter.response.defer(ephemeral=True)

        em_inst = discord.Embed(
            title=f"{BOT}  Enviar Novo {titulo}",
            color=0x5865F2,
            description=(
                f"{DOT} Envie agora **neste canal** uma imagem como **anexo** (PNG, JPG, GIF ou WEBP).\n"
                f"{DOT} Tamanho máximo: **10 MB**.\n"
                f"{DOT} Você tem **60 segundos** para enviar.\n\n"
                f"{INFO} O {titulo.lower()} será aplicado **somente neste servidor**."
            ),
        )
        await inter.followup.send(embed=em_inst, ephemeral=True)

        bot = self.bot or inter.client

        def _check(m: discord.Message):
            return (
                m.author.id == inter.user.id
                and m.channel.id == inter.channel.id
                and bool(m.attachments)
            )

        try:
            msg = await bot.wait_for("message", timeout=60.0, check=_check)
        except asyncio.TimeoutError:
            return await inter.followup.send(
                embed=_err("Tempo Esgotado", f"{OFF} Você não enviou nenhuma imagem em 60 segundos."),
                ephemeral=True,
            )

        att = msg.attachments[0]
        ct = (att.content_type or "").lower()
        valid_types = ("image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp")
        if not any(v in ct for v in valid_types):
            ext = att.filename.lower().rsplit(".", 1)[-1] if "." in att.filename else ""
            if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
                return await inter.followup.send(
                    embed=_err("Arquivo Inválido", f"{OFF} Envie uma imagem PNG, JPG, GIF ou WEBP."),
                    ephemeral=True,
                )
            ct = f"image/{'jpeg' if ext=='jpg' else ext}"

        if att.size and att.size > 10 * 1024 * 1024:
            return await inter.followup.send(
                embed=_err("Arquivo Muito Grande", f"{OFF} A imagem deve ter no máximo 10 MB."),
                ephemeral=True,
            )

        try:
            img_bytes = await att.read()
        except Exception as ex:
            return await inter.followup.send(
                embed=_err("Erro ao Ler", f"{OFF} Não consegui baixar a imagem: `{ex}`"),
                ephemeral=True,
            )

        try:
            await msg.delete()
        except Exception:
            pass

        b64 = base64.b64encode(img_bytes).decode("ascii")
        mime = "image/png"
        if "jpeg" in ct or "jpg" in ct: mime = "image/jpeg"
        elif "gif" in ct: mime = "image/gif"
        elif "webp" in ct: mime = "image/webp"
        data_uri = f"data:{mime};base64,{b64}"

        import aiohttp as _aiohttp
        url = f"https://discord.com/api/v10/guilds/{self.guild.id}/members/@me"
        headers = {
            "Authorization": f"Bot {config.DISCORD_TOKEN}",
            "Content-Type": "application/json",
            "X-Audit-Log-Reason": f"{titulo} alterado via /dev por {inter.user} ({inter.user.id})",
        }
        payload = {campo: data_uri}

        ok = False
        err_txt = ""
        try:
            async with _aiohttp.ClientSession() as sess:
                async with sess.patch(url, headers=headers, json=payload) as resp:
                    ok = resp.status in (200, 204)
                    if not ok:
                        body = await resp.text()
                        err_txt = f"HTTP {resp.status} — {body[:300]}"
        except Exception as ex:
            err_txt = str(ex)

        if not ok:
            return await inter.followup.send(
                embed=_err(f"Erro ao Alterar {titulo}", f"{OFF} Falha na API do Discord:\n```{err_txt}```"),
                ephemeral=True,
            )

        em_ok = _ok(f"{titulo} Alterado!")
        em_ok.description = f"{ON} O {titulo.lower()} do bot foi atualizado somente no servidor **{self.guild.name}**."
        em_ok.set_thumbnail(url=att.url)
        await inter.followup.send(embed=em_ok, ephemeral=True)


class ModalNickBot(discord.ui.Modal, title="Alterar Nick do Bot"):
    novo_nick = discord.ui.TextInput(
        label="Novo nick do bot neste servidor",
        placeholder="Ex: SalasFF Bot",
        min_length=1,
        max_length=32,
    )

    def __init__(self, guild):
        super().__init__()
        self.guild = guild
        self.novo_nick.default = guild.me.nick or guild.me.name

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        nick = self.novo_nick.value.strip()
        try:
            await self.guild.me.edit(nick=nick)
            em = _ok(f"Nick Alterado!")
            em.description = f"{ROLES} Nick do bot neste servidor: **{nick}**"
        except discord.Forbidden:
            em = _err("Sem Permissão", f"{OFF} O bot não tem permissão para alterar o próprio nick neste servidor.")
        except Exception as ex:
            em = _err("Erro", f"{OFF} Falha ao alterar nick: `{ex}`")
        await inter.followup.send(embed=em, ephemeral=True)



# ═══════════════════════════════════════════
#  Comprar Salas pro Servidor
# ═══════════════════════════════════════════

class ComprarServidorBtnView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        self.guild = guild

    @discord.ui.button(label="Comprar Salas", style=discord.ButtonStyle.success, row=0)
    async def comprar(self, inter, btn):
        await inter.response.send_modal(ComprarServidorModal(self.guild))


class ComprarServidorModal(discord.ui.Modal, title="Comprar Salas — Servidor"):
    def __init__(self, guild):
        super().__init__()
        self.guild = guild
        preco = get_preco_por_sala()
        self.quantia = discord.ui.TextInput(
            label=f"Quantidade de salas (R$ {preco:.2f}/sala)",
            placeholder="Ex: 100",
            min_length=1,
            max_length=6,
        )
        self.add_item(self.quantia)

    async def on_submit(self, inter):
        await inter.response.defer(thinking=True, ephemeral=True)

        try:
            qtd = int(self.quantia.value.strip())
            if qtd < 1:
                raise ValueError
        except ValueError:
            return await inter.followup.send(embed=_err("Quantidade inválida."), ephemeral=True)

        preco = get_preco_por_sala()
        valor = round(qtd * preco, 2)

        try:
            pix = criar_cobranca_pix(valor, f"{qtd} salas servidor {self.guild.name}")
        except Exception as ex:
            return await inter.followup.send(embed=_err(f"Erro PIX: {ex}"), ephemeral=True)

        # Registra pedido vinculado ao servidor
        from utils.database import criar_pedido_pix
        criar_pedido_pix(
            f"guild_{self.guild.id}",
            f"Servidor: {self.guild.name}",
            pix["txid"],
            qtd,
            valor,
        )

        em = discord.Embed(
            title=f"{MONEY}  PIX Gerado — {self.guild.name}",
            color=0xA855F7,
            
        )

        em.add_field(name=f"{CART}  Salas", value=f"> **{qtd}**", inline=True)
        em.add_field(name=f"{MONEY}  Total", value=f"> **R$ {valor:.2f}**", inline=True)
        em.add_field(name=f"{SETTINGS}  Validade", value=f"> **1 hora**", inline=True)
        em.add_field(name=f"{CART}  PIX Copia e Cola", value=f"```{pix['copia_cola']}```", inline=False)


        files = []
        if pix.get("qrcode"):
            try:
                img = base64.b64decode(pix["qrcode"].split(",")[-1])
                files.append(discord.File(io.BytesIO(img), "qrcode.png"))
                em.set_image(url="attachment://qrcode.png")
            except:
                pass

        await inter.followup.send(embed=em, files=files, ephemeral=True)


# ═══════════════════════════════════════════
#  Cargo Sala — Select de cargos do servidor
# ═══════════════════════════════════════════

class CargoSalaView(discord.ui.View):
    """3 botões: Adicionar Cargo, Remover Cargo, Cargo OFF."""
    def __init__(self, guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.button(label="Adicionar Cargo", emoji=PE["adduser"], style=discord.ButtonStyle.success, row=0)
    async def btn_add(self, inter, btn):
        em = _info("Selecione o Cargo", f"{ROLES} Escolha abaixo o cargo que poderá criar salas:")
        await inter.response.send_message(embed=em, view=CargoSalaSelectView(self.guild), ephemeral=True)

    @discord.ui.button(label="Remover Cargo", emoji=PE["othertrash"], style=discord.ButtonStyle.danger, row=0)
    async def btn_remove(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        gid = str(self.guild.id)
        await asyncio.to_thread(guild_set_cargo_sala, gid, None)
        em = _ok("Cargo Removido!")
        em.description = f"{TRASH} O cargo de criação de salas foi removido.\n{DOT} Nenhum cargo pode criar salas pelo servidor agora."
        await inter.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="Cargo OFF", emoji=PE["off"], style=discord.ButtonStyle.secondary, row=0)
    async def btn_off(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        gid = str(self.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)
        cargo_off = cfg.get("cargo_off", False)

        if cargo_off:
            # Reativar
            from utils.database import guild_config_set
            await asyncio.to_thread(guild_config_set, gid, {"cargo_off": False})
            em = _ok("Cargo Reativado!")
            em.description = f"{ON} Os cargos voltaram a funcionar normalmente.\n{DOT} Quem tem o cargo configurado pode criar salas novamente."
            await inter.followup.send(embed=em, ephemeral=True)
        else:
            # Confirmar desativação
            em = discord.Embed(
                title=f"{RAGE}  Desabilitar Cargos?",
                color=0xFF4444,
                
            )
            em.description = (
                f"**⚠️ Atenção!**\n\n"
                f"{DOT} **Nenhum cargo** poderá criar salas até ser habilitado novamente.\n"
                f"{DOT} O saldo do servidor será mantido.\n"
                f"{DOT} Use **Cargo OFF** novamente para reativar."
            )
            await inter.followup.send(embed=em, view=CargoOffConfirmView(gid), ephemeral=True)


class CargoOffConfirmView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=60)
        self.guild_id = guild_id

    @discord.ui.button(label="Confirmar Desativação", emoji=PE["off"], style=discord.ButtonStyle.danger, row=0)
    async def confirmar(self, inter, btn):
        from utils.database import guild_config_set
        await asyncio.to_thread(guild_config_set, self.guild_id, {"cargo_off": True})
        for item in self.children:
            item.disabled = True
        em = _ok("Cargos Desabilitados!")
        em.description = f"{OFF} Nenhum cargo pode criar salas até ser habilitado novamente.\n{DOT} Use `/dev` → **Cargo Sala** → **Cargo OFF** para reativar."
        em.color = 0xFF4444
        await inter.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="Cancelar", emoji=PE["on"], style=discord.ButtonStyle.secondary, row=0)
    async def cancelar(self, inter, btn):
        for item in self.children:
            item.disabled = True
        em = _info("Cancelado", f"{ON} Os cargos continuam funcionando normalmente.")
        await inter.response.edit_message(embed=em, view=self)


class CargoSalaSelectView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Selecione o cargo que pode criar salas",
        min_values=1,
        max_values=1,
    )
    async def role_select(self, inter, sel):
        role = sel.values[0]
        gid = str(self.guild.id)

        await asyncio.to_thread(guild_set_cargo_sala, gid, role.id)
        # Reativa cargo se estava OFF
        from utils.database import guild_config_set
        await asyncio.to_thread(guild_config_set, gid, {"cargo_off": False})

        cfg_atual = await asyncio.to_thread(guild_config_get, gid)
        saldo = cfg_atual.get("saldo", 0)

        em = _ok(f"Cargo Configurado!")
        em.add_field(name=f"{ROLES}  Cargo", value=f"> {role.mention}", inline=True)
        em.add_field(name=f"{STATS}  Saldo", value=f"> **{saldo}**", inline=True)
        em.add_field(
            name=f"{INFO}  Próximos passos",
            value=f"> {DOT} Membros com {role.mention} já podem criar salas\n> {DOT} Salas são descontadas do saldo do servidor",
            inline=False,
        )
        await inter.response.edit_message(embed=em, view=None)


# ═══════════════════════════════════════════
#  Usuário Sala — adicionar/remover salas por usuário
# ═══════════════════════════════════════════

class UsuarioSalaView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=120)
        self.guild = guild

    @discord.ui.button(label="Adicionar Salas", emoji=PE["adduser"], style=discord.ButtonStyle.success, row=0)
    async def btn_add(self, inter, btn):
        await self._pedir_no_chat(inter, "add")

    @discord.ui.button(label="Remover Salas", emoji=PE["othertrash"], style=discord.ButtonStyle.danger, row=0)
    async def btn_remove(self, inter, btn):
        await self._pedir_no_chat(inter, "remove")

    async def _pedir_no_chat(self, inter, acao):
        acao_txt = "adicionar" if acao == "add" else "remover"
        em = discord.Embed(
            title=f"{ADDUSER}  {acao_txt.title()} Salas",
            color=0x5865F2,
            
        )
        em.description = (
            f"Envie no chat a **menção do usuário** e a **quantidade**.\n\n"
            f"> {DOT} Exemplo: `@usuario 50`\n"
            f"> {DOT} Ou: `<@1268379167519408139> 50`\n\n"
            f"{SETTINGS} Aguardando resposta... *(30 segundos)*"
        )
        await inter.response.send_message(embed=em, ephemeral=True)

        def check(m):
            return m.author.id == inter.user.id and m.channel.id == inter.channel.id

        try:
            msg = await inter.client.wait_for("message", check=check, timeout=30)
        except asyncio.TimeoutError:
            return await inter.followup.send(embed=_err("Tempo esgotado", f"{DOT} Nenhuma resposta em 30 segundos."), ephemeral=True)

        # Deleta a mensagem do usuário pra manter limpo
        try:
            await msg.delete()
        except:
            pass

        raw = msg.content.strip()

        # Parse: aceita "@user qtd", "<@id> qtd", "<@!id> qtd", "id qtd"
        import re
        match = re.match(r'<?@?!?(\d{15,21})>?\s+(\d+)', raw)
        if not match:
            return await inter.followup.send(embed=_err("Formato inválido", f"{DOT} Use: `@usuario quantidade`\n{DOT} Ex: `@bruno 50`"), ephemeral=True)

        user_id = match.group(1)
        qtd = int(match.group(2))
        if qtd < 1:
            return await inter.followup.send(embed=_err("Quantidade inválida"), ephemeral=True)

        gid = str(self.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)
        saldo_srv = cfg.get("saldo", 0)

        if acao == "add":
            if qtd > saldo_srv:
                return await inter.followup.send(embed=_err("Saldo insuficiente", f"{DOT} O servidor tem **{saldo_srv} salas** mas você tentou adicionar **{qtd}**."), ephemeral=True)

            from utils.database import guild_config_set, adicionar_saldo_usuario
            novo_saldo_srv = saldo_srv - qtd
            await asyncio.to_thread(guild_config_set, gid, {"saldo": novo_saldo_srv})

            try:
                member = await self.guild.fetch_member(int(user_id))
                nome = member.display_name
            except:
                nome = f"User {user_id}"

            await asyncio.to_thread(adicionar_saldo_usuario, user_id, nome, qtd)

            em = _ok(f"Salas Adicionadas!")
            em.description = (
                f"{ADDUSER} **{qtd} salas** adicionadas a <@{user_id}>\n\n"
                f"{DOT} Saldo do servidor: **{saldo_srv}** → **{novo_saldo_srv}**\n"
                f"{DOT} O usuário já pode usar as salas"
            )

        else:
            from utils.database import remover_salas_cliente
            removidas, saldo_user = await asyncio.to_thread(remover_salas_cliente, user_id, qtd)

            if removidas == 0:
                return await inter.followup.send(embed=_err("Sem salas", f"{DOT} O usuário não tem salas para remover."), ephemeral=True)

            from utils.database import guild_config_set
            novo_saldo_srv = saldo_srv + removidas
            await asyncio.to_thread(guild_config_set, gid, {"saldo": novo_saldo_srv})

            em = _ok(f"Salas Removidas!")
            em.description = (
                f"{TRASH} **{removidas} salas** removidas de <@{user_id}>\n\n"
                f"{DOT} Saldo do servidor: **{saldo_srv}** → **{novo_saldo_srv}**\n"
                f"{DOT} Saldo restante do usuário: **{saldo_user}**"
            )

        await inter.followup.send(embed=em, ephemeral=True)


# ═══════════════════════════════════════════
#  COG
# ═══════════════════════════════════════════

class DevCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="dev", description="Central de configurações do servidor.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_dev(self, inter):
        # Só admin do servidor OU dono do bot
        if not inter.user.guild_permissions.administrator and inter.user.id not in config.ADMIN_IDS:
            return await inter.response.send_message(
                embed=_err("Sem permissão", f"{OFF} Você precisa ser **Administrador** do servidor para usar este comando."),
                ephemeral=True,
            )

        await inter.response.defer(ephemeral=True)
        gid = str(inter.guild.id)
        cfg = await asyncio.to_thread(guild_config_get, gid)

        em = _dev_embed(inter.guild, cfg)
        await inter.followup.send(embed=em, view=DevSelectView(inter.guild), ephemeral=True)


# ═══════════════════════════════════════════
#  /mod — Painel de moderação de servidores
#  Só aparece nos GUILD_IDS e só ADMIN_IDS usa
# ═══════════════════════════════════════════

_FOOTER = "SalasFF Bot • salasff.com"
_ICON   = "https://cdn.discordapp.com/emojis/1481526579963363450.webp"

def _mod_emb(t, c=0x5865F2, d=""):
    em = discord.Embed(title=t, color=c)
    if d: em.description = d
    return em


def _embed_servidor(guild_obj, cfg):
    saldo = cfg.get("saldo", 0)
    cargo_id = cfg.get("cargo_sala_id")
    nome = guild_obj.name if guild_obj else "Servidor desconhecido"
    icone = guild_obj.icon.url if (guild_obj and guild_obj.icon) else None

    em = discord.Embed(title=f"{STATS}  {nome}", color=0x5865F2)
    if icone:
        em.set_thumbnail(url=icone)
    membros = guild_obj.member_count if guild_obj else 0
    em.add_field(name=f"{CART}  Saldo",   value=f"> **{saldo}** salas", inline=True)
    em.add_field(name=f"{ROLES}  Membros", value=f"> **{membros}**",    inline=True)
    em.add_field(name=f"{ROLES}  Cargo Sala",
                 value=f"> <@&{cargo_id}>" if cargo_id else f"> {OFF} *Não definido*",
                 inline=True)

    # Lucro da org
    lucro_cent = cfg.get("lucro_centavos_sala", 0)
    lucro_tot  = cfg.get("lucro_total", 0.0)
    salas_vendidas_lucro = cfg.get("lucro_salas_contadas", 0)
    if lucro_cent > 0 or lucro_tot > 0:
        em.add_field(
            name=f"{MONEY}  Lucro da Org",
            value=(
                f"> **R$ {lucro_tot:.2f}** acumulado\n"
                f"> {DOT} **{salas_vendidas_lucro}** salas contadas\n"
                f"> {DOT} **{lucro_cent}** centavo(s) por sala"
            ),
            inline=False,
        )
    return em


class ModServidorView(discord.ui.View):
    def __init__(self, bot, guild_id: str):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Entrar no Servidor", emoji=PE["vision"], style=discord.ButtonStyle.primary, row=0)
    async def btn_entrar(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        guild_obj = self.bot.get_guild(int(self.guild_id))
        if not guild_obj:
            return await inter.followup.send(embed=_mod_emb("❌  Servidor não encontrado.", config.COR_ERRO), ephemeral=True)
        invite = None
        for ch in guild_obj.text_channels:
            try:
                invite = await ch.create_invite(max_age=300, max_uses=1, unique=True, reason="Acesso admin via /mod")
                break
            except Exception:
                continue
        if not invite:
            return await inter.followup.send(
                embed=_mod_emb("❌  Não consegui criar o invite.", config.COR_ERRO,
                               f"O bot pode não ter permissão de criar convites em nenhum canal de **{guild_obj.name}**."),
                ephemeral=True,
            )
        em = _mod_emb(f"{ON}  Invite Gerado — {guild_obj.name}", config.COR_SUCESSO)
        em.add_field(name=f"{INFO}  Link", value=f"> {invite.url}", inline=False)
        em.add_field(name=f"{SETTINGS}  Validade", value=f"> **5 minutos** • **1 uso**", inline=True)
        await inter.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="Adicionar", emoji=PE["on"], style=discord.ButtonStyle.success, row=1)
    async def btn_add(self, inter, btn):
        await inter.response.send_modal(ModSaldoModal(self.bot, self.guild_id, acao="adicionar"))

    @discord.ui.button(label="Remover", emoji=PE["off"], style=discord.ButtonStyle.danger, row=1)
    async def btn_rem(self, inter, btn):
        await inter.response.send_modal(ModSaldoModal(self.bot, self.guild_id, acao="remover"))

    @discord.ui.button(label="Atualizar", emoji=PE["refresh"], style=discord.ButtonStyle.secondary, row=1)
    async def btn_refresh(self, inter, btn):
        await inter.response.defer()
        cfg = await asyncio.to_thread(guild_config_get, self.guild_id)
        guild_obj = self.bot.get_guild(int(self.guild_id))
        await inter.edit_original_response(embed=_embed_servidor(guild_obj, cfg), view=self)

    @discord.ui.button(label="Lucro", emoji=PE["otherdollar"], style=discord.ButtonStyle.success, row=2)
    async def btn_lucro_org(self, inter, btn):
        cfg = await asyncio.to_thread(guild_config_get, self.guild_id)
        lucro_cent = cfg.get("lucro_centavos_sala", 0)
        lucro_tot  = cfg.get("lucro_total", 0.0)
        salas_ct   = cfg.get("lucro_salas_contadas", 0)
        guild_obj = self.bot.get_guild(int(self.guild_id))
        nome = guild_obj.name if guild_obj else self.guild_id

        em = _mod_emb(f"{MONEY}  Lucro — {nome}", 0x2ecc71)
        em.description = (
            f"Defina o valor em **centavos** que você ganha por cada sala comprada por esta org.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{DOT} **Centavos por sala:** {lucro_cent}\n"
            f"{DOT} **Lucro acumulado:** R$ {lucro_tot:.2f}\n"
            f"{DOT} **Salas contadas:** {salas_ct}\n\n"
            f"{INFO} O lucro é contabilizado em **qualquer compra feita dentro desta org**:\n"
            f"> {DOT} Carteira pessoal do usuário (via `/c` → Comprar)\n"
            f"> {DOT} Saldo do servidor (via `/dev` → Comprar Salas)"
        )
        await inter.response.send_message(embed=em, view=LucroOrgView(self.bot, self.guild_id), ephemeral=True)

    @discord.ui.button(label="Remover Org", emoji=PE["rage"], style=discord.ButtonStyle.danger, row=2)
    async def btn_remover_org(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        guild_obj = self.bot.get_guild(int(self.guild_id))
        if not guild_obj:
            return await inter.followup.send(
                embed=_mod_emb("❌  Servidor não encontrado.", config.COR_ERRO), ephemeral=True
            )
        # Confirmação antes de sair
        em = _mod_emb(f"{OFF}  Remover Bot de {guild_obj.name}", config.COR_AVISO)
        em.description = (
            f"Tem certeza que deseja **remover o bot** de **{guild_obj.name}**?\n\n"
            f"> {DOT} O bot vai sair do servidor\n"
            f"> {DOT} O saldo salvo **não** será apagado\n"
            f"> {DOT} Esta ação não pode ser desfeita"
        )
        await inter.followup.send(embed=em, view=ConfirmarRemoverOrgView(self.bot, self.guild_id), ephemeral=True)


class LucroOrgView(discord.ui.View):
    def __init__(self, bot, guild_id: str):
        super().__init__(timeout=180)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Definir Centavos", emoji=PE["settings"], style=discord.ButtonStyle.primary, row=0)
    async def btn_definir(self, inter, btn):
        await inter.response.send_modal(LucroOrgModal(self.guild_id))

    @discord.ui.button(label="Zerar Lucro", emoji=PE["off"], style=discord.ButtonStyle.danger, row=0)
    async def btn_zerar(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        await asyncio.to_thread(
            guild_config_set, self.guild_id,
            {"lucro_total": 0.0, "lucro_salas_contadas": 0},
        )
        em = _mod_emb(f"{ON}  Lucro Zerado!", config.COR_SUCESSO)
        em.description = f"{DOT} O lucro acumulado foi resetado para **R$ 0,00**.\n{DOT} O valor de **centavos por sala** foi mantido."
        await inter.followup.send(embed=em, ephemeral=True)


class LucroOrgModal(discord.ui.Modal, title="💰 Definir Centavos por Sala"):
    centavos = discord.ui.TextInput(
        label="Centavos por sala",
        placeholder="Ex: 5 (R$ 0,05 por sala)",
        min_length=1, max_length=5,
    )

    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        try:
            c = int(self.centavos.value.strip())
            if c < 0 or c > 10000: raise ValueError
        except ValueError:
            return await inter.followup.send(
                embed=_mod_emb("❌  Valor inválido.", config.COR_ERRO,
                               f"{DOT} Digite um número inteiro entre **0** e **10000** centavos."),
                ephemeral=True,
            )
        await asyncio.to_thread(guild_config_set, self.guild_id, {"lucro_centavos_sala": c})
        em = _mod_emb(f"{ON}  Centavos Atualizados!", config.COR_SUCESSO)
        em.description = (
            f"{DOT} Agora você ganha **{c}** centavo(s) por cada sala comprada nesta org.\n"
            f"{DOT} Isso equivale a **R$ {c/100:.2f}** por sala.\n\n"
            f"{INFO} O lucro é contabilizado em compras feitas **dentro desta org**:\n"
            f"> {DOT} Carteira pessoal (via `/c` → Comprar)\n"
            f"> {DOT} Saldo do servidor (via `/dev` → Comprar Salas)"
        )
        await inter.followup.send(embed=em, ephemeral=True)


class ConfirmarRemoverOrgView(discord.ui.View):
    def __init__(self, bot, guild_id: str):
        super().__init__(timeout=60)
        self.bot = bot
        self.guild_id = guild_id

    @discord.ui.button(label="Confirmar", emoji=PE["on"], style=discord.ButtonStyle.danger)
    async def btn_confirmar(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        guild_obj = self.bot.get_guild(int(self.guild_id))
        nome = guild_obj.name if guild_obj else self.guild_id
        try:
            await guild_obj.leave()
            em = _mod_emb(f"{ON}  Bot removido!", config.COR_SUCESSO)
            em.add_field(name=f"{INFO}  Servidor", value=f"> **{nome}**", inline=False)
            for item in self.children:
                item.disabled = True
            await inter.edit_original_response(embed=em, view=self)
        except Exception as ex:
            await inter.followup.send(
                embed=_mod_emb("❌  Erro ao sair.", config.COR_ERRO, str(ex)), ephemeral=True
            )

    @discord.ui.button(label="Cancelar", emoji=PE["off"], style=discord.ButtonStyle.secondary)
    async def btn_cancelar(self, inter, btn):
        em = _mod_emb(f"{OFF}  Cancelado.", config.COR_AVISO, "O bot **não** foi removido do servidor.")
        for item in self.children:
            item.disabled = True
        await inter.response.edit_message(embed=em, view=self)


class ModSaldoModal(discord.ui.Modal):
    quantidade = discord.ui.TextInput(
        label="Quantidade de salas",
        placeholder="Ex: 50",
        min_length=1, max_length=6,
    )

    def __init__(self, bot, guild_id: str, acao: str):
        super().__init__(title=f"{'Adicionar' if acao == 'adicionar' else 'Remover'} Salas")
        self.bot = bot
        self.guild_id = guild_id
        self.acao = acao

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        try:
            qtd = int(self.quantidade.value.strip())
            if qtd < 1: raise ValueError
        except ValueError:
            return await inter.followup.send(
                embed=_mod_emb("❌  Quantidade inválida.", config.COR_ERRO), ephemeral=True
            )

        cfg = await asyncio.to_thread(guild_config_get, self.guild_id)
        saldo_atual = cfg.get("saldo", 0)

        if self.acao == "adicionar":
            novo = await asyncio.to_thread(guild_adicionar_saldo, self.guild_id, qtd)
            em = _mod_emb(f"{ON}  Salas Adicionadas!", config.COR_SUCESSO)
            em.add_field(name=f"{CART}  Adicionadas", value=f"> **+{qtd}**", inline=True)
            em.add_field(name=f"{STATS}  Novo Saldo",  value=f"> **{novo}**", inline=True)
        else:
            if qtd > saldo_atual:
                return await inter.followup.send(
                    embed=_mod_emb(f"❌  Saldo insuficiente.", config.COR_ERRO,
                                   f"Saldo atual: **{saldo_atual}** salas."), ephemeral=True
                )
            novo = saldo_atual - qtd
            await asyncio.to_thread(guild_config_set, self.guild_id, {"saldo": novo})
            em = _mod_emb(f"{OFF}  Salas Removidas!", config.COR_AVISO)
            em.add_field(name=f"{CART}  Removidas",  value=f"> **-{qtd}**", inline=True)
            em.add_field(name=f"{STATS}  Novo Saldo", value=f"> **{novo}**", inline=True)

        guild_obj = self.bot.get_guild(int(self.guild_id))
        if guild_obj:
            em.add_field(name=f"{INFO}  Servidor", value=f"> **{guild_obj.name}**", inline=False)
        await inter.followup.send(embed=em, ephemeral=True)


class ModGrupoView(discord.ui.View):
    """Select com até 25 orgs de um grupo específico."""
    def __init__(self, bot, guilds_slice: list, offset: int):
        super().__init__(timeout=300)
        self.bot = bot
        options = []
        for i, (guild_obj, cfg) in enumerate(guilds_slice):
            num = offset + i + 1
            saldo = cfg.get("saldo", 0)
            membros = guild_obj.member_count or 0
            if saldo == 0:
                desc = f"{membros} membros • Sem salas"
            elif saldo == 1:
                desc = f"{membros} membros • 1 sala"
            else:
                desc = f"{membros} membros • {saldo} salas"
            prefixo = f"Org {num} — "
            label = f"{prefixo}{guild_obj.name[:100 - len(prefixo)]}"
            options.append(discord.SelectOption(
                label=label,
                description=desc,
                value=str(guild_obj.id),
                emoji="🟢" if saldo > 0 else "🔴",
            ))
        self.select_org.options = options

    @discord.ui.select(placeholder="Selecione a org")
    async def select_org(self, inter, sel):
        await inter.response.defer(ephemeral=True)
        gid = sel.values[0]
        cfg = await asyncio.to_thread(guild_config_get, gid)
        guild_obj = self.bot.get_guild(int(gid))
        em = _embed_servidor(guild_obj, cfg)
        await inter.followup.send(embed=em, view=ModServidorView(self.bot, gid), ephemeral=True)


class ModMenuView(discord.ui.View):
    """Select inicial com as categorias do /mod."""
    def __init__(self, bot, guilds_data: list):
        super().__init__(timeout=300)
        self.bot = bot
        self.guilds_data = guilds_data

    @discord.ui.select(
        placeholder="Selecione uma opção",
        options=[
            discord.SelectOption(
                label="Orgs",
                description="Gerencie os servidores onde o bot está",
                emoji="🟢",
                value="orgs",
            ),
            discord.SelectOption(
                label="Logs de Compras",
                description="Veja compras recentes de todos os servidores",
                emoji="🔴",
                value="logs",
            ),
            discord.SelectOption(
                label="Valor da Sala",
                description="Altere o preço cobrado por sala",
                emoji="🟢",
                value="valor_sala",
            ),
            discord.SelectOption(
                label="Evento",
                description="Ative/desative bônus de evento (10+1 ou 10+2)",
                emoji="🎉",
                value="evento",
            ),
            discord.SelectOption(
                label="API",
                description="Alterne entre API 1 (F) e API 2 (B)",
                emoji="⚙️",
                value="api",
            ),
            discord.SelectOption(
                label="Salas Criadas Config",
                description="Escolha qual API usar pra venda/ranking/indicação",
                emoji="🧩",
                value="salas_origem",
            ),
            discord.SelectOption(
                label="Banco PIX",
                description="Configure qual banco PIX usar para cobranças",
                emoji="💳",
                value="banco_pix",
            ),
            discord.SelectOption(
                label="Avaliação",
                description="Canal de avaliação com moderação estrita",
                emoji="⭐",
                value="avaliacao",
            ),
            discord.SelectOption(
                label="Chat (só comandos)",
                description="Canal que só aceita comandos do bot (/ e .)",
                emoji="💬",
                value="chat_cmd",
            ),
            discord.SelectOption(
                label="Convidar Clientes",
                description="DM em massa pra clientes com saldo que não estão no server",
                emoji="📨",
                value="convidar_clientes",
            ),
            discord.SelectOption(
                label="Logs Token Mode",
                description="Canal onde sair logs de quem configurou/ativou token",
                emoji="📢",
                value="logs_token",
            ),
        ],
    )
    async def select_menu(self, inter, sel):
        choice = sel.values[0]
        if choice == "orgs":
            await inter.response.edit_message(
                view=ModSelectView(self.bot, self.guilds_data)
            )
        elif choice == "valor_sala":
            from utils.pix import get_preco_por_sala
            preco_atual = get_preco_por_sala()
            em = discord.Embed(
                title=f"{DOLLAR}  Valor da Sala",
                color=0x5865F2,
                
            )
            em.add_field(name=f"{DOLLAR}  Preço Atual", value=f"> **{round(preco_atual*100)}** centavos — R$ {preco_atual:.2f}/sala", inline=False)
            em.add_field(name=f"{INFO}  Como alterar", value=f"> {DOT} Clique em **Alterar Preço** e digite os centavos\n> {DOT} Ex: `9` = R$ 0,09 por sala", inline=False)
            await inter.response.send_message(embed=em, view=ValorSalaMenuView(), ephemeral=True)
        elif choice == "logs":
            await inter.response.defer(ephemeral=True)
            from utils.database import ultimas_compras
            compras = await asyncio.to_thread(ultimas_compras, 10)
            # filtra só compras de guild
            guild_compras = [p for p in compras if str(p.get("user_id","")).startswith("guild_")]

            em = discord.Embed(
                title=f"{CART}  Logs de Compras — Servidores",
                color=0x5865F2,
                
            )

            if not guild_compras:
                em.description = f"> {OFF} *Nenhuma compra de servidor registrada ainda.*"
            else:
                for p in guild_compras[:10]:
                    gid = str(p["user_id"]).replace("guild_", "")
                    guild_obj = self.bot.get_guild(int(gid)) if gid.isdigit() else None
                    nome = guild_obj.name if guild_obj else p.get("user_nome", gid)
                    dt = (p.get("pago_em") or p.get("criado_em") or "")[:16].replace("T", " ")
                    em.add_field(
                        name=f"{ON}  {nome}",
                        value=f"> {CART} **{p['quantia']}** salas  {DOT}  R$ {float(p['valor']):.2f}  {DOT}  `{dt}`",
                        inline=False,
                    )

            await inter.followup.send(embed=em, ephemeral=True)

        elif choice == "evento":
            from cogs.botconfig import get_evento_ativo
            evento = get_evento_ativo()
            _label = {None: f"{OFF} Desativado", "10+1": "🎉 10+1 ativo", "10+2": "🎉 10+2 ativo"}
            em = discord.Embed(
                title=f"🎉  Evento de Bônus",
                color=0xA855F7,
                
            )
            em.add_field(
                name=f"{SETTINGS}  Status Atual",
                value=f"> **{_label.get(evento, str(evento))}**",
                inline=False,
            )
            em.add_field(
                name=f"{INFO}  Como funciona",
                value=(
                    f"> {DOT} **10+1** — a cada 10 salas compradas, +1 grátis\n"
                    f"> {DOT} **10+2** — a cada 10 salas compradas, +2 grátis\n"
                    f"> {DOT} O bônus é creditado automaticamente na confirmação do PIX"
                ),
                inline=False,
            )
            await inter.response.send_message(embed=em, view=EventoView(), ephemeral=True)

        elif choice == "api":
            from cogs.botconfig import get_api_principal, get_api_secundario, get_api2_key, get_anticai_ativo
            principal  = get_api_principal()
            secundario = get_api_secundario()
            key_atual  = get_api2_key()
            anticai    = get_anticai_ativo()
            _nomes = {"1": "API 1 — F (salasff.com)", "2": "API 2 — B (freefireapi.online)"}
            em = discord.Embed(title=f"⚙️  Configuração de API", color=0x5865F2)
            em.add_field(
                name=f"⭐  API Principal",
                value=f"> **{_nomes.get(principal)}**\n> Sempre tentada primeiro em toda criação de sala.",
                inline=False,
            )
            em.add_field(
                name=f"🔄  API Secundária",
                value=f"> **{_nomes.get(secundario)}**\n> Usada como fallback quando a principal falha.",
                inline=False,
            )
            status_anticai = "✅ Ativado" if anticai else "❌ Desativado"
            em.add_field(
                name=f"🛡️  Anti-Caí",
                value=f"> **{status_anticai}** — se ativado, usa a secundária quando a principal falha\n> A próxima sala sempre tenta a **principal** novamente.",
                inline=False,
            )
            em.add_field(
                name=f"🗝️  Key Mãe (API 2)",
                value=f"> `{key_atual[:12]}...`",
                inline=False,
            )
            em.add_field(
                name=f"{INFO}  APIs disponíveis",
                value=(
                    f"> {DOT} **API 1 — F** → salasff.com\n"
                    f"> {DOT} **API 2 — B** → freefireapi.online\n\n"
                    f"> Use **Ver Usos** para checar quanto resta na key da API 2"
                ),
                inline=False,
            )
            await inter.response.send_message(embed=em, view=ApiView(), ephemeral=True)

        elif choice == "salas_origem":
            from cogs.botconfig import get_api_por_origem
            v = get_api_por_origem("venda")    or "—"
            r = get_api_por_origem("ranking")  or "—"
            i = get_api_por_origem("indicacao") or "—"
            def _lbl(x):
                if x == "1": return "**API 1** (F · salasff.com)"
                if x == "2": return "**API 2** (B · freefireapi.online)"
                return f"{OFF} Não definida (usa a API principal)"
            em = discord.Embed(
                title=f"🧩  Salas Criadas — Config por Origem",
                color=0x5865F2,
            )
            em.description = (
                f"{INFO}  Escolha qual **API** vai criar a sala dependendo de **como o saldo foi obtido**.\n"
                f"-# Se uma origem não estiver definida, ela usa a API principal padrão."
            )
            em.add_field(name=f"{DOT}  Salas de **Venda** (compra PIX)",     value=_lbl(v), inline=False)
            em.add_field(name=f"{DOT}  Salas de **Ranking** (prêmios)",       value=_lbl(r), inline=False)
            em.add_field(name=f"{DOT}  Salas de **Indicação** (convites)",    value=_lbl(i), inline=False)
            await inter.response.send_message(embed=em, view=SalasOrigemView(), ephemeral=True)

        elif choice == "banco_pix":
            from utils.pix import get_banco_ativo, get_banco_dividido_limite
            banco = get_banco_ativo()
            limite = get_banco_dividido_limite()
            _nomes = {
                "efi":       "Efí Bank",
                "mistic_15": "MisticPay 1,5%",
                "mistic_35": "MisticPay 0,35%",
                "dividido":  f"Dividido (abaixo de {limite} salas → 1,5% / acima → 0,35%)",
            }
            em = discord.Embed(title=f"💳  Banco PIX", color=0x5865F2)
            em.add_field(name=f"{SETTINGS}  Banco Atual", value=f"> **{_nomes.get(banco, banco)}**", inline=False)
            em.add_field(
                name=f"{INFO}  Bancos disponíveis",
                value=(
                    f"> 💳 **MisticPay 1,5%** — conta nova (ci_cunu...)\n"
                    f"> 💳 **MisticPay 0,35%** — conta antiga (ci_bypri...)\n"
                    f"> 🏦 **Efí Bank** — PIX com certificado\n"
                    f"> ⚡ **Dividido** — divide por quantidade de salas"
                ),
                inline=False,
            )
            await inter.response.send_message(embed=em, view=BancoPIXView(), ephemeral=True)

        elif choice == "avaliacao":
            em = discord.Embed(
                title=f"⭐  Canal de Avaliação",
                description=(
                    "Escolha o servidor para configurar o canal de avaliação.\n\n"
                    f"{DOT} Define **canal** onde rolam as avaliações\n"
                    f"{DOT} Define **cargo** que pode falar no canal\n"
                    f"{DOT} Bot deleta silenciosamente mensagens que:\n"
                    "  • não vêm de quem tem o cargo\n"
                    "  • contêm links\n"
                    "  • contêm anexos (imagens/vídeos)\n"
                    "  • não contêm `/` (formato nota: 10/10, 9/10)"
                ),
                color=0xA855F7,
            )
            await inter.response.send_message(
                embed=em,
                view=EscolherGuildAvaliacaoView(inter.client),
                ephemeral=True,
            )

        elif choice == "chat_cmd":
            em = discord.Embed(
                title=f"💬  Chat (Só Comandos do Bot)",
                description=(
                    "Escolha o servidor para configurar um canal que **só aceita comandos do bot**.\n\n"
                    f"{DOT} Define **canal** onde só comandos passam\n"
                    f"{DOT} Define **cargo** que pode falar livre (ex: ADM)\n"
                    f"{DOT} Bot deleta silenciosamente mensagens que:\n"
                    "  • não começam com `/` (slash) ou `.` (prefixo)\n"
                    "  • não vêm de quem tem o cargo livre\n"
                    "  • contêm anexos (imagens/vídeos)"
                ),
                color=0x3498DB,
            )
            await inter.response.send_message(
                embed=em,
                view=EscolherGuildChatView(inter.client),
                ephemeral=True,
            )

        elif choice == "convidar_clientes":
            em = discord.Embed(
                title="📨  Convidar Clientes",
                description=(
                    "Manda DM em massa pra clientes que **têm saldo** mas **não estão no servidor** escolhido.\n\n"
                    f"{DOT} Você escolhe o servidor de referência\n"
                    f"{DOT} Bot lista quantos clientes faltam\n"
                    f"{DOT} Você escreve a mensagem num modal\n"
                    f"{DOT} Bot anexa um convite permanente do server\n"
                    f"{DOT} Envio em background com rate limit (sem ban)\n\n"
                    "*Ideal pra avisar de novidades, atualizações ou novos servidores.*"
                ),
                color=0xFFD700,
            )
            await inter.response.send_message(
                embed=em,
                view=EscolherGuildConvidarView(inter.client),
                ephemeral=True,
            )

        elif choice == "logs_token":
            from utils.database import token_mode_log_channel_get
            ch_id = token_mode_log_channel_get()
            ch = self.bot.get_channel(int(ch_id)) if ch_id else None
            ch_txt = f"<#{ch_id}>" if ch else f"{OFF} *Não definido*"

            em = discord.Embed(
                title=f"📢  Logs Token Mode",
                color=0x5865F2,
                description=(
                    f"Canal onde será logado quem **configura**, **ativa** ou **desativa** o Token Mode.\n\n"
                    f"{SETTINGS}  **Canal Atual** — {ch_txt}\n\n"
                    f"{INFO}  Selecione um canal abaixo para definir, ou clique em **Limpar**."
                ),
            )
            await inter.response.send_message(
                embed=em, view=TokenLogsView(self.bot), ephemeral=True
            )

    @discord.ui.button(label="Remover Bot de Grupo", emoji="🚪", style=discord.ButtonStyle.danger, row=1)
    async def btn_remover_grupo(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        # Ordena do menor para o maior (para achar e remover grupos menores)
        guilds_ord = sorted(self.guilds_data, key=lambda x: x[0].member_count or 0)
        # Filtra fora os GUILD_IDS do dono
        externos = [(g, c) for g, c in guilds_ord if g.id not in config.GUILD_IDS]
        if not externos:
            em = _mod_emb(f"{ON}  Nenhum grupo externo.", config.COR_SUCESSO,
                          "O bot só está nos seus servidores.")
            return await inter.followup.send(embed=em, ephemeral=True)

        em = _mod_emb(f"🚪  Remover Bot de Grupo", config.COR_AVISO)
        em.description = (
            f"> Selecione abaixo o grupo de orgs (ordenado do **menor** para o **maior** em membros).\n"
            f"> O bot vai **sair de todos os servidores** do grupo escolhido.\n\n"
            f"> ⚠️ Seus servidores principais **não** aparecem aqui."
        )
        await inter.followup.send(
            embed=em,
            view=RemoverGrupoSelectView(self.bot, externos),
            ephemeral=True,
        )


class TokenLogsView(discord.ui.View):
    """View para definir o canal de log do Token Mode (global)."""
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Selecione o canal de log",
        min_values=1, max_values=1,
    )
    async def select_canal(self, inter: discord.Interaction, sel: discord.ui.ChannelSelect):
        from utils.database import token_mode_log_channel_set
        ch = sel.values[0]
        await asyncio.to_thread(token_mode_log_channel_set, int(ch.id))
        em = _ok("Canal de log definido!", f"> {DOT} Logs do Token Mode irão para {ch.mention}")
        await inter.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="Limpar Canal", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_limpar(self, inter: discord.Interaction, btn):
        from utils.database import token_mode_log_channel_set
        await asyncio.to_thread(token_mode_log_channel_set, None)
        em = _ok("Canal removido!", f"> Logs do Token Mode foram desativados.")
        await inter.response.send_message(embed=em, ephemeral=True)


class RemoverGrupoSelectView(discord.ui.View):
    """Select com grupos de até 25 orgs (menor→maior) para sair de todos de uma vez."""
    def __init__(self, bot, guilds_externos: list):
        super().__init__(timeout=300)
        self.bot = bot
        self.guilds_externos = guilds_externos
        options = []
        total = len(guilds_externos)
        for start in range(0, total, 25):
            end = min(start + 25, total)
            fatia = guilds_externos[start:end]
            menor = fatia[0][0].member_count or 0
            maior = fatia[-1][0].member_count or 0
            options.append(discord.SelectOption(
                label=f"Grupo {start + 1}–{end}",
                description=f"{end - start} servidores • {menor}–{maior} membros",
                value=str(start),
                emoji="🔴",
            ))
        self.select_grupo.options = options

    @discord.ui.select(placeholder="Selecione o grupo para remover o bot")
    async def select_grupo(self, inter, sel):
        await inter.response.defer(ephemeral=True)
        offset = int(sel.values[0])
        fatia  = self.guilds_externos[offset:offset + 25]

        em = _mod_emb(f"🚪  Prévia — {len(fatia)} Servidor(es) para Remover", config.COR_AVISO)
        em.description = (
            f"> ⚠️ Revise a lista abaixo antes de confirmar.\n"
            f"> O bot vai **sair de todos** estes servidores.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        # Monta lista paginada em fields (máx 25 por embed, divide em colunas)
        col_a, col_b = [], []
        for idx, (g, cfg) in enumerate(fatia):
            saldo = cfg.get("saldo", 0)
            membros = g.member_count or 0
            saldo_txt = f" • {saldo} salas" if saldo > 0 else ""
            linha = f"`{idx+1}.` **{g.name[:25]}**\n> 👥 {membros}{saldo_txt}"
            if idx % 2 == 0:
                col_a.append(linha)
            else:
                col_b.append(linha)

        if col_a:
            em.add_field(name="​", value="\n\n".join(col_a), inline=True)
        if col_b:
            em.add_field(name="​", value="\n\n".join(col_b), inline=True)

        total_membros = sum(g.member_count or 0 for g, _ in fatia)
        saldo_total   = sum(cfg.get("saldo", 0) for _, cfg in fatia)
        em.add_field(
            name="📊  Resumo do Grupo",
            value=(
                f"> 🖥️ **{len(fatia)}** servidores\n"
                f"> 👥 **{total_membros}** membros no total\n"
                f"> {CART} **{saldo_total}** salas em circulação (perderão acesso)\n"
                f"> ⚠️ Esta ação **não pode ser desfeita**"
            ),
            inline=False,
        )

        await inter.followup.send(
            embed=em,
            view=RemoverGrupoConfirmView(self.bot, fatia),
            ephemeral=True,
        )


class RemoverGrupoConfirmView(discord.ui.View):
    """Confirmação antes de sair de todos os servidores do grupo."""
    def __init__(self, bot, fatia: list):
        super().__init__(timeout=120)
        self.bot   = bot
        self.fatia = fatia

    @discord.ui.button(label="✅ Confirmar — Sair de Todos", style=discord.ButtonStyle.danger, row=0)
    async def btn_confirmar(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        for item in self.children:
            item.disabled = True
        await inter.edit_original_response(view=self)

        saiu, erros, nomes_saiu = 0, 0, []
        for guild_obj, _ in self.fatia:
            try:
                await guild_obj.leave()
                saiu += 1
                nomes_saiu.append(f"> {ON} {guild_obj.name}")
            except Exception:
                erros += 1
                nomes_saiu.append(f"> {OFF} {guild_obj.name} *(erro)*")

        em = _mod_emb(f"🚪  Remoção Concluída!", config.COR_SUCESSO if not erros else config.COR_AVISO)
        em.add_field(name=f"{ON}  Saiu de",    value=f"> **{saiu}** servidor(es)", inline=True)
        if erros:
            em.add_field(name=f"⚠️  Erros", value=f"> **{erros}** falhou", inline=True)

        # Mostra resultado por servidor (máx 20)
        if nomes_saiu:
            preview = "\n".join(nomes_saiu[:20])
            if len(nomes_saiu) > 20:
                preview += f"\n> ... +{len(nomes_saiu)-20} mais"
            em.add_field(name="📋  Detalhes", value=preview, inline=False)

        await inter.followup.send(embed=em, ephemeral=True)

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.secondary, row=0)
    async def btn_cancelar(self, inter, btn):
        em = _mod_emb(f"❌  Cancelado.", config.COR_AVISO, "Nenhum servidor foi afetado.")
        for item in self.children:
            item.disabled = True
        await inter.response.edit_message(embed=em, view=self)

    def _emb(self, banco):
        from utils.pix import get_banco_dividido_limite
        limite = get_banco_dividido_limite()
        _nomes = {
            "efi":       "Efí Bank",
            "mistic_15": "MisticPay 1,5%",
            "mistic_35": "MisticPay 0,35%",
            "dividido":  f"Dividido (< {limite} salas → 1,5% | ≥ {limite} salas → 0,35%)",
        }
        em = discord.Embed(title=f"💳  Banco PIX Atualizado!", color=config.COR_SUCESSO)
        em.add_field(name=f"{SETTINGS}  Banco Ativo", value=f"> **{_nomes.get(banco, banco)}**", inline=False)
        if banco == "dividido":
            em.add_field(
                name=f"{INFO}  Regra",
                value=f"> Abaixo de **{limite} salas** → MisticPay 1,5%\n> A partir de **{limite} salas** → MisticPay 0,35%",
                inline=False,
            )
        return em

    @discord.ui.button(label="MisticPay 1,5%", emoji="💳", style=discord.ButtonStyle.primary, row=0)
    async def btn_15(self, inter, btn):
        from utils.pix import set_banco_ativo
        set_banco_ativo("mistic_15")
        for item in self.children: item.disabled = True
        await inter.response.edit_message(embed=self._emb("mistic_15"), view=self)

    @discord.ui.button(label="MisticPay 0,35%", emoji="💳", style=discord.ButtonStyle.success, row=0)
    async def btn_35(self, inter, btn):
        from utils.pix import set_banco_ativo
        set_banco_ativo("mistic_35")
        for item in self.children: item.disabled = True
        await inter.response.edit_message(embed=self._emb("mistic_35"), view=self)

    @discord.ui.button(label="Efí Bank", emoji="🏦", style=discord.ButtonStyle.secondary, row=0)
    async def btn_efi(self, inter, btn):
        from utils.pix import set_banco_ativo
        set_banco_ativo("efi")
        for item in self.children: item.disabled = True
        await inter.response.edit_message(embed=self._emb("efi"), view=self)

    @discord.ui.button(label="Dividido (config por salas)", emoji="⚡", style=discord.ButtonStyle.danger, row=1)
    async def btn_dividido(self, inter, btn):
        from utils.pix import set_banco_ativo
        set_banco_ativo("dividido")
        for item in self.children: item.disabled = True
        await inter.response.edit_message(embed=self._emb("dividido"), view=self)

    @discord.ui.button(label="Config Limite Dividido", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_limite(self, inter, btn):
        await inter.response.send_modal(LimiteDivididoModal())


class LimiteDivididoModal(discord.ui.Modal, title="⚡ Config Banco Dividido"):
    limite = discord.ui.TextInput(
        label="Abaixo de X salas → MisticPay 1,5%",
        placeholder="Ex: 50  (abaixo de 50 → 1,5% | acima → 0,35%)",
        min_length=1,
        max_length=6,
    )

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        try:
            v = int(self.limite.value.strip())
            if v < 1: raise ValueError
        except ValueError:
            return await inter.followup.send(embed=_mod_emb("❌ Valor inválido.", config.COR_ERRO), ephemeral=True)
        from utils.pix import set_banco_dividido_limite, set_banco_ativo
        set_banco_dividido_limite(v)
        set_banco_ativo("dividido")
        em = discord.Embed(title=f"⚡  Banco Dividido Configurado!", color=config.COR_SUCESSO)
        em.add_field(name=f"{SETTINGS}  Regra", value=f"> Abaixo de **{v} salas** → MisticPay 1,5%\n> A partir de **{v} salas** → MisticPay 0,35%", inline=False)
        em.add_field(name=f"{ON}  Status", value=f"> Banco dividido ativado automaticamente.", inline=False)
        await inter.followup.send(embed=em, ephemeral=True)


class SalasOrigemView(discord.ui.View):
    """Painel /mod → Salas Criadas Config — escolhe origem, abre sub-view com API."""

    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        placeholder="Selecione a origem para configurar...",
        options=[
            discord.SelectOption(label="Venda",     value="venda",     emoji="🛒",
                                 description="Salas compradas via PIX"),
            discord.SelectOption(label="Ranking",   value="ranking",   emoji="🏆",
                                 description="Prêmios semanais do top 5"),
            discord.SelectOption(label="Indicação", value="indicacao", emoji="📨",
                                 description="Bônus de convites aprovados"),
        ],
    )
    async def sel_origem(self, inter, sel):
        from cogs.botconfig import get_api_por_origem
        origem = sel.values[0]
        atual  = get_api_por_origem(origem) or ""
        nomes  = {"venda": "Venda 🛒", "ranking": "Ranking 🏆", "indicacao": "Indicação 📨"}
        em = discord.Embed(
            title=f"🧩  Config API — {nomes.get(origem, origem)}",
            color=0x5865F2,
        )
        if atual == "1":
            cur = "**API 1** (F · salasff.com)"
        elif atual == "2":
            cur = "**API 2** (B · freefireapi.online)"
        else:
            cur = f"{OFF} Não definida — usa a **API principal** padrão"
        em.add_field(name=f"{DOT}  Status atual", value=f"> {cur}", inline=False)
        em.add_field(
            name=f"{INFO}  Escolha qual API criar essas salas",
            value=f"> {DOT} Defina explicitamente uma das duas APIs, **ou**\n"
                  f"> {DOT} use **Limpar** pra voltar a usar a API principal.",
            inline=False,
        )
        await inter.response.send_message(embed=em, view=SalasOrigemConfigView(origem), ephemeral=True)


class SalasOrigemConfigView(discord.ui.View):
    """Sub-view com 3 botões: API 1 / API 2 / Limpar."""

    def __init__(self, origem: str):
        super().__init__(timeout=300)
        self.origem = origem

    def _emb_ok(self, valor: str):
        nomes  = {"venda": "Venda", "ranking": "Ranking", "indicacao": "Indicação"}
        if valor == "1":
            api_lbl = "**API 1** (F · salasff.com)"
        elif valor == "2":
            api_lbl = "**API 2** (B · freefireapi.online)"
        else:
            api_lbl = "**API principal** padrão (não forçada)"
        em = discord.Embed(title=f"✅  Atualizado!", color=config.COR_SUCESSO)
        em.add_field(
            name=f"🧩  {nomes.get(self.origem)}",
            value=f"> Agora cria salas pela {api_lbl}.",
            inline=False,
        )
        em.add_field(
            name=f"{INFO}  Como funciona",
            value=(
                f"> {DOT} Quando o cliente criar uma sala, o bot identifica de qual "
                f"saldo está saindo (venda / ranking / indicação)\n"
                f"> {DOT} Se essa origem tiver uma API definida aqui, ela é usada — "
                f"caso contrário, usa a principal padrão."
            ),
            inline=False,
        )
        return em

    @discord.ui.button(label="API 1 — F", emoji="⚙️", style=discord.ButtonStyle.primary, row=0)
    async def btn_api1(self, inter, btn):
        from cogs.botconfig import set_api_por_origem
        set_api_por_origem(self.origem, "1")
        for item in self.children: item.disabled = True
        await inter.response.edit_message(embed=self._emb_ok("1"), view=self)

    @discord.ui.button(label="API 2 — B", emoji="⚙️", style=discord.ButtonStyle.success, row=0)
    async def btn_api2(self, inter, btn):
        from cogs.botconfig import set_api_por_origem
        set_api_por_origem(self.origem, "2")
        for item in self.children: item.disabled = True
        await inter.response.edit_message(embed=self._emb_ok("2"), view=self)

    @discord.ui.button(label="Limpar (usa principal)", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def btn_limpar(self, inter, btn):
        from cogs.botconfig import set_api_por_origem
        set_api_por_origem(self.origem, "")
        for item in self.children: item.disabled = True
        await inter.response.edit_message(embed=self._emb_ok(""), view=self)


class ApiView(discord.ui.View):
    """Botões para definir API Principal e Secundária."""
    def __init__(self):
        super().__init__(timeout=300)

    def _emb_resultado(self, principal: str):
        _nomes = {"1": "API 1 — F (salasff.com)", "2": "API 2 — B (freefireapi.online)"}
        secundario = "1" if principal == "2" else "2"
        em = discord.Embed(title=f"⚙️  API Atualizada!", color=config.COR_SUCESSO)
        em.add_field(name=f"⭐  Principal", value=f"> **{_nomes.get(principal)}**", inline=False)
        em.add_field(name=f"🔄  Secundária", value=f"> **{_nomes.get(secundario)}**", inline=False)
        em.add_field(
            name=f"{INFO}  Como funciona",
            value=(
                f"> Toda criação de sala tenta a **principal** primeiro.\n"
                f"> Se falhar e o **Anti-Caí** estiver ativo, usa a **secundária**.\n"
                f"> A próxima sala sempre tenta a **principal** novamente."
            ),
            inline=False,
        )
        return em

    @discord.ui.button(label="⭐ Principal: API 1 — F", emoji="⚙️", style=discord.ButtonStyle.primary, row=0)
    async def btn_api1_principal(self, inter, btn):
        from cogs.botconfig import set_api_principal
        set_api_principal("1")
        for item in self.children:
            item.disabled = True
        await inter.response.edit_message(embed=self._emb_resultado("1"), view=self)

    @discord.ui.button(label="⭐ Principal: API 2 — B", emoji="⚙️", style=discord.ButtonStyle.success, row=0)
    async def btn_api2_principal(self, inter, btn):
        from cogs.botconfig import set_api_principal
        set_api_principal("2")
        for item in self.children:
            item.disabled = True
        await inter.response.edit_message(embed=self._emb_resultado("2"), view=self)

    @discord.ui.button(label="Resgatar Key Mãe (API 2)", emoji="🗝️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_key(self, inter, btn):
        await inter.response.send_modal(ResgatarKeyMaeModal())

    @discord.ui.button(label="Resgatar Várias Keys (API 2)", emoji="📋", style=discord.ButtonStyle.primary, row=1)
    async def btn_keys_lista(self, inter, btn):
        await inter.response.send_modal(ResgatarVariasKeysMaeModal())

    @discord.ui.button(label="🛡️ Anti-Caí", style=discord.ButtonStyle.success, row=2)
    async def btn_anticai(self, inter, btn):
        from cogs.botconfig import get_anticai_ativo, set_anticai_ativo
        atual = get_anticai_ativo()
        set_anticai_ativo(not atual)
        novo = not atual
        em = discord.Embed(
            title=f"🛡️  Anti-Caí {'Ativado' if novo else 'Desativado'}!",
            color=config.COR_SUCESSO if novo else config.COR_AVISO,
        )
        em.add_field(name=f"{SETTINGS}  Status", value=f"> **{'✅ Ativado' if novo else '❌ Desativado'}**", inline=False)
        if novo:
            em.add_field(
                name=f"{INFO}  Como funciona",
                value=(
                    f"> Se a API **principal** falhar ao criar sala, o bot tenta a **secundária**.\n"
                    f"> A próxima sala sempre começa pela **principal** novamente."
                ),
                inline=False,
            )
        await inter.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="Ver Usos (API 2)", emoji=PE["stats"], style=discord.ButtonStyle.secondary, row=2)
    async def btn_usos(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from cogs.botconfig import get_api2_key, get_api2_keys_lista, get_api2_keys_idx
        import aiohttp

        lista = get_api2_keys_lista()
        idx_ativo = get_api2_keys_idx()

        async def _verificar(key: str) -> dict:
            """Retorna dict normalizado com: salas, total, ativa, invalida, msg_erro."""
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.post(
                        "https://salas-bot.freefireapi.online/api/verificar-key",
                        headers={"Authorization": key, "Content-Type": "application/json"},
                        json={},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r:
                        raw = await r.json(content_type=None)
            except Exception as ex:
                return {"salas": 0, "total": "?", "ativa": False, "invalida": False, "msg_erro": f"Sem resposta ({str(ex)[:40]})"}

            # API pode retornar string pura ou dict
            if not isinstance(raw, dict):
                return {"salas": 0, "total": "?", "ativa": False, "invalida": True, "msg_erro": str(raw)[:40]}

            # Erros da API (key inválida, bloqueada, etc.)
            erro_api = raw.get("erro") or raw.get("error") or raw.get("msg", "")
            status   = str(raw.get("status", "")).upper()
            invalida = status in ("KEY_INVALIDA", "KEY_EXPIRADA", "KEY_BLOQUEADA", "UNAUTHORIZED") or bool(raw.get("erro"))

            salas = raw.get("usos_restantes") or raw.get("remaining_uses") or 0
            total = raw.get("limite_usos")    or raw.get("total_uses")     or "?"
            ativa = raw.get("ativa", not invalida)

            try: salas = int(salas)
            except: salas = 0

            return {
                "salas":     salas,
                "total":     total,
                "ativa":     bool(ativa) and not invalida,
                "invalida":  invalida,
                "msg_erro":  str(erro_api)[:60] if invalida else "",
                "nome":      raw.get("nome", ""),
                "usados":    raw.get("usos_totais") or raw.get("used_count") or "?",
            }

        # ── Modo lista de keys ────────────────────────────────────────────────
        if lista:
            em = discord.Embed(title=f"📋  Keys API 2 — Status Geral", color=config.COR_INFO)
            total_salas = 0
            total_keys  = len(lista)
            keys_ok     = 0
            invalidas   = []  # keys a remover

            for idx, key in enumerate(lista):
                ativo = (idx == idx_ativo)
                d = await _verificar(key)

                if d["invalida"]:
                    icone     = "🔴"
                    status_txt = f"🔴 Key inválida/bloqueada"
                    invalidas.append(key)
                elif d["msg_erro"]:
                    icone     = "⚠️"
                    status_txt = f"⚠️ Sem resposta"
                elif d["salas"] > 0:
                    icone     = "✅"
                    status_txt = f"✅ **{d['salas']}** salas restantes / {d['total']} total"
                    total_salas += d["salas"]
                    keys_ok += 1
                else:
                    icone     = "🔴"
                    status_txt = f"🔴 **0** salas restantes / {d['total']} total"
                    invalidas.append(key)  # 0 salas = esgotada, remove também

                prefixo = "▶️" if ativo else f"`{idx+1}.`"
                em.add_field(
                    name=f"{prefixo} `{key[:16]}...`",
                    value=f"> {status_txt}",
                    inline=False,
                )

            em.add_field(
                name=f"📊  Resumo",
                value=(
                    f"> 🗝️ **{total_keys}** keys cadastradas  {DOT}  **{keys_ok}** com saldo\n"
                    f"> 🎯 **{total_salas}** salas disponíveis no total\n"
                    f"> ▶️ Key ativa: **{idx_ativo + 1}/{total_keys}**"
                ),
                inline=False,
            )
            view = LimparKeysView(invalidas) if invalidas else None
            return await inter.followup.send(embed=em, view=view, ephemeral=True) if view else await inter.followup.send(embed=em, ephemeral=True)

        # ── Modo key única (sem lista) ─────────────────────────────────────────
        key = get_api2_key()
        d   = await _verificar(key)

        em = discord.Embed(title=f"🗝️  Key Mãe — API 2", color=config.COR_INFO)

        if d["invalida"]:
            em.color = config.COR_ERRO
            em.add_field(name=f"❌  Status", value=f"> **Key inválida ou bloqueada**\n> `{d['msg_erro']}`", inline=False)
            em.add_field(name=f"{INFO}  Key", value=f"> `{key[:18]}...`", inline=False)
            em.add_field(name=f"💡  Solução", value=f"> Use **Resgatar Key Mãe** ou **Resgatar Várias Keys** para atualizar.", inline=False)
        elif d["msg_erro"]:
            em.color = config.COR_AVISO
            em.add_field(name=f"⚠️  Sem Resposta", value=f"> {d['msg_erro']}", inline=False)
        else:
            cor = config.COR_SUCESSO if d["salas"] > 100 else config.COR_AVISO if d["salas"] > 0 else config.COR_ERRO
            em.color = cor
            if d.get("nome"):
                em.add_field(name=f"{INFO}  Nome", value=f"> **{d['nome']}**", inline=True)
            em.add_field(name=f"{ON if d['ativa'] else OFF}  Status", value=f"> **{'Ativa' if d['ativa'] else 'Inativa'}**", inline=True)
            em.add_field(name=f"{STATS}  Usos", value=f"> **{d['usados']}** usados / **{d['total']}** total", inline=False)
            em.add_field(name=f"🎯  Salas Disponíveis", value=f"> **{d['salas']}** salas restantes", inline=False)

        await inter.followup.send(embed=em, ephemeral=True)


class LimparKeysView(discord.ui.View):
    """Aparece quando o 'Ver Usos' detecta keys inválidas/esgotadas. Permite limpá-las."""
    def __init__(self, invalidas: list):
        super().__init__(timeout=180)
        self.invalidas = invalidas
        # Atualiza label do botão com a quantidade
        self.btn_limpar.label = f"Limpar {len(invalidas)} Inválida(s)"

    @discord.ui.button(label="Limpar Inválidas", emoji="🧹", style=discord.ButtonStyle.danger, row=0)
    async def btn_limpar(self, inter, btn):
        await inter.response.defer(ephemeral=True)
        from cogs.botconfig import limpar_keys_invalidas
        removidas, ativa = limpar_keys_invalidas(self.invalidas)

        em = discord.Embed(title=f"🧹  Keys Limpas!", color=config.COR_SUCESSO)
        if removidas == 0:
            em.color = config.COR_AVISO
            em.description = f"{OFF} Nenhuma key foi removida."
        else:
            em.add_field(name=f"{OFF}  Removidas", value=f"> **{removidas}** key(s) inválida(s)/esgotada(s)", inline=False)
            if ativa:
                em.add_field(name=f"▶️  Key ativa agora", value=f"> `{ativa[:18]}...`", inline=False)
            else:
                em.add_field(name=f"⚠️  Atenção", value=f"> A lista ficou **vazia**! Adicione novas keys com **Resgatar Várias Keys**.", inline=False)
        for item in self.children:
            item.disabled = True
        await inter.edit_original_response(view=self)
        await inter.followup.send(embed=em, ephemeral=True)


class ResgatarKeyMaeModal(discord.ui.Modal, title="🗝️ Resgatar Key Mãe — API 2"):
    nova_key = discord.ui.TextInput(
        label="Nova Key (formato SALABOT-XXXX)",
        placeholder="SALABOT-XXXXXXXXXXXXXXXX",
        min_length=10,
        max_length=50,
    )

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        from cogs.botconfig import set_api2_key
        key = self.nova_key.value.strip()
        set_api2_key(key)
        em = discord.Embed(title=f"🗝️  Key Mãe Atualizada!", color=config.COR_SUCESSO)
        em.add_field(name=f"{SETTINGS}  Nova Key", value=f"> `{key}`", inline=False)
        em.add_field(name=f"{ON}  Status", value=f"> Key atualizada e ativa imediatamente.", inline=False)
        await inter.followup.send(embed=em, ephemeral=True)


class ResgatarVariasKeysMaeModal(discord.ui.Modal, title="📋 Resgatar Várias Keys — API 2"):
    lista_keys = discord.ui.TextInput(
        label="Keys (uma por linha)",
        style=discord.TextStyle.paragraph,
        placeholder="SALABOT-AAAAAAAAAAAAAAAA\nSALABOT-BBBBBBBBBBBBBBBB\nSALABOT-CCCCCCCCCCCCCCCC",
        min_length=10,
        max_length=4000,
    )

    async def on_submit(self, inter):
        await inter.response.defer(ephemeral=True)
        from cogs.botconfig import set_api2_keys_lista

        # Parseia: uma key por linha, remove vazios e espaços
        raw = self.lista_keys.value.strip()
        keys = [k.strip() for k in raw.splitlines() if k.strip()]

        if not keys:
            em = discord.Embed(title="❌  Nenhuma key válida!", color=config.COR_ERRO)
            em.description = "> Envie ao menos uma key, uma por linha."
            return await inter.followup.send(embed=em, ephemeral=True)

        set_api2_keys_lista(keys)

        em = discord.Embed(title=f"📋  Lista de Keys Atualizada!", color=config.COR_SUCESSO)
        em.add_field(
            name=f"{SETTINGS}  Keys cadastradas",
            value=f"> **{len(keys)} key(s)** salvas",
            inline=True,
        )
        em.add_field(
            name=f"▶️  Key ativa agora",
            value=f"> `{keys[0][:14]}...`  (1/{len(keys)})",
            inline=True,
        )
        # Mostra lista resumida
        linhas = [f"> `{idx+1}.` `{k[:14]}...`" for idx, k in enumerate(keys[:10])]
        if len(keys) > 10:
            linhas.append(f"> ... +{len(keys)-10} mais")
        em.add_field(
            name=f"🗝️  Lista",
            value="\n".join(linhas),
            inline=False,
        )
        em.add_field(
            name=f"{INFO}  Como funciona",
            value=(
                f"> Quando uma key **esgota ou fica inválida**, o bot passa automaticamente para a próxima.\n"
                f"> Use **Ver Usos** para ver o status de cada key e o total de salas disponíveis."
            ),
            inline=False,
        )
        await inter.followup.send(embed=em, ephemeral=True)


class EventoView(discord.ui.View):
    """Botões para ativar/desativar evento de bônus."""
    def __init__(self):
        super().__init__(timeout=300)

    def _emb_resultado(self, evento):
        _label = {None: f"{OFF} Desativado", "10+1": "🎉 10+1 ativo", "10+2": "🎉 10+2 ativo"}
        cor = config.COR_SUCESSO if evento else config.COR_AVISO
        em = discord.Embed(
            title=f"🎉  Evento Atualizado!",
            color=cor,
            
        )
        em.add_field(name=f"{SETTINGS}  Status", value=f"> **{_label.get(evento, str(evento))}**", inline=False)
        if evento:
            bonus = 1 if evento == "10+1" else 2
            em.add_field(
                name=f"{INFO}  Regra ativa",
                value=f"> A cada **10 salas** compradas → **+{bonus} sala(s) grátis** creditadas automaticamente",
                inline=False,
            )
        return em

    @discord.ui.button(label="10+1 Grátis", emoji="🎉", style=discord.ButtonStyle.success, row=0)
    async def btn_10mais1(self, inter, btn):
        from cogs.botconfig import set_evento_ativo
        set_evento_ativo("10+1")
        for item in self.children:
            item.disabled = True
        await inter.response.edit_message(embed=self._emb_resultado("10+1"), view=self)

    @discord.ui.button(label="10+2 Grátis", emoji="🎉", style=discord.ButtonStyle.primary, row=0)
    async def btn_10mais2(self, inter, btn):
        from cogs.botconfig import set_evento_ativo
        set_evento_ativo("10+2")
        for item in self.children:
            item.disabled = True
        await inter.response.edit_message(embed=self._emb_resultado("10+2"), view=self)

    @discord.ui.button(label="Desativar Evento", emoji=PE["off"], style=discord.ButtonStyle.danger, row=0)
    async def btn_off(self, inter, btn):
        from cogs.botconfig import set_evento_ativo
        set_evento_ativo(None)
        for item in self.children:
            item.disabled = True
        await inter.response.edit_message(embed=self._emb_resultado(None), view=self)


# ═══════════════════════════════════════════
#  Canal de Avaliação — moderação estrita
# ═══════════════════════════════════════════
def _avaliacao_emb(guild: discord.Guild) -> discord.Embed:
    """Embed que mostra a config atual de avaliação da guild."""
    from utils.database import guild_get_avaliacao
    av = guild_get_avaliacao(str(guild.id))
    canal = guild.get_channel(av["canal_id"]) if av["canal_id"] else None
    cargo = guild.get_role(av["cargo_id"]) if av["cargo_id"] else None
    status = f"{ON} **LIGADO**" if av["ativo"] else f"{OFF} **DESLIGADO**"

    em = discord.Embed(title=f"⭐  Canal de Avaliação", color=0xA855F7)
    em.set_thumbnail(url=guild.icon.url if guild.icon else None)
    em.description = (
        f"**Servidor:** {guild.name}\n\n"
        f"**Status:** {status}\n"
        f"**Canal:** {canal.mention if canal else '*Não definido*'}\n"
        f"**Cargo permitido:** {cargo.mention if cargo else '*Não definido*'}\n\n"
        "**Regras de moderação (quando ligado):**\n"
        f"{DOT} Só quem tem o cargo pode falar\n"
        f"{DOT} Não pode ter links\n"
        f"{DOT} Não pode ter imagens/anexos\n"
        f"{DOT} Mensagem **precisa conter `/`** (formato nota: 10/10, 9/10)\n\n"
        "*Admins do bot nunca são bloqueados.*"
    )
    em.set_footer(text=f"Guild ID: {guild.id}")
    return em


class AvaliacaoConfigView(discord.ui.View):
    """Painel ephemeral de configuração do canal de avaliação."""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Selecionar canal de avaliação...",
        min_values=1, max_values=1,
        row=0,
    )
    async def sel_canal(self, inter: discord.Interaction, select: discord.ui.ChannelSelect):
        from utils.database import guild_set_avaliacao, avaliacao_rebuild_cache
        canal = select.values[0]
        guild_set_avaliacao(str(self.guild_id), canal_id=canal.id)
        avaliacao_rebuild_cache()
        guild = inter.client.get_guild(self.guild_id)
        await inter.response.edit_message(embed=_avaliacao_emb(guild), view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Selecionar cargo permitido...",
        min_values=1, max_values=1,
        row=1,
    )
    async def sel_cargo(self, inter: discord.Interaction, select: discord.ui.RoleSelect):
        from utils.database import guild_set_avaliacao, avaliacao_rebuild_cache
        cargo = select.values[0]
        guild_set_avaliacao(str(self.guild_id), cargo_id=cargo.id)
        avaliacao_rebuild_cache()
        guild = inter.client.get_guild(self.guild_id)
        await inter.response.edit_message(embed=_avaliacao_emb(guild), view=self)

    @discord.ui.button(label="Ligar/Desligar", emoji="🔁", style=discord.ButtonStyle.success, row=2)
    async def toggle(self, i, b):
        from utils.database import guild_get_avaliacao, guild_set_avaliacao, avaliacao_rebuild_cache
        av = guild_get_avaliacao(str(self.guild_id))
        if not av["canal_id"] or not av["cargo_id"]:
            return await i.response.send_message(
                embed=discord.Embed(
                    description=f"{OFF} Configure canal **e** cargo antes de ligar.",
                    color=config.COR_ERRO,
                ),
                ephemeral=True,
            )
        guild_set_avaliacao(str(self.guild_id), ativo=not av["ativo"])
        avaliacao_rebuild_cache()
        guild = i.client.get_guild(self.guild_id)
        await i.response.edit_message(embed=_avaliacao_emb(guild), view=self)

    @discord.ui.button(label="Atualizar", emoji=PE["refresh"], style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, i, b):
        guild = i.client.get_guild(self.guild_id)
        await i.response.edit_message(embed=_avaliacao_emb(guild), view=self)


class _EscolherGuildAvaliacaoSelect(discord.ui.Select):
    """Select com todas as guilds pra escolher qual configurar avaliação."""
    def __init__(self, client):
        guilds = sorted(client.guilds, key=lambda g: g.name.lower())[:25]
        opcoes = []
        for g in guilds:
            opcoes.append(discord.SelectOption(
                label=g.name[:80],
                value=str(g.id),
                description=f"ID: {g.id}"[:90],
            ))
        if not opcoes:
            opcoes.append(discord.SelectOption(label="— nenhum servidor —", value="0"))
        super().__init__(
            placeholder="Escolha o servidor para configurar avaliação...",
            min_values=1, max_values=1, options=opcoes,
        )

    async def callback(self, inter: discord.Interaction):
        gid = int(self.values[0])
        if gid == 0:
            return await inter.response.edit_message(
                embed=discord.Embed(description="Nenhum servidor.", color=config.COR_AVISO),
                view=None,
            )
        guild = inter.client.get_guild(gid)
        if not guild:
            return await inter.response.edit_message(
                embed=discord.Embed(description="❌ Servidor não encontrado.", color=config.COR_ERRO),
                view=None,
            )
        await inter.response.edit_message(
            embed=_avaliacao_emb(guild),
            view=AvaliacaoConfigView(gid),
        )


class EscolherGuildAvaliacaoView(discord.ui.View):
    def __init__(self, client):
        super().__init__(timeout=180)
        self.add_item(_EscolherGuildAvaliacaoSelect(client))


# ═══════════════════════════════════════════
#  Canal Chat — só aceita comandos do bot
# ═══════════════════════════════════════════
def _chat_emb(guild: discord.Guild) -> discord.Embed:
    """Embed que mostra a config atual do canal Chat (só comandos)."""
    from utils.database import guild_get_chat
    ch = guild_get_chat(str(guild.id))
    canal = guild.get_channel(ch["canal_id"]) if ch["canal_id"] else None
    cargo = guild.get_role(ch["cargo_id"]) if ch["cargo_id"] else None
    status = f"{ON} **LIGADO**" if ch["ativo"] else f"{OFF} **DESLIGADO**"

    em = discord.Embed(title=f"💬  Chat (Só Comandos)", color=0x3498DB)
    em.set_thumbnail(url=guild.icon.url if guild.icon else None)
    em.description = (
        f"**Servidor:** {guild.name}\n\n"
        f"**Status:** {status}\n"
        f"**Canal:** {canal.mention if canal else '*Não definido*'}\n"
        f"**Cargo livre:** {cargo.mention if cargo else '*Nenhum (todos bloqueados)*'}\n\n"
        "**Regras de moderação (quando ligado):**\n"
        f"{DOT} Mensagem **precisa começar com `/` ou `.`**\n"
        f"{DOT} Quem tem o cargo livre pode falar normalmente\n"
        f"{DOT} Anexos (imagens/vídeos) são bloqueados\n\n"
        "*Admins do bot nunca são bloqueados.*"
    )
    em.set_footer(text=f"Guild ID: {guild.id}")
    return em


class ChatConfigView(discord.ui.View):
    """Painel ephemeral de configuração do canal Chat (só comandos)."""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Selecionar canal de chat...",
        min_values=1, max_values=1,
        row=0,
    )
    async def sel_canal(self, inter: discord.Interaction, select: discord.ui.ChannelSelect):
        from utils.database import guild_set_chat, chat_rebuild_cache
        canal = select.values[0]
        guild_set_chat(str(self.guild_id), canal_id=canal.id)
        chat_rebuild_cache()
        guild = inter.client.get_guild(self.guild_id)
        await inter.response.edit_message(embed=_chat_emb(guild), view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Selecionar cargo livre (opcional)...",
        min_values=1, max_values=1,
        row=1,
    )
    async def sel_cargo(self, inter: discord.Interaction, select: discord.ui.RoleSelect):
        from utils.database import guild_set_chat, chat_rebuild_cache
        cargo = select.values[0]
        guild_set_chat(str(self.guild_id), cargo_id=cargo.id)
        chat_rebuild_cache()
        guild = inter.client.get_guild(self.guild_id)
        await inter.response.edit_message(embed=_chat_emb(guild), view=self)

    @discord.ui.button(label="Ligar/Desligar", emoji="🔁", style=discord.ButtonStyle.success, row=2)
    async def toggle(self, i, b):
        from utils.database import guild_get_chat, guild_set_chat, chat_rebuild_cache
        ch = guild_get_chat(str(self.guild_id))
        if not ch["canal_id"]:
            return await i.response.send_message(
                embed=discord.Embed(
                    description=f"{OFF} Configure o **canal** antes de ligar.",
                    color=config.COR_ERRO,
                ),
                ephemeral=True,
            )
        guild_set_chat(str(self.guild_id), ativo=not ch["ativo"])
        chat_rebuild_cache()
        guild = i.client.get_guild(self.guild_id)
        await i.response.edit_message(embed=_chat_emb(guild), view=self)

    @discord.ui.button(label="Atualizar", emoji=PE["refresh"], style=discord.ButtonStyle.secondary, row=2)
    async def refresh(self, i, b):
        guild = i.client.get_guild(self.guild_id)
        await i.response.edit_message(embed=_chat_emb(guild), view=self)


class _EscolherGuildChatSelect(discord.ui.Select):
    """Select com todas as guilds pra escolher qual configurar chat."""
    def __init__(self, client):
        guilds = sorted(client.guilds, key=lambda g: g.name.lower())[:25]
        opcoes = []
        for g in guilds:
            opcoes.append(discord.SelectOption(
                label=g.name[:80],
                value=str(g.id),
                description=f"ID: {g.id}"[:90],
            ))
        if not opcoes:
            opcoes.append(discord.SelectOption(label="— nenhum servidor —", value="0"))
        super().__init__(
            placeholder="Escolha o servidor para configurar chat...",
            min_values=1, max_values=1, options=opcoes,
        )

    async def callback(self, inter: discord.Interaction):
        gid = int(self.values[0])
        if gid == 0:
            return await inter.response.edit_message(
                embed=discord.Embed(description="Nenhum servidor.", color=config.COR_AVISO),
                view=None,
            )
        guild = inter.client.get_guild(gid)
        if not guild:
            return await inter.response.edit_message(
                embed=discord.Embed(description="❌ Servidor não encontrado.", color=config.COR_ERRO),
                view=None,
            )
        await inter.response.edit_message(
            embed=_chat_emb(guild),
            view=ChatConfigView(gid),
        )


class EscolherGuildChatView(discord.ui.View):
    def __init__(self, client):
        super().__init__(timeout=180)
        self.add_item(_EscolherGuildChatSelect(client))


# ═══════════════════════════════════════════
#  Convidar Clientes — DM em massa
# ═══════════════════════════════════════════
async def _calcular_clientes_fora(bot, guild_id: int) -> tuple[list[str], discord.Guild | None]:
    """Retorna lista de user_ids com saldo > 0 que NÃO estão na guild_id."""
    from utils.database import usuarios_com_saldo
    guild = bot.get_guild(int(guild_id))
    if not guild:
        return [], None
    com_saldo = await asyncio.to_thread(usuarios_com_saldo)
    fora = []
    for uid in com_saldo:
        try:
            if not guild.get_member(int(uid)):
                fora.append(uid)
        except Exception:
            pass
    return fora, guild


class _EscolherGuildConvidarSelect(discord.ui.Select):
    """Select com guilds (limitado a 25 — primeiras alfabeticamente)."""
    def __init__(self, client):
        guilds = sorted(client.guilds, key=lambda g: g.name.lower())[:25]
        opcoes = []
        for g in guilds:
            opcoes.append(discord.SelectOption(
                label=g.name[:80],
                value=str(g.id),
                description=f"ID: {g.id}"[:90],
            ))
        if not opcoes:
            opcoes.append(discord.SelectOption(label="— nenhum servidor —", value="0"))
        super().__init__(
            placeholder="Escolha o servidor de referência...",
            min_values=1, max_values=1, options=opcoes,
        )

    async def callback(self, inter: discord.Interaction):
        gid = int(self.values[0])
        if gid == 0:
            return await inter.response.edit_message(
                embed=discord.Embed(description="Nenhum servidor.", color=config.COR_AVISO),
                view=None,
            )
        await inter.response.defer()
        fora, guild = await _calcular_clientes_fora(inter.client, gid)
        if not guild:
            return await inter.edit_original_response(
                embed=discord.Embed(description="❌ Servidor não encontrado.", color=config.COR_ERRO),
                view=None,
            )

        em = discord.Embed(
            title="📨  Convidar Clientes",
            color=0xFFD700,
        )
        em.set_thumbnail(url=guild.icon.url if guild.icon else None)
        em.description = (
            f"**Servidor de referência:** {guild.name}\n\n"
            f"{DOT} **Clientes com saldo total:** consulta no clique\n"
            f"{DOT} **Fora desse servidor:** **`{len(fora)}`** clientes\n\n"
            f"Clique em **Escrever Mensagem** para definir o conteúdo da DM.\n"
            f"O bot vai criar um convite permanente e anexar no fim da mensagem."
        )
        if not fora:
            em.add_field(
                name="ℹ️  Info",
                value="Todos os clientes com saldo já estão nesse servidor.",
                inline=False,
            )

        await inter.edit_original_response(
            embed=em, view=ConvidarClientesView(gid, fora),
        )


class EscolherGuildConvidarView(discord.ui.View):
    def __init__(self, client):
        super().__init__(timeout=180)
        self.add_item(_EscolherGuildConvidarSelect(client))


class ConvidarClientesView(discord.ui.View):
    """View com botão pra abrir modal de mensagem."""
    def __init__(self, guild_id: int, user_ids: list[str]):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.user_ids = user_ids
        # Desabilita botão se não tem ninguém
        if not user_ids:
            self.btn_escrever.disabled = True

    @discord.ui.button(
        label="Escrever Mensagem",
        emoji="✏️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def btn_escrever(self, inter: discord.Interaction, btn):
        await inter.response.send_modal(
            ConvidarMensagemModal(self.guild_id, self.user_ids)
        )

    @discord.ui.button(
        label="Cancelar",
        emoji="✖️",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def btn_cancelar(self, inter: discord.Interaction, btn):
        await inter.response.edit_message(
            embed=discord.Embed(description="Cancelado.", color=config.COR_AVISO),
            view=None,
        )


class ConvidarMensagemModal(discord.ui.Modal, title="✏️ Mensagem da DM"):
    titulo = discord.ui.TextInput(
        label="Título do embed",
        placeholder="Ex: 🎉 Nova Atualização Rolando!",
        max_length=100,
        required=True,
    )
    mensagem = discord.ui.TextInput(
        label="Mensagem (use \\n para quebra de linha)",
        placeholder="Olá! Temos uma nova atualização...",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True,
    )

    def __init__(self, guild_id: int, user_ids: list[str]):
        super().__init__()
        self.guild_id = guild_id
        self.user_ids = user_ids
        self.titulo.default = "🎉  Nova Atualização!"
        self.mensagem.default = (
            "Olá! Estamos com uma nova atualização rolando.\n"
            "Clique no botão abaixo para participar do nosso servidor!"
        )

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)

        guild = inter.client.get_guild(int(self.guild_id))
        if not guild:
            return await inter.followup.send("❌ Servidor não encontrado.", ephemeral=True)

        # Cria invite permanente
        try:
            canal_invite = guild.system_channel or next(
                (c for c in guild.text_channels if c.permissions_for(guild.me).create_instant_invite),
                None,
            )
            if not canal_invite:
                return await inter.followup.send(
                    "❌ Não achei nenhum canal pra criar convite no servidor.",
                    ephemeral=True,
                )
            invite = await canal_invite.create_invite(
                max_age=0, max_uses=0, unique=False,
                reason=f"Convite em massa por {inter.user}",
            )
            invite_url = invite.url
        except discord.Forbidden:
            return await inter.followup.send(
                "❌ Bot sem permissão pra criar convites no servidor.",
                ephemeral=True,
            )
        except Exception as ex:
            return await inter.followup.send(f"❌ Erro ao criar convite: {ex}", ephemeral=True)

        titulo = self.titulo.value
        mensagem = self.mensagem.value.replace("\\n", "\n")

        # Confirma antes de disparar
        em_preview = discord.Embed(
            title=titulo,
            description=f"{mensagem}\n\n🔗 **Link:** {invite_url}",
            color=0xFFD700,
        )
        em_preview.set_footer(text=f"Servidor: {guild.name}")

        em_confirm = discord.Embed(
            title="📋  Confirmar Envio",
            color=0x5865F2,
            description=(
                f"Será enviado para **`{len(self.user_ids)}`** clientes que têm saldo "
                f"e não estão no servidor **{guild.name}**.\n\n"
                f"⏱️ Tempo estimado: ~**{len(self.user_ids) * 1.5 / 60:.1f} min** "
                f"(rate limit safe)\n\n"
                f"**Prévia da DM:**"
            ),
        )

        await inter.followup.send(
            embeds=[em_confirm, em_preview],
            view=ConvidarConfirmView(self.guild_id, self.user_ids, titulo, mensagem, invite_url),
            ephemeral=True,
        )


class ConvidarConfirmView(discord.ui.View):
    def __init__(self, guild_id: int, user_ids: list, titulo: str, mensagem: str, invite_url: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_ids = user_ids
        self.titulo = titulo
        self.mensagem = mensagem
        self.invite_url = invite_url

    @discord.ui.button(label="Confirmar e Enviar", emoji="📨", style=discord.ButtonStyle.success, row=0)
    async def btn_enviar(self, inter: discord.Interaction, btn):
        await inter.response.edit_message(
            embed=discord.Embed(
                title="📨  Envio Iniciado!",
                description=(
                    f"Disparando DMs pra **`{len(self.user_ids)}`** clientes em background.\n"
                    f"Você receberá uma DM com o resumo quando acabar."
                ),
                color=0x57F287,
            ),
            view=None,
        )
        # Dispara em background
        asyncio.create_task(_enviar_dms_em_massa(
            inter.client, inter.user.id,
            self.guild_id, self.user_ids,
            self.titulo, self.mensagem, self.invite_url,
        ))

    @discord.ui.button(label="Cancelar", emoji="✖️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_cancelar(self, inter: discord.Interaction, btn):
        await inter.response.edit_message(
            embed=discord.Embed(description="Cancelado.", color=config.COR_AVISO),
            view=None,
        )


async def _enviar_dms_em_massa(bot, admin_id: int, guild_id: int, user_ids: list[str],
                                titulo: str, mensagem: str, invite_url: str):
    """Roda em background, envia DM pra cada user com rate limit."""
    import logging as _lg
    _ldm = _lg.getLogger("salasff.convidar")

    guild = bot.get_guild(int(guild_id))
    gname = guild.name if guild else f"Server {guild_id}"

    enviados = 0
    falhas_dm_off = 0
    falhas_outras = 0
    inicio = datetime.now()

    for uid in user_ids:
        try:
            user = bot.get_user(int(uid)) or await bot.fetch_user(int(uid))
            em = discord.Embed(
                title=titulo,
                description=f"{mensagem}\n\n🔗 **Link:** {invite_url}",
                color=0xFFD700,
            )
            em.set_footer(text=f"Servidor: {gname}")
            try:
                if guild and guild.icon:
                    em.set_thumbnail(url=guild.icon.url)
            except Exception:
                pass

            await user.send(embed=em)
            enviados += 1
        except discord.Forbidden:
            falhas_dm_off += 1
        except Exception as ex:
            _ldm.warning(f"[convidar] erro user {uid}: {ex}")
            falhas_outras += 1
        # Rate limit safe — 1.2 a 1.5s por DM
        await asyncio.sleep(1.3)

    duracao = (datetime.now() - inicio).total_seconds()
    _ldm.info(
        f"[convidar] guild={guild_id} fim: enviados={enviados} "
        f"dm_off={falhas_dm_off} outras={falhas_outras} duracao={duracao:.0f}s"
    )

    # Resumo pro admin
    try:
        admin = bot.get_user(int(admin_id)) or await bot.fetch_user(int(admin_id))
        em_resumo = discord.Embed(
            title="📨  Convite em Massa Concluído!",
            color=0x57F287 if enviados else 0xFEE75C,
        )
        em_resumo.add_field(name="✅ Enviados", value=f"`{enviados}`", inline=True)
        em_resumo.add_field(name="🔒 DM Fechada", value=f"`{falhas_dm_off}`", inline=True)
        em_resumo.add_field(name="❌ Outras Falhas", value=f"`{falhas_outras}`", inline=True)
        em_resumo.add_field(name="⏱️ Duração", value=f"`{duracao/60:.1f} min`", inline=True)
        em_resumo.add_field(name="🌐 Servidor", value=gname, inline=True)
        em_resumo.set_footer(text=f"Total testado: {len(user_ids)} clientes")
        await admin.send(embed=em_resumo)
    except Exception as ex:
        _ldm.warning(f"[convidar] erro resumo: {ex}")


class ModSelectView(discord.ui.View):
    """Select principal com grupos de 25 orgs cada."""
    def __init__(self, bot, guilds_data: list):
        super().__init__(timeout=300)
        self.bot = bot
        self.guilds_data = guilds_data
        # monta opções de grupos: "Orgs 1-25", "Orgs 26-50", etc
        options = []
        total = len(guilds_data)
        for start in range(0, total, 25):
            end = min(start + 25, total)
            tem_saldo = any(cfg.get("saldo", 0) > 0 for _, cfg in guilds_data[start:end])
            options.append(discord.SelectOption(
                label=f"Orgs {start + 1}–{end}",
                description=f"{end - start} servidor{'es' if end - start != 1 else ''}",
                value=str(start),
                emoji="🟢" if tem_saldo else "🔴",
            ))
        self.select_grupo.options = options

    @discord.ui.select(placeholder="Selecione o grupo de orgs")
    async def select_grupo(self, inter, sel):
        await inter.response.defer(ephemeral=True)
        offset = int(sel.values[0])
        grupo = self.guilds_data[offset:offset + 25]
        await inter.followup.send(
            embed=_mod_emb(f"{SETTINGS}  Orgs {offset + 1}–{offset + len(grupo)}"),
            view=ModGrupoView(self.bot, grupo, offset),
            ephemeral=True,
        )


class ModCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(*[discord.Object(id=gid) for gid in config.OWNER_GUILD_IDS])
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="mod", description="[ADMIN] Gerenciar saldo dos servidores.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_mod(self, inter):
        if inter.user.id not in config.ADMIN_IDS:
            return await inter.response.send_message(
                embed=_mod_emb("❌  Sem permissão.", config.COR_ERRO), ephemeral=True
            )

        await inter.response.defer(ephemeral=True)

        # Busca todos os guild_configs em uma única query
        from utils.database import get_db
        try:
            all_cfgs_raw = await asyncio.to_thread(
                lambda: get_db().table("guild_config").select("*").execute().data or []
            )
            cfg_map = {row["id"]: row for row in all_cfgs_raw}
        except Exception:
            cfg_map = {}

        _empty = {"saldo": 0, "cargo_sala_id": None, "canal_compras_id": None}
        guilds_data = [
            (guild_obj, cfg_map.get(str(guild_obj.id), _empty))
            for guild_obj in self.bot.guilds
        ]
        guilds_data.sort(key=lambda x: x[0].member_count or 0, reverse=True)

        total_guilds = len(guilds_data)
        total_salas  = sum(c.get("saldo", 0) for _, c in guilds_data)

        em = discord.Embed(
            title=f"{SETTINGS}  Painel de Servidores",
            color=0x5865F2,
            
        )
        em.add_field(name=f"{INFO}  Servidores", value=f"> **{total_guilds}**", inline=True)
        em.add_field(name=f"{CART}  Salas em circulação", value=f"> **{total_salas}**", inline=True)

        await inter.followup.send(embed=em, view=ModMenuView(self.bot, guilds_data), ephemeral=True)


async def setup(bot):
    await bot.add_cog(DevCog(bot))
    await bot.add_cog(ModCog(bot))
