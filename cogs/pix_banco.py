# cogs/pix_panel.py — Painel de configuração de credenciais PIX por guild
#
# Cada admin de servidor (mediador) configura suas próprias credenciais bancárias.
# As credenciais ficam isoladas por guild_id e criptografadas em disco.
#
# Fluxo:
#   1. Admin abre /painelglobal e clica em "PIX Banco"
#   2. Vê o status atual da guild (qual banco ativo, quais configurados)
#   3. Escolhe um banco → modal aparece pedindo as credenciais
#   4. Pode testar conexão antes de salvar
#   5. Pode definir qual banco fica como "ativo" (qual o bot vai usar)

import asyncio
import io
import logging
import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import pix_credentials as pc

_log = logging.getLogger("salasff.pix_panel")


# ═══════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════

def _emb(titulo="", cor=0x5865F2, desc=""):
    em = discord.Embed(title=titulo, color=cor)
    if desc:
        em.description = desc
    return em


def _ok(t, d=""): return _emb(f"✅  {t}", getattr(config, "COR_SUCESSO", 0x57F287), d)
def _err(t, d=""): return _emb(f"❌  {t}", getattr(config, "COR_ERRO", 0xED4245), d)
def _info(t, d=""): return _emb(f"ℹ️  {t}", 0x5865F2, d)


def _is_server_admin(inter: discord.Interaction) -> bool:
    """Admin do servidor Discord (não do bot)."""
    if not inter.guild:
        return False
    perms = inter.user.guild_permissions
    return perms.administrator or perms.manage_guild


# ═══════════════════════════════════════════
#  Embed de status da guild
# ═══════════════════════════════════════════

def _embed_status(guild_id: int | str) -> discord.Embed:
    gid = int(guild_id) if isinstance(guild_id, str) else guild_id
    resumo = pc.resumo_guild(gid)
    ativo = resumo.get("banco_ativo")
    configs = resumo.get("configurados") or []

    em = discord.Embed(
        title="💰  Configuração PIX do Servidor",
        color=0x5865F2,
        description=(
            "Configure as credenciais bancárias **deste servidor**. O bot usa essas "
            "credenciais pra **consultar PIX recebidos** (não cria cobrança) e "
            "confirmar pagamentos automaticamente quando jogadores digitam `pg Nome`."
        )
    )

    # Banco ativo
    if ativo:
        em.add_field(
            name="🎯 Banco Ativo",
            value=f"**{pc.BANCOS_LABEL[ativo]}**",
            inline=True,
        )
    else:
        em.add_field(
            name="🎯 Banco Ativo",
            value="*Nenhum — configure abaixo*",
            inline=True,
        )

    # Status por banco
    linhas = []
    for banco in pc.BANCOS_VISIVEIS:
        label = pc.BANCOS_LABEL[banco]
        if banco in configs:
            extra = ""
            if banco == "efi":
                extra = " + cert.pem ✅" if resumo.get("tem_cert_efi") else " ⚠️ falta cert.pem"
            elif banco == "gmail":
                # Mostra o email autorizado se foi capturado
                creds = pc.get_creds_guild(gid, "gmail")
                email = creds.get("email_autorizado") if creds else None
                if email:
                    extra = f" → `{email}`"
            mark = "✅" if banco == ativo else "🟢"
            linhas.append(f"{mark} **{label}**{extra}")
        else:
            linhas.append(f"⚪ {label} — *não configurado*")

    em.add_field(
        name="📋 Status",
        value="\n".join(linhas),
        inline=False,
    )

    if resumo.get("atualizado_em"):
        em.set_footer(text=f"Última atualização: {resumo['atualizado_em']}")

    return em


# ═══════════════════════════════════════════
#  Modais por banco
# ═══════════════════════════════════════════

class MercadoPagoModal(discord.ui.Modal, title="🔵 Configurar Mercado Pago"):
    access_token = discord.ui.TextInput(
        label="Access Token",
        placeholder="APP_USR-1234567890-... (Credenciais de Produção)",
        style=discord.TextStyle.short,
        max_length=200,
        required=True,
    )

    async def on_submit(self, inter: discord.Interaction):
        token = self.access_token.value.strip()
        if not token.startswith(("APP_USR-", "TEST-")):
            return await inter.response.send_message(
                embed=_err("Token inválido", "O Access Token deve começar com `APP_USR-` (produção) ou `TEST-` (sandbox)."),
                ephemeral=True,
            )
        try:
            pc.set_creds_guild(inter.guild_id, "mercadopago", {"access_token": token})
            if not pc.get_banco_ativo(inter.guild_id):
                pc.set_banco_ativo(inter.guild_id, "mercadopago")
            await inter.response.send_message(
                embed=_ok("Mercado Pago configurado", "Credenciais salvas e criptografadas."),
                view=PixBancoView(inter.guild_id),
                ephemeral=True,
            )
        except Exception as e:
            _log.error(f"[mp] erro: {e}")
            await inter.response.send_message(embed=_err("Erro ao salvar", f"`{e}`"), ephemeral=True)


