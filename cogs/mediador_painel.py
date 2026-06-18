# cogs/mediador_painel.py - Painel do Mediador (cliente)
#
# Acessível via botão "Mediador PIX" no /painelglobal.
# Permite ao mediador:
#   1. Configurar o email Gmail que recebe os PIX
#   2. Ativar/desativar o sistema de confirmação automática
#   3. Ver/editar as guilds (orgs) que ele media
#   4. Ver tutorial de como configurar

import asyncio
import logging
import re

import discord
from discord.ext import commands

import config
from utils import pix_credentials as pc

_log = logging.getLogger("salasff.mediador_painel")


# ═══════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════

def _emb(titulo="", cor=0x5865F2, desc=""):
    em = discord.Embed(title=titulo, color=cor)
    if desc:
        em.description = desc
    return em

def _ok(t, d=""): return _emb(f"✅  {t}", 0x57F287, d)
def _err(t, d=""): return _emb(f"❌  {t}", 0xED4245, d)
def _info(t, d=""): return _emb(f"ℹ️  {t}", 0x5865F2, d)


def _get_mediador_doc(user_id: int | str) -> dict:
    """Carrega config do mediador no MongoDB."""
    try:
        from utils.database import _get_db
        doc = _get_db()["mediadores"].find_one({"_id": str(user_id)}) or {}
        return doc
    except Exception as e:
        _log.warning(f"[mediador] _get_mediador_doc: {e}")
        return {}


def _save_mediador_doc(user_id: int | str, doc: dict):
    """Salva config do mediador no MongoDB."""
    try:
        from utils.database import _get_db
        d = dict(doc)
        d.pop("_id", None)
        _get_db()["mediadores"].update_one(
            {"_id": str(user_id)},
            {"$set": d},
            upsert=True,
        )
    except Exception as e:
        _log.warning(f"[mediador] _save_mediador_doc: {e}")


async def _checkpoints_setup(user_id: int | str, guild_id: int | str | None) -> dict:
    """Calcula o estado dos 3 checkpoints do setup. Retorna dict com flags e detalhes."""
    from utils.database import token_mode_get
    cfg_tok = await asyncio.to_thread(token_mode_get, str(user_id))
    tem_token = bool(cfg_tok.get("token"))
    token_ativo = bool(cfg_tok.get("ativo"))

    # Gmail: o pix_polling busca creds nas guilds do user, então basta UMA guild
    # ter creds completas (client_id + client_secret + refresh_token).
    tem_gmail = False
    email_autorizado = ""
    try:
        # Tenta pela guild atual primeiro (mais rápido)
        if guild_id:
            creds = pc.get_creds_guild(int(guild_id), "gmail")
            if creds and creds.get("refresh_token"):
                tem_gmail = True
                email_autorizado = creds.get("email_autorizado", "")
        # Se não, varre as orgs do mediador
        if not tem_gmail:
            doc = _get_mediador_doc(user_id)
            for gid in doc.get("orgs", []):
                try:
                    creds = pc.get_creds_guild(int(gid), "gmail")
                    if creds and creds.get("refresh_token"):
                        tem_gmail = True
                        email_autorizado = creds.get("email_autorizado", "")
                        break
                except Exception:
                    continue
    except Exception as e:
        _log.warning(f"[checkpoints] gmail check: {e}")

    doc = _get_mediador_doc(user_id)
    orgs = doc.get("orgs", [])
    canais = doc.get("canais_extras", [])
    tem_servidor = bool(orgs or canais)

    return {
        "token": tem_token,
        "token_ativo": token_ativo,
        "gmail": tem_gmail,
        "email_autorizado": email_autorizado,
        "servidor": tem_servidor,
        "orgs": orgs,
        "canais": canais,
        "ativo": doc.get("ativo", False),
        "email_doc": doc.get("email", ""),  # email que o mediador disse usar (nem sempre = autorizado)
    }


async def _embed_status_mediador(user_id: int | str, user: discord.User | discord.Member,
                                   guild_id: int | str | None = None) -> discord.Embed:
    """Embed-wizard com checkpoints numerados. Mostra o que falta pra ativar."""
    cp = await _checkpoints_setup(user_id, guild_id)

    # Cor do embed reflete o progresso geral
    if cp["ativo"] and cp["token"] and cp["gmail"] and cp["servidor"]:
        cor = 0x57F287  # tudo verde + ativo
        status_top = "✅ **Sistema Ativo** — confirmando PIX automaticamente"
    elif cp["token"] and cp["gmail"] and cp["servidor"]:
        cor = 0xFEE75C  # tudo configurado mas pausado
        status_top = "⏸️ **Pronto pra ativar** — clique em **Ativar** abaixo"
    else:
        cor = 0x5865F2  # setup incompleto
        faltam = []
        if not cp["token"]: faltam.append("Token")
        if not cp["gmail"]: faltam.append("Gmail")
        if not cp["servidor"]: faltam.append("Servidor")
        status_top = f"⚙️ **Setup pendente** — falta configurar: **{', '.join(faltam)}**"

    em = discord.Embed(
        title="🏦  Painel do Mediador PIX",
        color=cor,
        description=(
            f"{status_top}\n\n"
            "Configure os 3 passos abaixo. Quando o jogador digitar `pg Nome` "
            "num canal `fila-*`, o bot confere seu Gmail e confirma o PIX automaticamente."
        ),
    )

    # ── Checkpoint 1: Token Discord ───────────────
    icon = "✅" if cp["token"] else "⚪"
    detalhe = "Token vinculado" if cp["token"] else "Configure pra o bot enviar mensagens com sua conta"
    em.add_field(
        name=f"{icon}  1. Token do Discord",
        value=detalhe,
        inline=False,
    )

    # ── Checkpoint 2: Gmail ───────────────────────
    icon = "✅" if cp["gmail"] else "⚪"
    if cp["gmail"]:
        detalhe = f"Autorizado: `{cp['email_autorizado']}`" if cp["email_autorizado"] else "Gmail OAuth autorizado"
    else:
        detalhe = "Configure o Google Cloud + autorize sua conta"
    em.add_field(
        name=f"{icon}  2. Gmail (OAuth)",
        value=detalhe,
        inline=False,
    )

    # ── Checkpoint 3: Servidor ────────────────────
    icon = "✅" if cp["servidor"] else "⚪"
    if cp["servidor"]:
        partes = []
        if cp["orgs"]:
            partes.append(f"{len(cp['orgs'])} org(s)")
        if cp["canais"]:
            partes.append(f"{len(cp['canais'])} canal/categoria")
        detalhe = " + ".join(partes)
        # Mostra IDs (limitado)
        if cp["orgs"]:
            detalhe += "\n" + "\n".join(f"• `{g}`" for g in cp["orgs"][:5])
            if len(cp["orgs"]) > 5:
                detalhe += f"\n_...e mais {len(cp['orgs'])-5}_"
    else:
        detalhe = "Adicione pelo menos uma org ou canal"
    em.add_field(
        name=f"{icon}  3. Servidor(es)",
        value=detalhe,
        inline=False,
    )

    em.set_footer(text=f"Mediador: {user} • ID: {user_id}")
    return em