class PagBankModal(discord.ui.Modal, title="🟠 Configurar PagBank"):
    token = discord.ui.TextInput(
        label="Token de Acesso",
        placeholder="Token gerado em Integrações → Tokens",
        style=discord.TextStyle.short,
        max_length=300,
        required=True,
    )

    async def on_submit(self, inter: discord.Interaction):
        tok = self.token.value.strip()
        if len(tok) < 20:
            return await inter.response.send_message(
                embed=_err("Token muito curto", "Verifique se copiou o token completo."),
                ephemeral=True,
            )
        try:
            pc.set_creds_guild(inter.guild_id, "pagbank", {"token": tok})
            if not pc.get_banco_ativo(inter.guild_id):
                pc.set_banco_ativo(inter.guild_id, "pagbank")
            await inter.response.send_message(
                embed=_ok("PagBank configurado", "Credenciais salvas e criptografadas."),
                view=PixBancoView(inter.guild_id),
                ephemeral=True,
            )
        except Exception as e:
            _log.error(f"[pagbank] erro: {e}")
            await inter.response.send_message(embed=_err("Erro ao salvar", f"`{e}`"), ephemeral=True)


class EFIModal(discord.ui.Modal, title="🟢 Configurar EFI Pay"):
    client_id = discord.ui.TextInput(
        label="Client ID",
        placeholder="Client_Id_xxxxxxxxxxxxxxxx",
        style=discord.TextStyle.short,
        max_length=200,
        required=True,
    )
    client_secret = discord.ui.TextInput(
        label="Client Secret",
        placeholder="Client_Secret_xxxxxxxxxxxxxxxx",
        style=discord.TextStyle.short,
        max_length=200,
        required=True,
    )

    async def on_submit(self, inter: discord.Interaction):
        cid = self.client_id.value.strip()
        csec = self.client_secret.value.strip()
        try:
            pc.set_creds_guild(inter.guild_id, "efi", {
                "client_id": cid,
                "client_secret": csec,
            })
            if not pc.get_banco_ativo(inter.guild_id):
                pc.set_banco_ativo(inter.guild_id, "efi")
            tem_cert = pc.has_cert_efi(inter.guild_id)
            msg = "Credenciais salvas. "
            msg += "✅ Certificado já carregado." if tem_cert else "⚠️ **Falta o cert.pem** — use `/configurar_pix_efi_cert` pra fazer upload."
            await inter.response.send_message(
                embed=_ok("EFI Pay configurado", msg),
                view=PixBancoView(inter.guild_id),
                ephemeral=True,
            )
        except Exception as e:
            _log.error(f"[efi] erro: {e}")
            await inter.response.send_message(embed=_err("Erro ao salvar", f"`{e}`"), ephemeral=True)


class MacroDroidModal(discord.ui.Modal, title="📱 Configurar MacroDroid (Notificações)"):
    site_url = discord.ui.TextInput(
        label="URL do site Fmed",
        placeholder="https://fmediador.discloud.app",
        style=discord.TextStyle.short,
        max_length=200,
        required=True,
        default="https://fmediador.discloud.app",
    )

    async def on_submit(self, inter: discord.Interaction):
        url = self.site_url.value.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            return await inter.response.send_message(
                embed=_err("URL inválida", "Deve começar com `http://` ou `https://`."),
                ephemeral=True,
            )
        try:
            pc.set_creds_guild(inter.guild_id, "macrodroid", {"site_url": url})
            if not pc.get_banco_ativo(inter.guild_id):
                pc.set_banco_ativo(inter.guild_id, "macrodroid")
            await inter.response.send_message(
                embed=_ok(
                    "MacroDroid configurado",
                    f"URL salva: `{url}`\n\nAgora configure o MacroDroid no celular pra fazer "
                    f"POST em `{url}/pix` quando notificação Nubank chegar."
                ),
                view=PixBancoView(inter.guild_id),
                ephemeral=True,
            )
        except Exception as e:
            _log.error(f"[macrodroid] erro: {e}")
            await inter.response.send_message(embed=_err("Erro ao salvar", f"`{e}`"), ephemeral=True)