# Compat: versão síncrona que usa o doc direto pra retro-compatibilidade
def _embed_status_mediador_sync(user_id: int | str, user: discord.User | discord.Member) -> discord.Embed:
    """Embed simples — usado em locais legacy onde não dá pra await."""
    doc = _get_mediador_doc(user_id)
    ativo = doc.get("ativo", False)
    email = doc.get("email", None)
    orgs = doc.get("orgs", [])

    cor = 0x57F287 if (ativo and email) else (0xFEE75C if email else 0x5865F2)

    em = discord.Embed(
        title="🏦  Painel do Mediador",
        color=cor,
        description=(
            "Configure seu sistema de **confirmação automática de PIX**.\n"
            "Quando um jogador digitar `pg Nome` num canal `fila-*` das suas orgs, "
            "o bot verifica o seu Gmail e confirma automaticamente."
        ),
    )

    if ativo and email:
        status = "✅ **Ativo** — confirmando PIX automaticamente"
    elif email:
        status = "⏸️ **Pausado** — Gmail configurado mas sistema desativado"
    else:
        status = "⚪ **Inativo** — configure o Gmail pra começar"
    em.add_field(name="📡 Status", value=status, inline=False)

    gmail_val = f"✅ `{email}`" if email else "⚪ Não configurado"
    em.add_field(name="📧 Gmail", value=gmail_val, inline=True)

    if orgs:
        orgs_txt = "\n".join(f"• `{g}`" for g in orgs[:10])
        if len(orgs) > 10:
            orgs_txt += f"\n_...e mais {len(orgs)-10}_"
    else:
        orgs_txt = "⚪ Nenhuma org cadastrada"
    em.add_field(name=f"🏢 Orgs ({len(orgs)})", value=orgs_txt, inline=True)

    canais_extras = doc.get("canais_extras", [])
    if canais_extras:
        canais_txt = "\n".join(f"• <#{c}>" for c in canais_extras[:10])
        if len(canais_extras) > 10:
            canais_txt += f"\n_...e mais {len(canais_extras)-10}_"
    else:
        canais_txt = "⚪ Nenhum cadastrado"
    em.add_field(name=f"📌 Canais/Categorias ({len(canais_extras)})", value=canais_txt, inline=True)

    em.set_footer(text=f"Mediador: {user} • ID: {user_id}")
    return em


# ═══════════════════════════════════════════
#  Modal: configurar Gmail do mediador
# ═══════════════════════════════════════════

class ModalGmailMediador(discord.ui.Modal, title="📧 Configurar Gmail"):
    email = discord.ui.TextInput(
        label="Seu email Gmail",
        placeholder="seuemail@gmail.com",
        style=discord.TextStyle.short,
        max_length=100,
        required=True,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id
        # Pre-preenche se já tiver
        doc = _get_mediador_doc(user_id)
        if doc.get("email"):
            self.email.default = doc["email"]

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        email_val = self.email.value.strip().lower()

        # Validação básica
        if not re.match(r"^[\w.+-]+@gmail\.com$", email_val):
            return await inter.followup.send(
                embed=_err("Email inválido", "Use um endereço `@gmail.com` válido."),
                ephemeral=True,
            )

        doc = _get_mediador_doc(self.user_id)
        doc["email"] = email_val

        # Verifica se o Gmail já está autorizado via OAuth (refresh_token salvo)
        # Busca nas guilds do user se tem creds do Gmail
        gmail_ok = False
        try:
            from cogs.token_mode import listar_guilds_user
            from utils.database import token_mode_get
            cfg_tok = await asyncio.to_thread(token_mode_get, str(self.user_id))
            token = cfg_tok.get("token")
            if token:
                guilds = await listar_guilds_user(token)
                for g in guilds:
                    creds = pc.get_creds_guild(int(g["id"]), "gmail")
                    if creds and creds.get("refresh_token"):
                        gmail_ok = True
                        break
        except Exception as e:
            _log.warning(f"[mediador] check gmail oauth: {e}")

        _save_mediador_doc(self.user_id, doc)

        if gmail_ok:
            await inter.followup.send(
                embed=_ok(
                    "Gmail configurado!",
                    f"Email: `{email_val}`\n\n"
                    "✅ OAuth já autorizado — você pode **Ativar** o sistema agora.",
                ),
                view=MediadorView(self.user_id),
                ephemeral=True,
            )
        else:
            await inter.followup.send(
                embed=_info(
                    "Email salvo — falta autorizar OAuth",
                    f"Email: `{email_val}`\n\n"
                    "⚠️ Você ainda precisa autorizar o Gmail no bot.\n"
                    "Use `/configurar_pix` → **Gmail** → siga o tutorial.\n\n"
                    "Depois que autorizar, volte aqui e clique em **Ativar**.",
                ),
                view=MediadorView(self.user_id),
                ephemeral=True,
            )


# ═══════════════════════════════════════════
#  Modal: adicionar org
# ═══════════════════════════════════════════

class ModalAdicionarCanal(discord.ui.Modal, title="📌 Canal, Categoria ou Tópico"):
    canal_id_input = discord.ui.TextInput(
        label="ID do canal, categoria OU tópico",
        placeholder="Categoria pega tudo. Tópico individual também rola se descoberta falhar.",
        style=discord.TextStyle.short,
        max_length=20,
        required=True,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, inter: discord.Interaction):
        cid = self.canal_id_input.value.strip()
        if not cid.isdigit():
            return await inter.response.send_message(
                embed=_err("ID inválido", "O ID deve conter apenas números."),
                ephemeral=True,
            )

        doc = _get_mediador_doc(self.user_id)
        canais = doc.get("canais_extras", [])
        if cid in canais:
            return await inter.response.send_message(
                embed=_info("Já cadastrado", f"O ID `{cid}` já está na sua lista."),
                ephemeral=True,
            )
        if len(canais) >= 30:
            return await inter.response.send_message(
                embed=_err("Limite atingido", "Máximo de 30 canais/categorias por mediador."),
                ephemeral=True,
            )

        canais.append(cid)
        doc["canais_extras"] = canais
        _save_mediador_doc(self.user_id, doc)

        await inter.response.send_message(
            embed=_ok(
                "Adicionado!",
                f"<#{cid}> salvo.\n"
                f"• **Categoria** → pega todos os canais filhos (e novos)\n"
                f"• **Canal** → lê threads dentro dele\n"
                f"• **Tópico/Thread** → processa direto (use se o bot não estiver "
                f"descobrindo threads sozinho)",
            ),
            view=MediadorView(self.user_id),
            ephemeral=True,
        )


class ModalRemoverCanal(discord.ui.Modal, title="🗑️ Remover Canal/Thread"):
    canal_id_input = discord.ui.TextInput(
        label="ID do canal pra remover",
        placeholder="Ex: 1497036782955397310",
        style=discord.TextStyle.short,
        max_length=20,
        required=True,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, inter: discord.Interaction):
        cid = self.canal_id_input.value.strip()
        doc = _get_mediador_doc(self.user_id)
        canais = doc.get("canais_extras", [])
        if cid not in canais:
            return await inter.response.send_message(
                embed=_info("Não encontrado", f"Canal `{cid}` não está na lista."),
                ephemeral=True,
            )
        canais.remove(cid)
        doc["canais_extras"] = canais
        _save_mediador_doc(self.user_id, doc)
        await inter.response.send_message(
            embed=_ok("Removido", f"Canal `{cid}` removido."),
            view=MediadorView(self.user_id),
            ephemeral=True,
        )
    guild_id_input = discord.ui.TextInput(
        label="ID da Org (servidor Discord)",
        placeholder="Ex: 1351284459214999562",
        style=discord.TextStyle.short,
        max_length=20,
        required=True,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, inter: discord.Interaction):
        gid = self.guild_id_input.value.strip()
        if not gid.isdigit():
            return await inter.response.send_message(
                embed=_err("ID inválido", "O ID deve conter apenas números."),
                ephemeral=True,
            )

        doc = _get_mediador_doc(self.user_id)
        orgs = doc.get("orgs", [])
        if gid in orgs:
            return await inter.response.send_message(
                embed=_info("Já cadastrada", f"A org `{gid}` já está na sua lista."),
                ephemeral=True,
            )
        if len(orgs) >= 20:
            return await inter.response.send_message(
                embed=_err("Limite atingido", "Máximo de 20 orgs por mediador."),
                ephemeral=True,
            )

        orgs.append(gid)
        doc["orgs"] = orgs
        _save_mediador_doc(self.user_id, doc)

        # Tenta pegar o nome da guild
        guild = inter.client.get_guild(int(gid))
        nome = f"**{guild.name}**" if guild else f"`{gid}`"

        await inter.response.send_message(
            embed=_ok("Org adicionada!", f"{nome} foi adicionada à sua lista de orgs."),
            view=MediadorView(self.user_id),
            ephemeral=True,
        )


# ═══════════════════════════════════════════
#  View principal do mediador
# ═══════════════════════════════════════════

class MediadorView(discord.ui.View):
    """Painel principal do mediador — wizard guiado de 3 passos."""

    def __init__(self, user_id: int | str):
        super().__init__(timeout=None)
        self.user_id = int(user_id)

    def _check_owner(self, inter: discord.Interaction) -> bool:
        return inter.user.id == self.user_id

    # ═══ Row 0: 3 passos de setup ═══════════════════════════════
    @discord.ui.button(label="1. Token Discord", emoji="🔑", style=discord.ButtonStyle.primary, row=0, custom_id="med:setup_token")
    async def btn_setup_token(self, inter: discord.Interaction, btn):
        if not self._check_owner(inter):
            return await inter.response.send_message(embed=_err("Este painel não é seu."), ephemeral=True)
        from cogs.token_mode import TokenModal
        await inter.response.send_modal(TokenModal(inter.user.id))

    @discord.ui.button(label="2. Gmail OAuth", emoji="📧", style=discord.ButtonStyle.primary, row=0, custom_id="med:setup_gmail")
    async def btn_setup_gmail(self, inter: discord.Interaction, btn):
        if not self._check_owner(inter):
            return await inter.response.send_message(embed=_err("Este painel não é seu."), ephemeral=True)
        # Abre sub-painel ephemeral
        guild_id = inter.guild_id
        em = _gmail_setup_embed(guild_id)
        await inter.response.send_message(embed=em, view=GmailSetupView(self.user_id, guild_id), ephemeral=True)

    @discord.ui.button(label="3. Servidores", emoji="🏢", style=discord.ButtonStyle.primary, row=0, custom_id="med:setup_servers")
    async def btn_setup_servers(self, inter: discord.Interaction, btn):
        if not self._check_owner(inter):
            return await inter.response.send_message(embed=_err("Este painel não é seu."), ephemeral=True)
        em = _servidores_setup_embed(self.user_id)
        await inter.response.send_message(embed=em, view=ServidoresSetupView(self.user_id), ephemeral=True)

    # ═══ Row 1: Ativar / Pausar / Atualizar ═════════════════════
    @discord.ui.button(label="Ativar", emoji="▶️", style=discord.ButtonStyle.success, row=1, custom_id="med:ativar")
    async def btn_ativar(self, inter: discord.Interaction, btn):
        if not self._check_owner(inter):
            return await inter.response.send_message(embed=_err("Este painel não é seu."), ephemeral=True)
        await inter.response.defer(ephemeral=True, thinking=True)

        cp = await _checkpoints_setup(inter.user.id, inter.guild_id)
        faltam = []
        if not cp["token"]: faltam.append("**🔑 Token do Discord**")
        if not cp["gmail"]: faltam.append("**📧 Gmail OAuth**")
        if not cp["servidor"]: faltam.append("**🏢 Servidor(es)**")

        if faltam:
            return await inter.followup.send(
                embed=_err(
                    "Setup incompleto",
                    "Termine de configurar antes de ativar:\n" + "\n".join(f"• {f}" for f in faltam),
                ),
                ephemeral=True,
            )

        # Ativa também o token mode (necessário pra polling rodar)
        try:
            from utils.database import token_mode_set_ativo
            await asyncio.to_thread(token_mode_set_ativo, str(inter.user.id), True)
        except Exception as e:
            _log.warning(f"[mediador] erro ativar token_mode: {e}")

        doc = _get_mediador_doc(inter.user.id)
        doc["ativo"] = True
        _save_mediador_doc(inter.user.id, doc)

        em = await _embed_status_mediador(inter.user.id, inter.user, inter.guild_id)
        await inter.followup.send(
            embed=_ok(
                "Sistema ativado! 🎉",
                "Quando jogadores digitarem `pg Nome` nos canais `fila-*` das suas orgs, "
                "o bot vai verificar seu Gmail e confirmar o PIX automaticamente.",
            ),
            ephemeral=True,
        )
        # Tenta atualizar o painel principal também
        try:
            await inter.edit_original_response(embed=em, view=MediadorView(inter.user.id))
        except Exception:
            pass

    @discord.ui.button(label="Pausar", emoji="⏸️", style=discord.ButtonStyle.danger, row=1, custom_id="med:desativar")
    async def btn_desativar(self, inter: discord.Interaction, btn):
        if not self._check_owner(inter):
            return await inter.response.send_message(embed=_err("Este painel não é seu."), ephemeral=True)
        doc = _get_mediador_doc(inter.user.id)
        if not doc.get("ativo"):
            return await inter.response.send_message(
                embed=_info("Já está pausado."),
                ephemeral=True,
            )
        doc["ativo"] = False
        _save_mediador_doc(inter.user.id, doc)
        try:
            from utils.database import token_mode_set_ativo
            await asyncio.to_thread(token_mode_set_ativo, str(inter.user.id), False)
        except Exception:
            pass
        em = await _embed_status_mediador(inter.user.id, inter.user, inter.guild_id)
        await inter.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="Atualizar", emoji="🔄", style=discord.ButtonStyle.secondary, row=1, custom_id="med:refresh")
    async def btn_refresh(self, inter: discord.Interaction, btn):
        if not self._check_owner(inter):
            return await inter.response.send_message(embed=_err("Este painel não é seu."), ephemeral=True)
        em = await _embed_status_mediador(inter.user.id, inter.user, inter.guild_id)
        await inter.response.edit_message(embed=em, view=self)

    # ═══ Row 2: Tutorial ════════════════════════════════════════
    @discord.ui.button(label="Tutorial Completo", emoji="📖", style=discord.ButtonStyle.secondary, row=2, custom_id="med:tutorial_full")
    async def btn_tutorial(self, inter: discord.Interaction, btn):
        em = _tutorial_pagina(0)
        await inter.response.send_message(embed=em, view=TutorialView(0), ephemeral=True)