# ═══════════════════════════════════════════
#  Tutorial inline (links pra pegar credenciais)
# ═══════════════════════════════════════════

TUTORIAIS = {
    "mercadopago": (
        "**Como pegar o Access Token do Mercado Pago:**\n"
        "1. Acesse [mercadopago.com.br/developers/panel](https://www.mercadopago.com.br/developers/panel)\n"
        "2. Vá em **Suas integrações** → cria uma nova ou usa existente\n"
        "3. Em **Credenciais de produção** → copia o **Access Token** (`APP_USR-...`)\n"
        "4. Cola no botão abaixo"
    ),
    "pagbank": (
        "**Como pegar o token do PagBank:**\n"
        "1. Acesse sua conta PagBank em [acesso.pagseguro.uol.com.br](https://acesso.pagseguro.uol.com.br/)\n"
        "2. Vá em **Integrações → Tokens de API**\n"
        "3. Gera um novo token com permissões de **leitura de extrato**\n"
        "4. Cola no botão abaixo"
    ),
    "efi": (
        "**Como pegar credenciais do EFI Pay:**\n"
        "1. Acesse sua conta EFI → **Integração → API Pix**\n"
        "2. Cria uma aplicação → ativa scopes `pix.read` e `cob.read`\n"
        "3. Copia **Client_Id** e **Client_Secret**\n"
        "4. **Baixa o certificado P12** e converte pra PEM:\n"
        "   `openssl pkcs12 -in cert.p12 -out cert.pem -nodes`\n"
        "5. Cola as credenciais no botão abaixo\n"
        "6. Faça upload do `cert.pem` com `/configurar_pix_efi_cert`"
    ),
    "macrodroid": (
        "**Como configurar MacroDroid:**\n"
        "1. Você precisa do **site Fmed** rodando (separado, na Discloud)\n"
        "2. URL padrão: `https://fmediador.discloud.app`\n"
        "3. No celular, instale **MacroDroid** (Play Store)\n"
        "4. Crie macro com trigger `Notificação Nubank contém 'recebeu'` e action `HTTP POST` pro `{url}/pix`\n"
        "5. Conceda permissão de **acesso a notificações** + **bateria sem otimização**"
    ),
    "gmail": (
        "**Como autorizar Gmail (lê PIX por email, sem celular):**\n\n"
        "É **bem simples** — você não precisa criar nada no Google Cloud, é só autorizar.\n\n"
        "**Passo a passo:**\n"
        "1. `/configurar_pix` → botão **Gmail**\n"
        "2. Clique no link **\"Autorizar Gmail\"** que vai aparecer\n"
        "3. Faça login com o **Gmail que recebe os PIX dos bancos**\n"
        "4. Pode aparecer aviso *\"Google não verificou este app\"* — é normal:\n"
        "   • Clique em **Avançado**\n"
        "   • Depois em **Acessar (não seguro)**\n"
        "5. Clique em **Permitir** quando pedir acesso aos emails\n"
        "6. Você vai ser redirecionado pra uma página do Fmed com um **código**\n"
        "7. Clique em **Copiar** e use o comando `/configurar_pix_gmail_code`\n"
        "8. Cole o código → ✅\n\n"
        "**O que o bot faz com isso:**\n"
        "Lê emails de bancos brasileiros (Nubank, Inter, C6, PicPay, Bradesco, Itaú, Santander) "
        "pra detectar PIX recebidos. Quando jogadores digitam `pg Nome`, o bot procura nos "
        "últimos emails se tem PIX com aquele nome.\n\n"
        "⚠️ O Gmail só precisa **receber** os emails dos bancos — não precisa fazer nada além disso."
    ),
}


# ═══════════════════════════════════════════
#  Main View do painel
# ═══════════════════════════════════════════



async def _enviar_link_autorizacao_gmail(inter: discord.Interaction):
    """Mostra ao admin o link pra autorizar Gmail."""
    from utils.pix_gmail import gerar_url_autorizacao
    try:
        url = gerar_url_autorizacao()
    except ValueError:
        # Client creds não configuradas — pede pra configurar primeiro
        return await inter.response.send_message(
            embed=_err(
                "OAuth não configurado",
                "Clique em **⚙️ Config OAuth** primeiro e informe o `client_id` e `client_secret` "
                "do seu projeto no Google Cloud.",
            ),
            ephemeral=True,
        )

    em = discord.Embed(
        title="📧 Autorizar Gmail",
        color=0x5865F2,
        description=(
            "**Como funciona:**\n"
            "1. Clique em **Autorizar Gmail** abaixo\n"
            "2. Faça login com o Gmail que **recebe os PIX**\n"
            "3. Clique em **Avançado → Acessar (não seguro)** se aparecer aviso\n"
            "4. Clique em **Permitir** na tela de permissões\n"
            "5. Você será redirecionado pra uma página do Fmed com um código\n"
            "6. Copie o código e use `/configurar_pix_gmail_code`\n\n"
            "⚠️ O código expira em **10 minutos**.\n"
            "💡 O Gmail só precisa receber emails dos bancos (Nubank, Inter, C6, etc.)."
        ),
    )
    em.add_field(name="🔗 Link de autorização", value=f"[Clique aqui pra autorizar]({url})", inline=False)
    await inter.response.send_message(embed=em, ephemeral=True)


class ModalOAuthGlobal(discord.ui.Modal, title="⚙️ Configurar OAuth do Gmail"):
    client_id = discord.ui.TextInput(
        label="Client ID",
        placeholder="619293914559-xxxxx.apps.googleusercontent.com",
        style=discord.TextStyle.short,
        max_length=200,
        required=True,
    )
    client_secret = discord.ui.TextInput(
        label="Client Secret",
        placeholder="GOCSPX-xxxxx",
        style=discord.TextStyle.short,
        max_length=100,
        required=True,
    )

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True, thinking=True)
        cid  = self.client_id.value.strip()
        csec = self.client_secret.value.strip()
        try:
            from utils.pix_gmail import salvar_client_creds
            await asyncio.to_thread(salvar_client_creds, cid, csec)
            await inter.followup.send(
                embed=_ok(
                    "OAuth configurado!",
                    f"Client ID salvo: `{cid[:30]}…`\n\n"
                    "Agora clique em **Gmail** no painel pra autorizar a conta.",
                ),
                ephemeral=True,
            )
        except Exception as e:
            await inter.followup.send(embed=_err("Erro ao salvar", f"`{e}`"), ephemeral=True)


async def _enviar_link_autorizacao_gmail(inter: discord.Interaction):
    """Mostra ao admin o link pra autorizar Gmail."""
    from utils.pix_gmail import gerar_url_autorizacao
    try:
        url = gerar_url_autorizacao()
    except ValueError as e:
        return await inter.response.send_message(
            embed=_err(
                "OAuth não configurado",
                f"`{e}`\n\nClique em **⚙️ Config OAuth** primeiro.",
            ),
            ephemeral=True,
        )

    em = discord.Embed(
        title="📧 Autorizar Gmail",
        color=0x5865F2,
        description=(
            "**Como funciona:**\n"
            "1. Clique em **Autorizar Gmail** abaixo\n"
            "2. Faça login com o Gmail que **recebe os PIX**\n"
            "3. Clique em **Avançado → Acessar (não seguro)** se aparecer aviso\n"
            "4. Clique em **Permitir** na tela de permissões\n"
            "5. Você será redirecionado pra uma página do Fmed com um código\n"
            "6. Copie o código e use `/configurar_pix_gmail_code`\n\n"
            "⚠️ O código expira em **10 minutos**.\n"
            "💡 O Gmail só precisa receber emails dos bancos (Nubank, Inter, C6, etc.)."
        ),
    )
    em.add_field(name="🔗 Link de autorização", value=f"[Clique aqui pra autorizar]({url})", inline=False)
    await inter.response.send_message(embed=em, ephemeral=True)