# ═══════════════════════════════════════════
#  Sub-painel: Gmail OAuth
# ═══════════════════════════════════════════

def _gmail_setup_embed(guild_id: int | str | None) -> discord.Embed:
    """Embed do sub-painel Gmail OAuth."""
    cli_ok = False
    autorizado = False
    email = ""
    try:
        from utils.pix_gmail import _get_client_creds
        try:
            _get_client_creds()
            cli_ok = True
        except Exception:
            cli_ok = False
        if guild_id:
            creds = pc.get_creds_guild(int(guild_id), "gmail")
            autorizado = bool(creds and creds.get("refresh_token"))
            email = creds.get("email_autorizado", "") if creds else ""
    except Exception as e:
        _log.warning(f"[gmail_setup] {e}")

    cor = 0x57F287 if (cli_ok and autorizado) else 0x5865F2
    em = discord.Embed(
        title="📧  Configurar Gmail",
        color=cor,
        description=(
            "Pra confirmar PIX automaticamente o bot precisa **ler os emails** dos bancos "
            "que entram na sua conta Gmail. Isso requer 2 etapas:\n\n"
            "**A)** Cadastrar um **Cliente Google Cloud** (Client ID + Secret) — apenas 1 vez.\n"
            "**B)** **Autorizar sua conta Gmail** clicando em um link.\n\n"
            "*Não temos acesso ao seu email — apenas leitura dos PIX.*"
        ),
    )

    icon_a = "✅" if cli_ok else "⚪"
    em.add_field(
        name=f"{icon_a}  A. Cliente Google Cloud",
        value="Configurado" if cli_ok else "Falta cadastrar",
        inline=False,
    )
    icon_b = "✅" if autorizado else "⚪"
    if autorizado:
        val_b = f"Autorizado: `{email}`" if email else "Conta autorizada"
    else:
        val_b = "Falta autorizar a conta"
    em.add_field(
        name=f"{icon_b}  B. Conta Gmail",
        value=val_b,
        inline=False,
    )

    return em