class PixBancoView(discord.ui.View):
    """View principal do painel PIX (persistente - sobrevive a restart)."""

    def __init__(self, guild_id: int | str | None = None):
        super().__init__(timeout=None)  # persistente
        self._guild_id = int(guild_id) if guild_id else None

    def _gid(self, inter: discord.Interaction) -> int:
        if self._guild_id is not None:
            return self._guild_id
        return inter.guild_id

    async def _check_admin(self, inter: discord.Interaction) -> bool:
        if not _is_server_admin(inter):
            await inter.response.send_message(
                embed=_err("Sem permissão", "Apenas admins do servidor podem configurar."),
                ephemeral=True,
            )
            return False
        return True

    # ── Linha 0: bancos ─────────────────────────────────────
    # NOTA: os botões de Mercado Pago, PagBank, EFI Pay e MacroDroid foram
    # ocultados temporariamente. O código deles (modais, lógica, persistência)
    # continua intacto — pra reexibir basta restaurar os @discord.ui.button
    # correspondentes e adicionar o banco em pc.BANCOS_VISIVEIS.

    @discord.ui.button(label="Gmail", emoji="📧", style=discord.ButtonStyle.primary, row=0, custom_id="pixpanel:btn_gmail")
    async def btn_gmail(self, inter: discord.Interaction, btn):
        if not await self._check_admin(inter): return
        await _enviar_link_autorizacao_gmail(inter)

    @discord.ui.button(label="Config OAuth", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0, custom_id="pixpanel:btn_oauth_global")
    async def btn_oauth_global(self, inter: discord.Interaction, btn):
        if not await self._check_admin(inter): return
        await inter.response.send_modal(ModalOAuthGlobal())

    # ── Linha 1: ações ──────────────────────────────────────
    @discord.ui.button(label="Trocar Banco Ativo", emoji="🔄", style=discord.ButtonStyle.secondary, row=1, custom_id="pixpanel:trocar_ativo")
    async def btn_trocar(self, inter: discord.Interaction, btn):
        if not await self._check_admin(inter): return
        gid = self._gid(inter)
        configs = pc.listar_bancos_configurados(gid)
        if len(configs) < 2:
            return await inter.response.send_message(
                embed=_err("Configure pelo menos 2 bancos antes de trocar."),
                ephemeral=True,
            )
        await inter.response.send_message(
            embed=_info("Selecione o banco ativo", "O bot vai usar este banco pra confirmar PIX."),
            view=TrocarAtivoView(gid, configs),
            ephemeral=True,
        )

    @discord.ui.button(label="Testar Conexão", emoji="🔌", style=discord.ButtonStyle.success, row=1, custom_id="pixpanel:testar")
    async def btn_testar(self, inter: discord.Interaction, btn):
        if not await self._check_admin(inter): return
        gid = self._gid(inter)
        ativo = pc.get_banco_ativo(gid)
        if not ativo:
            return await inter.response.send_message(
                embed=_err("Sem banco ativo", "Configure um banco primeiro."),
                ephemeral=True,
            )
        await inter.response.defer(ephemeral=True, thinking=True)
        creds = pc.get_creds_guild(gid, ativo)
        if not creds:
            return await inter.followup.send(
                embed=_err("Credenciais não encontradas"), ephemeral=True
            )

        # Adiciona cert_path pro EFI
        if ativo == "efi":
            if not pc.has_cert_efi(gid):
                return await inter.followup.send(
                    embed=_err("Falta cert.pem", "Use `/configurar_pix_efi_cert` pra fazer upload."),
                    ephemeral=True,
                )
            creds = {**creds, "cert_path": pc.cert_path_efi(gid)}

        # MacroDroid não tem teste real (só checa URL)
        if ativo == "macrodroid":
            url = creds.get("site_url", "")
            return await inter.followup.send(
                embed=_ok("MacroDroid configurado", f"URL: `{url}`\n\nO teste real depende do celular do mediador estar com MacroDroid ativo."),
                ephemeral=True,
            )

        try:
            from utils.pix_consulta import testar_credenciais
            ok, msg = await testar_credenciais(ativo, creds)
            if ok:
                await inter.followup.send(embed=_ok("Conexão OK", msg), ephemeral=True)
            else:
                await inter.followup.send(embed=_err("Falha na conexão", msg), ephemeral=True)
        except Exception as e:
            _log.error(f"[testar] {e}")
            await inter.followup.send(embed=_err("Erro ao testar", f"`{e}`"), ephemeral=True)

    @discord.ui.button(label="Remover Banco", emoji="🗑️", style=discord.ButtonStyle.danger, row=1, custom_id="pixpanel:remover")
    async def btn_remover(self, inter: discord.Interaction, btn):
        if not await self._check_admin(inter): return
        gid = self._gid(inter)
        configs = pc.listar_bancos_configurados(gid)
        if not configs:
            return await inter.response.send_message(
                embed=_err("Nada pra remover."),
                ephemeral=True,
            )
        await inter.response.send_message(
            embed=_info("Selecione qual banco remover", "Esta ação não pode ser desfeita."),
            view=RemoverView(gid, configs),
            ephemeral=True,
        )

    # ── Linha 2: tutorial / atualizar ───────────────────────
    @discord.ui.button(label="Tutorial", emoji="📖", style=discord.ButtonStyle.secondary, row=2, custom_id="pixpanel:btn_tutorial")
    async def btn_tutorial(self, inter: discord.Interaction, btn):
        if not await self._check_admin(inter): return
        await inter.response.send_message(
            embed=_info("Como pegar as credenciais", "Selecione o banco abaixo:"),
            view=TutorialView(),
            ephemeral=True,
        )

    @discord.ui.button(label="Atualizar Status", emoji="🔄", style=discord.ButtonStyle.secondary, row=2, custom_id="pixpanel:refresh")
    async def btn_refresh(self, inter: discord.Interaction, btn):
        if not await self._check_admin(inter): return
        gid = self._gid(inter)
        await inter.response.edit_message(
            embed=_embed_status(gid),
            view=self,
        )