class GmailSetupView(discord.ui.View):
    def __init__(self, user_id: int | str, guild_id: int | str | None):
        super().__init__(timeout=300)
        self.user_id = int(user_id)
        self.guild_id = guild_id

    def _check(self, inter):
        return inter.user.id == self.user_id

    @discord.ui.button(label="A. Cadastrar Cliente Google", emoji="⚙️", style=discord.ButtonStyle.primary, row=0)
    async def btn_client(self, inter: discord.Interaction, btn):
        if not self._check(inter):
            return await inter.response.send_message(embed=_err("Não é seu."), ephemeral=True)
        await inter.response.send_modal(ModalClientGoogleCloud())

    @discord.ui.button(label="B. Autorizar Conta", emoji="🔐", style=discord.ButtonStyle.success, row=0)
    async def btn_autorizar(self, inter: discord.Interaction, btn):
        if not self._check(inter):
            return await inter.response.send_message(embed=_err("Não é seu."), ephemeral=True)
        try:
            from utils.pix_gmail import gerar_url_autorizacao
            url = gerar_url_autorizacao()
        except Exception as e:
            return await inter.response.send_message(
                embed=_err(
                    "Cliente Google não configurado",
                    f"Clique em **A. Cadastrar Cliente Google** primeiro.\n\n`{e}`",
                ),
                ephemeral=True,
            )

        em = discord.Embed(
            title="🔐  Autorizar Conta Gmail",
            color=0x5865F2,
            description=(
                "**Passo a passo:**\n\n"
                "**1.** Clique no link abaixo\n"
                "**2.** Faça login com o Gmail que **recebe os PIX**\n"
                "**3.** Vai aparecer **\"Acesso bloqueado\"** ou **\"App não verificado\"** — é normal!\n"
                "**4.** Clique em **Avançado** (Advanced)\n"
                "**5.** Clique em **Acessar [seu app] (não seguro)** *(é seguro — é seu próprio app)*\n"
                "**6.** Clique em **Continuar** e depois **Permitir**\n"
                "**7.** Você será redirecionado pra uma página com um **código**\n"
                "**8.** Copie o código → volte aqui → clique em **Colar Código** abaixo\n\n"
                "⏱️  O código expira em **10 minutos**."
            ),
        )
        em.add_field(name="🔗 Link de autorização", value=f"[**Clique aqui pra autorizar →**]({url})", inline=False)
        await inter.response.send_message(embed=em, view=ColarCodigoView(self.user_id, self.guild_id), ephemeral=True)

    @discord.ui.button(label="Bancos Suportados", emoji="🏦", style=discord.ButtonStyle.secondary, row=1)
    async def btn_bancos(self, inter: discord.Interaction, btn):
        em = discord.Embed(
            title="🏦  Bancos detectados pelo bot",
            color=0x5865F2,
            description=(
                "O bot lê emails recebidos no Gmail e identifica o PIX dos seguintes bancos:\n\n"
                "🟣  **Nubank**\n"
                "🟠  **Inter**\n"
                "⚫  **C6 Bank**\n"
                "🟢  **PicPay**\n"
                "🔴  **Bradesco**\n"
                "🟠  **Itaú**\n"
                "🔴  **Santander**\n\n"
                "**Como ativar a notificação no banco?**\n"
                "Cada app de banco tem uma config diferente:\n"
                "• **Nubank** → Perfil → Configurações → Notificações por email → ativar PIX recebido\n"
                "• **Inter** → Configurações → Email → ativar comprovantes\n"
                "• **Itaú/Bradesco/Santander/etc** → ativar comprovantes por email no app\n\n"
                "Os emails precisam chegar no Gmail que você autorizar no passo **B**."
            ),
        )
        await inter.response.send_message(embed=em, ephemeral=True)


class ColarCodigoView(discord.ui.View):
    def __init__(self, user_id: int | str, guild_id: int | str | None):
        super().__init__(timeout=600)
        self.user_id = int(user_id)
        self.guild_id = guild_id

    @discord.ui.button(label="Colar Código", emoji="📋", style=discord.ButtonStyle.success, row=0)
    async def btn_colar(self, inter: discord.Interaction, btn):
        if inter.user.id != self.user_id:
            return await inter.response.send_message(embed=_err("Não é seu."), ephemeral=True)
        await inter.response.send_modal(ModalColarCodigoOAuth(self.guild_id))


class ModalClientGoogleCloud(discord.ui.Modal, title="⚙️ Cliente Google Cloud"):
    client_id = discord.ui.TextInput(
        label="Client ID",
        placeholder="123456789-xxxxx.apps.googleusercontent.com",
        style=discord.TextStyle.short,
        max_length=200,
        required=True,
    )
    client_secret = discord.ui.TextInput(
        label="Client Secret",
        placeholder="GOCSPX-xxxxxxxxxxxxx",
        style=discord.TextStyle.short,
        max_length=100,
        required=True,
    )

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        cid = self.client_id.value.strip()
        csec = self.client_secret.value.strip()
        try:
            from utils.pix_gmail import salvar_client_creds
            await asyncio.to_thread(salvar_client_creds, cid, csec)
            await inter.followup.send(
                embed=_ok(
                    "Cliente Google salvo! ⚙️",
                    f"Client ID: `{cid[:30]}…`\n\n"
                    "Agora clique em **B. Autorizar Conta** pra conectar seu Gmail.",
                ),
                ephemeral=True,
            )
        except Exception as e:
            await inter.followup.send(embed=_err("Erro ao salvar", f"`{e}`"), ephemeral=True)