# ═══════════════════════════════════════════
#  Sub-views
# ═══════════════════════════════════════════

class TrocarAtivoView(discord.ui.View):
    def __init__(self, guild_id: int, configs: list[str]):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        sel = discord.ui.Select(
            placeholder="Escolha o banco ativo...",
            options=[
                discord.SelectOption(
                    label=pc.BANCOS_LABEL[b],
                    value=b,
                    emoji={"efi": "🟢", "mercadopago": "🔵", "pagbank": "🟠", "macrodroid": "📱"}.get(b),
                )
                for b in configs
            ],
        )
        sel.callback = self._on_select
        self.add_item(sel)
        self.sel = sel

    async def _on_select(self, inter: discord.Interaction):
        if not _is_server_admin(inter):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        banco = self.sel.values[0]
        pc.set_banco_ativo(self.guild_id, banco)
        await inter.response.edit_message(
            embed=_ok(
                f"Banco ativo: {pc.BANCOS_LABEL[banco]}",
                "O bot agora vai usar este banco pra confirmar PIX recebidos."
            ),
            view=None,
        )


class RemoverView(discord.ui.View):
    def __init__(self, guild_id: int, configs: list[str]):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        sel = discord.ui.Select(
            placeholder="Qual banco remover...",
            options=[
                discord.SelectOption(label=pc.BANCOS_LABEL[b], value=b)
                for b in configs
            ],
        )
        sel.callback = self._on_select
        self.add_item(sel)
        self.sel = sel

    async def _on_select(self, inter: discord.Interaction):
        if not _is_server_admin(inter):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        banco = self.sel.values[0]
        pc.remover_creds(self.guild_id, banco)
        if banco == "efi":
            pc.remover_cert_efi(self.guild_id)
        await inter.response.edit_message(
            embed=_ok(f"{pc.BANCOS_LABEL[banco]} removido", "Credenciais apagadas."),
            view=None,
        )


class TutorialView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    # Tutoriais dos outros bancos foram ocultados temporariamente.
    # Pra reexibir, restaure os @discord.ui.button correspondentes.

    @discord.ui.button(label="Gmail", emoji="📧", style=discord.ButtonStyle.secondary, row=0)
    async def t_gmail(self, inter, btn):
        await inter.response.send_message(embed=_info("Tutorial — Gmail", TUTORIAIS["gmail"]), ephemeral=True)


# ═══════════════════════════════════════════
#  Cog principal
# ═══════════════════════════════════════════