class ModalColarCodigoOAuth(discord.ui.Modal, title="📋 Colar Código de Autorização"):
    codigo = discord.ui.TextInput(
        label="Código do Google",
        placeholder="4/0AeanS0a-xxxxxxxxxxxxxxxxxxxxxxxxxxx",
        style=discord.TextStyle.paragraph,
        max_length=500,
        required=True,
    )

    def __init__(self, guild_id: int | str | None):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        if not self.guild_id:
            return await inter.followup.send(
                embed=_err("Sem servidor", "Use este painel dentro de um servidor."),
                ephemeral=True,
            )

        try:
            from utils.pix_gmail import trocar_code_por_tokens, obter_email_gmail
            tokens = await trocar_code_por_tokens(auth_code=self.codigo.value.strip())
        except Exception as e:
            return await inter.followup.send(
                embed=_err(
                    "Código inválido ou expirado",
                    f"`{e}`\n\nGere um novo link em **B. Autorizar Conta** e tente de novo.",
                ),
                ephemeral=True,
            )

        # Captura email pra mostrar no painel
        try:
            email_autorizado = await obter_email_gmail(tokens)
            if email_autorizado:
                tokens["email_autorizado"] = email_autorizado
        except Exception as e:
            _log.warning(f"[gmail] não capturou email: {e}")

        try:
            await asyncio.to_thread(pc.set_creds_guild, int(self.guild_id), "gmail", tokens)
            ativo = await asyncio.to_thread(pc.get_banco_ativo, int(self.guild_id))
            if not ativo:
                await asyncio.to_thread(pc.set_banco_ativo, int(self.guild_id), "gmail")
        except Exception as e:
            return await inter.followup.send(embed=_err("Erro ao salvar", f"`{e}`"), ephemeral=True)

        # Salva também o email no doc do mediador (pra tracking)
        try:
            doc = _get_mediador_doc(inter.user.id)
            if tokens.get("email_autorizado"):
                doc["email"] = tokens["email_autorizado"]
                _save_mediador_doc(inter.user.id, doc)
        except Exception:
            pass

        # Testa conexão
        try:
            from utils.pix_gmail import testar_gmail
            ok, msg = await testar_gmail(tokens)
        except Exception as e:
            ok, msg = False, f"❌ Erro ao testar: {e}"

        email_str = tokens.get("email_autorizado", "Gmail")
        if ok:
            await inter.followup.send(
                embed=_ok(
                    "Gmail autorizado! 📧",
                    f"Conta vinculada: `{email_str}`\n{msg}\n\n"
                    "Agora volte ao painel e configure o **3. Servidores**.",
                ),
                ephemeral=True,
            )
        else:
            await inter.followup.send(
                embed=_emb("⚠️  Tokens salvos, mas teste falhou", 0xFEE75C, msg),
                ephemeral=True,
            )


# ═══════════════════════════════════════════
#  Sub-painel: Servidores
# ═══════════════════════════════════════════

def _servidores_setup_embed(user_id: int | str) -> discord.Embed:
    doc = _get_mediador_doc(user_id)
    orgs = doc.get("orgs", [])
    canais = doc.get("canais_extras", [])

    cor = 0x57F287 if (orgs or canais) else 0x5865F2
    em = discord.Embed(
        title="🏢  Servidores que você media",
        color=cor,
        description=(
            "Cadastre as **orgs** (servidores inteiros) ou **canais/categorias específicas** "
            "onde os jogadores digitam `pg Nome` pra confirmar PIX.\n\n"
            "**Recomendação:** se você media um servidor inteiro, adicione a **Org** (mais fácil). "
            "Se só media alguns canais específicos, adicione **Canal/Categoria**."
        ),
    )

    if orgs:
        txt = "\n".join(f"• `{g}`" for g in orgs[:10])
        if len(orgs) > 10:
            txt += f"\n_…e mais {len(orgs)-10}_"
        em.add_field(name=f"🏢 Orgs ({len(orgs)})", value=txt, inline=False)
    else:
        em.add_field(name="🏢 Orgs", value="⚪ Nenhuma cadastrada", inline=False)

    if canais:
        txt = "\n".join(f"• <#{c}>" for c in canais[:10])
        if len(canais) > 10:
            txt += f"\n_…e mais {len(canais)-10}_"
        em.add_field(name=f"📌 Canais/Categorias ({len(canais)})", value=txt, inline=False)
    else:
        em.add_field(name="📌 Canais/Categorias", value="⚪ Nenhum cadastrado", inline=False)

    return em


class ServidoresSetupView(discord.ui.View):
    def __init__(self, user_id: int | str):
        super().__init__(timeout=300)
        self.user_id = int(user_id)

    def _check(self, inter):
        return inter.user.id == self.user_id

    @discord.ui.button(label="Adicionar Org", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def btn_add_org(self, inter: discord.Interaction, btn):
        if not self._check(inter):
            return await inter.response.send_message(embed=_err("Não é seu."), ephemeral=True)
        await inter.response.send_modal(ModalAdicionarOrg(inter.user.id))

    @discord.ui.button(label="Remover Org", emoji="➖", style=discord.ButtonStyle.danger, row=0)
    async def btn_rem_org(self, inter: discord.Interaction, btn):
        if not self._check(inter):
            return await inter.response.send_message(embed=_err("Não é seu."), ephemeral=True)
        doc = _get_mediador_doc(inter.user.id)
        orgs = doc.get("orgs", [])
        if not orgs:
            return await inter.response.send_message(embed=_info("Nenhuma org cadastrada."), ephemeral=True)
        await inter.response.send_message(
            embed=_info("Selecione a org pra remover:"),
            view=RemoverOrgView(inter.user.id, orgs, inter.client),
            ephemeral=True,
        )

    @discord.ui.button(label="Adicionar Canal/Categoria", emoji="📌", style=discord.ButtonStyle.success, row=1)
    async def btn_add_canal(self, inter: discord.Interaction, btn):
        if not self._check(inter):
            return await inter.response.send_message(embed=_err("Não é seu."), ephemeral=True)
        await inter.response.send_modal(ModalAdicionarCanal(inter.user.id))

    @discord.ui.button(label="Remover Canal/Categoria", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_rem_canal(self, inter: discord.Interaction, btn):
        if not self._check(inter):
            return await inter.response.send_message(embed=_err("Não é seu."), ephemeral=True)
        await inter.response.send_modal(ModalRemoverCanal(inter.user.id))


# ═══════════════════════════════════════════
#  Tutorial paginado
# ═══════════════════════════════════════════

_TUTORIAL_PAGINAS = [
    {
        "title": "📖  Tutorial — Visão Geral",
        "color": 0x5865F2,
        "desc": (
            "Esse painel configura **confirmação automática de PIX** pelo Discord.\n\n"
            "**Como funciona:**\n"
            "1️⃣  Jogador faz PIX pra você\n"
            "2️⃣  Banco te manda email confirmando\n"
            "3️⃣  Jogador digita `pg Nome` num canal `fila-*`\n"
            "4️⃣  O bot lê seu Gmail, encontra o PIX e **confirma com sua conta** automaticamente\n\n"
            "**O que você precisa preparar:**\n"
            "• Conta do Discord (vai precisar do **token**)\n"
            "• Conta Gmail que recebe emails dos bancos\n"
            "• Conta no **Google Cloud Console** (gratuita)\n"
            "• Servidor/canal onde acontecem as filas\n\n"
            "*Use os botões abaixo pra ver cada passo em detalhe.*"
        ),
        "footer": "Página 1 de 5",
    },
    {
        "title": "🔑  Passo 1 — Token do Discord",
        "color": 0x5865F2,
        "desc": (
            "O bot envia mensagens de confirmação **com sua própria conta**, então precisa do seu token.\n\n"
            "**Como pegar o token:**\n"
            "1.  Abra o Discord no **navegador** (não no app)\n"
            "2.  Faça login normalmente\n"
            "3.  Aperte **F12** ou **Ctrl+Shift+I** pra abrir o DevTools\n"
            "4.  Vá na aba **Network**\n"
            "5.  Mande qualquer mensagem ou clique em qualquer canal\n"
            "6.  Procure uma requisição com nome tipo `science` ou `messages`\n"
            "7.  Em **Headers** → role até achar **`authorization`**\n"
            "8.  Copie o valor (começa com `MTQ...`, `NTQ...` ou `OD...`)\n\n"
            "**Onde colar:** botão **🔑 1. Token Discord** no painel principal.\n\n"
            "⚠️  **Nunca compartilhe seu token com ninguém além desse bot.** "
            "É como uma senha — quem tem ele entra na sua conta."
        ),
        "footer": "Página 2 de 5",
    },
    {
        "title": "⚙️  Passo 2A — Cliente Google Cloud",
        "color": 0x4285F4,
        "desc": (
            "Pra o bot ler seu Gmail, você precisa criar um **cliente OAuth** no Google Cloud (1 vez só).\n\n"
            "**Passo a passo:**\n"
            "**1.** Vá em https://console.cloud.google.com\n"
            "**2.** Crie um projeto novo (botão no canto superior, **\"Novo Projeto\"**)\n"
            "**3.** Menu lateral → **APIs e Serviços** → **Biblioteca**\n"
            "**4.** Procure **Gmail API** → clique → **Ativar**\n"
            "**5.** Volte em **APIs e Serviços** → **Tela de permissão OAuth**\n"
            "  • Tipo: **Externo** → Criar\n"
            "  • Nome do app: o que quiser (ex: `FmedPIX`)\n"
            "  • Email de suporte: o seu\n"
            "  • Salvar e continuar (pode pular escopos e usuários teste)\n"
            "**6.** Menu → **APIs e Serviços** → **Credenciais**\n"
            "**7.** **+ Criar credenciais** → **ID do cliente OAuth**\n"
            "  • Tipo: **App da Web** (Web Application)\n"
            "  • URIs de redirecionamento autorizados: adicione `https://fmed.com.br/oauth/gmail/callback`\n"
            "    *(ou a URL que o bot mostrar — peça pro admin)*\n"
            "**8.** Copie o **Client ID** e **Client Secret**\n"
            "**9.** Cole no botão **A. Cadastrar Cliente Google** do bot"
        ),
        "footer": "Página 3 de 5 • Demora ~5 minutos no Console Google",
    },
    {
        "title": "🔐  Passo 2B — Autorizar sua Conta Gmail",
        "color": 0x4285F4,
        "desc": (
            "Aqui você dá permissão pro bot **ler** os emails de PIX (não escreve, não apaga, só lê).\n\n"
            "**Passo a passo:**\n"
            "**1.** No painel, clique em **B. Autorizar Conta**\n"
            "**2.** Clique no link gerado\n"
            "**3.** Faça login com o Gmail **que recebe os emails dos PIX**\n"
            "**4.** Vai aparecer uma tela:\n"
            "  > **\"O Google não verificou este app\"**\n"
            "  > **\"Acesso bloqueado: erro de autorização\"**\n"
            "  > 🟦 Isso é normal — é seu próprio app, ainda não foi publicado.\n\n"
            "**5.** Clique em **Avançado** (ou **Mostrar opções avançadas**)\n"
            "**6.** Vai aparecer um link em letras pequenas tipo:\n"
            "  > *Acessar [nome do seu app] (não seguro)*\n"
            "**7.** **Clique nesse link** — é o caminho certo, não é perigoso\n"
            "**8.** Tela de permissão → **Continuar** → **Permitir**\n"
            "**9.** Você será redirecionado pra uma página com um **código** (começa com `4/0...`)\n"
            "**10.** **Copie o código** → volte ao painel → clique em **Colar Código**\n"
            "**11.** Cole o código → ✅ pronto\n\n"
            "⏱️  O código expira em **10 minutos** — se demorar, gere um novo link."
        ),
        "footer": "Página 4 de 5",
    },
    {
        "title": "🏢  Passo 3 — Servidores e Bancos",
        "color": 0x57F287,
        "desc": (
            "**Cadastrar onde o bot vai funcionar:**\n\n"
            "No botão **🏢 3. Servidores** você adiciona:\n"
            "• **Org** = ID do servidor inteiro (recomendado se você media tudo)\n"
            "• **Canal/Categoria** = ID de um canal/categoria específica\n"
            "• **Tópico** = ID de uma thread específica (caso especial)\n\n"
            "**Como pegar o ID:**\n"
            "1.  No Discord, ative o **Modo Desenvolvedor** (Configurações → Avançado)\n"
            "2.  Clique com botão direito no servidor/canal → **Copiar ID**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**🏦 Bancos suportados:**\n"
            "Nubank, Inter, C6, PicPay, Bradesco, Itaú, Santander.\n\n"
            "**⚙️ Importante: ative os emails de comprovante no app do banco!**\n"
            "Cada banco tem uma config diferente. Procure por **\"notificação por email\"** "
            "ou **\"comprovante de PIX recebido\"** nas configurações do app.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "**▶️  Quando os 3 passos estiverem ✅, clique em ATIVAR** no painel principal."
        ),
        "footer": "Página 5 de 5 • Setup completo!",
    },
]


def _tutorial_pagina(idx: int) -> discord.Embed:
    idx = max(0, min(idx, len(_TUTORIAL_PAGINAS) - 1))
    p = _TUTORIAL_PAGINAS[idx]
    em = discord.Embed(title=p["title"], color=p["color"], description=p["desc"])
    em.set_footer(text=p["footer"])
    return em


class TutorialView(discord.ui.View):
    def __init__(self, idx: int = 0):
        super().__init__(timeout=600)
        self.idx = idx
        self._update_buttons()

    def _update_buttons(self):
        self.btn_prev.disabled = (self.idx <= 0)
        self.btn_next.disabled = (self.idx >= len(_TUTORIAL_PAGINAS) - 1)
        self.btn_prev.label = f"◀  Anterior"
        self.btn_next.label = f"Próximo  ▶"

    @discord.ui.button(label="◀  Anterior", style=discord.ButtonStyle.secondary, row=0)
    async def btn_prev(self, inter: discord.Interaction, btn):
        self.idx -= 1
        self._update_buttons()
        await inter.response.edit_message(embed=_tutorial_pagina(self.idx), view=self)

    @discord.ui.button(label="Próximo  ▶", style=discord.ButtonStyle.primary, row=0)
    async def btn_next(self, inter: discord.Interaction, btn):
        self.idx += 1
        self._update_buttons()
        await inter.response.edit_message(embed=_tutorial_pagina(self.idx), view=self)

    @discord.ui.select(
        placeholder="Pular pra um passo específico…",
        options=[
            discord.SelectOption(label="Visão Geral", value="0", emoji="📖"),
            discord.SelectOption(label="1. Token Discord", value="1", emoji="🔑"),
            discord.SelectOption(label="2A. Cliente Google", value="2", emoji="⚙️"),
            discord.SelectOption(label="2B. Autorizar Gmail", value="3", emoji="🔐"),
            discord.SelectOption(label="3. Servidores + Bancos", value="4", emoji="🏢"),
        ],
        row=1,
    )
    async def sel_pagina(self, inter: discord.Interaction, sel):
        self.idx = int(sel.values[0])
        self._update_buttons()
        await inter.response.edit_message(embed=_tutorial_pagina(self.idx), view=self)


# ═══════════════════════════════════════════
#  View de remover org
# ═══════════════════════════════════════════

class RemoverOrgView(discord.ui.View):
    def __init__(self, user_id: int, orgs: list, client):
        super().__init__(timeout=120)
        self.user_id = user_id

        options = []
        for gid in orgs[:25]:
            guild = client.get_guild(int(gid))
            nome = guild.name if guild else gid
            options.append(discord.SelectOption(label=nome[:100], value=gid, description=f"ID: {gid}"))

        sel = discord.ui.Select(placeholder="Escolha a org pra remover...", options=options)
        sel.callback = self._on_select
        self.add_item(sel)
        self.sel = sel

    async def _on_select(self, inter: discord.Interaction):
        if inter.user.id != self.user_id:
            return await inter.response.send_message(embed=_err("Este painel não é seu."), ephemeral=True)
        gid = self.sel.values[0]
        doc = _get_mediador_doc(inter.user.id)
        orgs = doc.get("orgs", [])
        if gid in orgs:
            orgs.remove(gid)
        doc["orgs"] = orgs
        _save_mediador_doc(inter.user.id, doc)
        await inter.response.edit_message(
            embed=_ok("Org removida", f"Org `{gid}` removida da sua lista."),
            view=None,
        )


# ═══════════════════════════════════════════
#  Função pública pra abrir o painel
# ═══════════════════════════════════════════

async def abrir_painel_mediador(inter: discord.Interaction):
    """Chamado pelo botão no /painelglobal. Abre painel ephemeral do mediador."""
    user_id = inter.user.id
    guild_id = inter.guild_id
    em = await _embed_status_mediador(user_id, inter.user, guild_id)
    view = MediadorView(user_id)

    if inter.response.is_done():
        await inter.followup.send(embed=em, view=view, ephemeral=True)
    else:
        await inter.response.send_message(embed=em, view=view, ephemeral=True)


# ═══════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════

class MediadorPainelCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


async def setup(bot):
    await bot.add_cog(MediadorPainelCog(bot))