class PixBancoCog(commands.Cog):
    """Cog do painel de configuração de credenciais PIX."""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="configurar_pix",
        description="Configurar credenciais bancárias PIX deste servidor.",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_configurar_pix(self, inter: discord.Interaction):
        if not _is_server_admin(inter):
            return await inter.response.send_message(
                embed=_err("Sem permissão", "Apenas admins do servidor podem configurar."),
                ephemeral=True,
            )
        await inter.response.send_message(
            embed=_embed_status(inter.guild_id),
            view=PixBancoView(inter.guild_id),
            ephemeral=True,
        )

    @app_commands.command(
        name="configurar_pix_efi_cert",
        description="Faz upload do cert.pem do EFI Pay (necessário pra usar EFI).",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.describe(arquivo="Arquivo cert.pem (gerado a partir do .p12 do EFI)")
    async def cmd_efi_cert(self, inter: discord.Interaction, arquivo: discord.Attachment):
        if not _is_server_admin(inter):
            return await inter.response.send_message(
                embed=_err("Sem permissão"), ephemeral=True
            )
        # validações básicas
        if arquivo.size > 100_000:
            return await inter.response.send_message(
                embed=_err("Arquivo muito grande", "cert.pem geralmente tem <10KB."),
                ephemeral=True,
            )
        nome = arquivo.filename.lower()
        if not (nome.endswith(".pem") or nome.endswith(".crt")):
            return await inter.response.send_message(
                embed=_err("Extensão inválida", "Envie um arquivo `.pem` ou `.crt`."),
                ephemeral=True,
            )
        await inter.response.defer(ephemeral=True, thinking=True)
        try:
            conteudo = await arquivo.read()
            # Validação mínima: tem que ter cabeçalho PEM
            if b"-----BEGIN" not in conteudo:
                return await inter.followup.send(
                    embed=_err("Arquivo inválido", "Não parece um PEM válido (faltou `-----BEGIN`)."),
                    ephemeral=True,
                )
            path = pc.salvar_cert_efi(inter.guild_id, conteudo)
            await inter.followup.send(
                embed=_ok(
                    "Certificado salvo",
                    f"Tamanho: {len(conteudo)} bytes\n"
                    f"Caminho: `{path}`\n\n"
                    "**Apague a mensagem original com o anexo** pra não vazar o certificado!"
                ),
                ephemeral=True,
            )
        except Exception as e:
            _log.error(f"[efi_cert] {e}")
            await inter.followup.send(embed=_err("Erro", f"`{e}`"), ephemeral=True)


    @app_commands.command(
        name="configurar_pix_gmail_code",
        description="Cole o código de autorização do Google após autorizar o Gmail.",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.describe(codigo="Código gerado pelo Google após autorizar")
    async def cmd_gmail_code(self, inter: discord.Interaction, codigo: str):
        if not _is_server_admin(inter):
            return await inter.response.send_message(embed=_err("Sem permissão"), ephemeral=True)
        await inter.response.defer(ephemeral=True, thinking=True)

        # Verifica se Gmail já foi autorizado anteriormente
        creds_atual = pc.get_creds_guild(inter.guild_id, "gmail")
        if creds_atual.get("refresh_token"):
            return await inter.followup.send(
                embed=_info("Gmail já autorizado",
                            "Para reautorizar, clique em **Gmail** no painel `/configurar_pix` "
                            "e faça o fluxo novamente. O refresh_token antigo será sobrescrito."),
                ephemeral=True,
            )

        try:
            from utils.pix_gmail import trocar_code_por_tokens, obter_email_gmail
            tokens = await trocar_code_por_tokens(auth_code=codigo.strip())
        except Exception as e:
            return await inter.followup.send(
                embed=_err("Código inválido ou expirado",
                           f"`{e}`\n\nTente novamente: `/configurar_pix` → Gmail → repita o processo."),
                ephemeral=True,
            )

        # Captura o email autorizado pra exibir no painel
        try:
            email_autorizado = await obter_email_gmail(tokens)
            if email_autorizado:
                tokens["email_autorizado"] = email_autorizado
        except Exception as e:
            _log.warning(f"[gmail] não foi possível capturar email: {e}")

        pc.set_creds_guild(inter.guild_id, "gmail", tokens)
        if not pc.get_banco_ativo(inter.guild_id):
            pc.set_banco_ativo(inter.guild_id, "gmail")

        try:
            from utils.pix_gmail import testar_gmail
            ok, msg = await testar_gmail(tokens)
        except Exception as e:
            ok, msg = False, f"❌ Erro ao testar: {e}"

        if ok:
            await inter.followup.send(
                embed=_ok(
                    "Gmail configurado! 📧",
                    f"{msg}\n\n"
                    "Bancos suportados via email: **Nubank, Inter, C6, PicPay, Bradesco, Itaú, Santander** e outros.\n"
                    "Jogadores usam `pg Nome` normalmente — o bot vai checar os emails automaticamente.",
                ),
                ephemeral=True,
            )
        else:
            await inter.followup.send(
                embed=_err("Tokens salvos mas teste falhou", msg), ephemeral=True
            )

    @app_commands.command(
        name="debug_gmail",
        description="[ADMIN] Diagnóstico: lista emails encontrados pelo Gmail e o que foi parseado.",
    )
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    @app_commands.describe(horas="Quantas horas pra trás procurar (default 12)")
    async def cmd_debug_gmail(self, inter: discord.Interaction, horas: int = 12):
        if not _is_server_admin(inter):
            return await inter.response.send_message(embed=_err("Sem permissão"), ephemeral=True)
        await inter.response.defer(ephemeral=True, thinking=True)

        creds = pc.get_creds_guild(inter.guild_id, "gmail")
        if not creds.get("refresh_token"):
            return await inter.followup.send(
                embed=_err("Gmail não configurado", "Use `/configurar_pix` → Gmail primeiro."),
                ephemeral=True,
            )

        from utils import pix_gmail as pg
        try:
            access_token = await pg._refresh_access_token(creds)
        except Exception as e:
            return await inter.followup.send(
                embed=_err("Falha no refresh_token", f"`{e}`"), ephemeral=True
            )

        # 1. Roda a query exata da API
        query = pg._GMAIL_QUERY_TEMPLATE.format(horas=horas)

        # 2. Busca IDs sem filtro nenhum (pra ver se chega email)
        try:
            ids_filtrados = await pg._buscar_ids_mensagens(access_token, horas=horas, max_results=20)
        except Exception as e:
            return await inter.followup.send(
                embed=_err("Falha ao listar mensagens", f"`{e}`"), ephemeral=True
            )

        # 3. Busca SEM filtro de remetente, só pra ver o que tem na inbox
        ids_amplos = []
        try:
            data = await pg._gmail_get(access_token, "messages", {
                "q": f"newer_than:{horas}h",
                "maxResults": "10",
            })
            ids_amplos = [m["id"] for m in (data.get("messages") or [])]
        except Exception:
            pass

        linhas = [
            f"**Query usada (com filtro de bancos):**",
            f"```\n{query[:500]}\n```",
            f"**Resultados:**",
            f"• Filtrados (de bancos): **{len(ids_filtrados)}**",
            f"• Amplos (qualquer email recente): **{len(ids_amplos)}**",
            "",
        ]

        # 4. Detalha as mensagens filtradas (com parser)
        if ids_filtrados:
            linhas.append("**📧 Emails de bancos encontrados:**")
            for i, mid in enumerate(ids_filtrados[:5]):
                try:
                    msg = await pg._get_mensagem(access_token, mid)
                    payload = msg.get("payload", {})
                    headers = payload.get("headers", [])
                    from_raw = pg._extrair_header(headers, "From")[:60]
                    subject = pg._extrair_header(headers, "Subject")[:60]
                    body = pg._extrair_texto(payload)
                    if body and "<" in body:
                        body = pg._limpar_html(body)
                    parser = pg._escolher_parser(from_raw)
                    parsed = parser(subject, body)
                    linhas.append(f"\n`{i+1}.` **De:** `{from_raw}`")
                    linhas.append(f"   **Assunto:** {subject}")
                    linhas.append(f"   **Body (50 chars):** `{body[:50]}...`")
                    if parsed:
                        linhas.append(f"   ✅ **Parser OK:** valor=`R$ {parsed['valor']}` nome=`{parsed['nome']}`")
                    else:
                        linhas.append(f"   ❌ **Parser falhou** (não achou valor+nome)")
                except Exception as e:
                    linhas.append(f"`{i+1}.` Erro: `{e}`")
        elif ids_amplos:
            linhas.append("⚠️ **Tem emails na inbox mas NENHUM bate com o filtro de bancos.**")
            linhas.append("Possíveis causas:")
            linhas.append("• Remetente não tá na lista `REMETENTES_BANCOS`")
            linhas.append("• Assunto não contém: pix, recebeu, transferencia ou credito")
            linhas.append("• Email caiu em SPAM (a query ignora SPAM)")
            linhas.append("\n**Amostra dos últimos emails amplos:**")
            for i, mid in enumerate(ids_amplos[:3]):
                try:
                    msg = await pg._get_mensagem(access_token, mid)
                    headers = msg.get("payload", {}).get("headers", [])
                    f = pg._extrair_header(headers, "From")[:60]
                    s = pg._extrair_header(headers, "Subject")[:60]
                    linhas.append(f"`{i+1}.` `{f}` → {s}")
                except Exception:
                    pass
        else:
            linhas.append("⚠️ **Nenhum email recente na inbox.**")
            linhas.append("Confirme:")
            linhas.append(f"• Você enviou pra `pedropedrosa017@gmail.com`?")
            linhas.append("• O email não caiu em SPAM?")
            linhas.append(f"• O email é mais antigo que {horas}h?")

        texto = "\n".join(linhas)
        # Discord embed: máx 4096 chars
        if len(texto) > 4000:
            texto = texto[:4000] + "\n... (truncado)"

        em = discord.Embed(
            title="🔍 Debug Gmail",
            description=texto,
            color=0x5865F2,
        )
        await inter.followup.send(embed=em, ephemeral=True)


async def setup(bot):
    await bot.add_cog(PixBancoCog(bot))
