"""
.setar — apaga TUDO do servidor (categorias, canais e cargo Suporte) e recria
a estrutura completa do bot.

⚠️ MUITO DESTRUTIVO: apaga TODOS os canais do servidor antes de recriar.
Exige confirmação: `.setar confirmar`

Só admin do servidor pode rodar.
"""

import discord
from discord.ext import commands

from utils import database
from utils.emojis import AWAITING, CLOUD, DOLLAR, SWORD, VISION
from discord.ui import LayoutView, Container, TextDisplay, Separator
from views.gratis_view import (
    PainelTesteView,
    PainelBonusView,
    PainelRankingFixoView,
)
from views.loja_view import (
    LojaDiscordView,
    LojaTelegramView,
    ComoUsarDiscordView,
    ComoUsarTelegramView,
)
from views.ticket_view import PainelTicketView


# ─── ESTRUTURA-ALVO (igual à foto do servidor) ────────────────────────

CARGO_SUPORTE = "Suporte"

# Cada categoria → lista de canais (na ordem de exibição)
ESTRUTURA = [
    ("Comunidade", [
        "📢｜anúncios",
        "❓｜como-compra",
        "📋｜avaliacoes",
        "💬｜sujestao",
    ]),
    ("Sala Discord", [
        "🔶｜sala-discord",
        "🔶｜como-usar",
    ]),
    ("Salas Telegram", [
        "🔷｜sala-telegram",
        "🔷｜como-usar",
    ]),
    ("Salas Gratis", [
        "🎁｜sala-teste",
        "🏆｜ranking",
    ]),
    ("Atendimento", [
        "🎫｜abrir-ticket",
    ]),
]

# Categoria de logs (privada) e mapeamento campo→nome p/ guild_config
CATEGORIA_LOGS = "Logs"
CANAIS_LOGS = {
    "canal_logs_tickets_id": "logs-tickets",
    "canal_logs_vendas_id":  "logs-vendas",
    "canal_logs_saldo_id":   "logs-saldo",
    "canal_logs_teste_id":   "logs-teste",
    "canal_logs_bonus_id":   "logs-bonus",
    "canal_logs_ranking_id": "logs-ranking",
}

# Quais categorias são a "categoria de tickets" no guild_config
CATEGORIA_TICKETS = "Atendimento"

REASON = ".setar — bot painel de compras"


# ─── TEXTOS DE INTRODUÇÃO PRONTOS ─────────────────────────────────────
# Mensagens postadas automaticamente nos canais informativos

TXT_ANUNCIOS = (
    "# 📢 Canal de Anúncios\n"
    "Aqui são publicados todos os avisos, novidades e atualizações importantes.\n"
    "Fique de olho — ative as notificações pra não perder nada!"
)

TXT_COMO_COMPRA = (
    "# ❓ Como Comprar\n\n"
    "**1.** Vá até o canal <#__SALA_DISCORD__> ou <#__SALA_TELEGRAM__> e escolha "
    "o produto no painel.\n"
    "**2.** Selecione no menu e confirme a compra — vai gerar um **Pix** na hora.\n"
    "**3.** Pague pelo seu banco com o código copia-e-cola ou QR Code.\n"
    "**4.** Clique em **Verificar Pagamento** — assim que confirmar, sua sala "
    "particular é criada automaticamente e o link chega no seu privado.\n\n"
    "Dúvidas? Abra um ticket na categoria **Atendimento**."
)

TXT_AVALIACOES = (
    "# 📋 Avaliações\n"
    "Compartilhe sua experiência com a comunidade!\n"
    "Mande aqui prints de ganhos, depoimentos ou só um \"funcionou demais\".\n"
    "Avaliações honestas ajudam todo mundo a confiar no serviço. 🙏"
)

TXT_SUJESTAO = (
    "# 💬 Sugestões\n"
    "Tem ideia pra melhorar o servidor, um produto novo, alguma feature?\n"
    "Manda aqui — leio todas e implemento o que fizer sentido."
)

TXT_COMO_USAR_DISCORD = (
    "# 🔶 Como usar — Sala Discord\n\n"
    "**Depois que você comprar**, o bot cria uma sala particular dentro do servidor "
    "(canal de voz/texto privado).\n\n"
    "**Como funciona:**\n"
    "• A sala só você e o suporte têm acesso\n"
    "• Você recebe o link diretamente no seu privado pelo bot\n"
    "• Dura conforme o plano que você escolheu (1 dia, 7 dias, etc.)\n"
    "• Quando expira, a sala é apagada automaticamente\n\n"
    "**Comandos úteis dentro da sala:**\n"
    "• `/c` — ver sua carteira\n"
    "• `/cs` — criar/gerenciar suas salas\n\n"
    "Qualquer problema, abra um ticket em **Atendimento**."
)

TXT_COMO_USAR_TELEGRAM = (
    "# 🔷 Como usar — Sala Telegram\n\n"
    "**Depois que você comprar um plano Telegram**, o bot te manda no privado "
    "o **link de convite** pro grupo/canal Telegram correspondente.\n\n"
    "**Como funciona:**\n"
    "• O link é único e válido por tempo limitado — use logo que receber\n"
    "• Você entra no grupo do Telegram e recebe as dicas/conteúdos lá\n"
    "• Acesso dura conforme o plano (1 dia, 7 dias, etc.)\n"
    "• Quando expira, você é removido automaticamente\n\n"
    "**Não recebeu o link?** Verifique se seu privado tá aberto pro bot, ou abra "
    "um ticket em **Atendimento**."
)


def _formatar_como_compra(canais_por_nome: dict) -> str:
    """Substitui placeholders pelos mentions reais dos canais."""
    sd = canais_por_nome.get("🔶｜sala-discord")
    st = canais_por_nome.get("🔷｜sala-telegram")
    return TXT_COMO_COMPRA.replace(
        "__SALA_DISCORD__", str(sd.id) if sd else "0"
    ).replace(
        "__SALA_TELEGRAM__", str(st.id) if st else "0"
    )


# ─── COG ──────────────────────────────────────────────────────────────

class SetarCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="setar")
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def setar(self, ctx: commands.Context, modo: str = None):
        guild = ctx.guild

        # ── 1. Confirmação ────────────────────────────────────────────
        if (modo or "").lower() != "confirmar":
            return await ctx.reply(
                f"{AWAITING} **Atenção:** isso vai **APAGAR TODOS** os canais "
                f"e categorias do servidor e recriar a estrutura do bot do zero.\n\n"
                f"Vai ser criado:\n"
                f"• Cargo: `{CARGO_SUPORTE}`\n"
                f"• Categorias: `Comunidade`, `Sala Discord`, `Salas Telegram`, "
                f"`Salas Gratis`, `Atendimento`, `Logs` (privada)\n"
                f"• Canais públicos: anúncios, como-compra, avaliacoes, sujestao, "
                f"sala-discord, sala-telegram, sala-teste, sala-bonus, ranking, etc.\n"
                f"• Canais de logs privados (só Admin + Suporte): logs-tickets, "
                f"logs-vendas, logs-saldo, logs-teste, logs-bonus, logs-ranking\n\n"
                f"Se tiver certeza, rode:\n```\n.setar confirmar\n```",
                mention_author=False,
            )

        # ── 2. Permissões ────────────────────────────────────────────
        me = guild.me
        faltando = []
        if not me.guild_permissions.manage_channels: faltando.append("Gerenciar Canais")
        if not me.guild_permissions.manage_roles:    faltando.append("Gerenciar Cargos")
        if not me.guild_permissions.view_channel:    faltando.append("Ver Canais")
        if faltando:
            return await ctx.reply(
                f"{AWAITING} Faltam permissões pro bot: "
                f"`{', '.join(faltando)}`.",
                mention_author=False,
            )

        msg_status = await ctx.reply(
            f"{CLOUD} Limpando e recriando servidor... aguarde.",
            mention_author=False,
        )

        log = []
        # canal de comando — pra não apagar antes de avisar o usuário
        canal_origem_id = ctx.channel.id

        # ── 3. APAGAR TUDO ───────────────────────────────────────────
        # 3.1 Canais (texto, voz, anúncio, stage, fórum) — exceto o canal atual
        canais_pra_apagar = [c for c in guild.channels
                             if not isinstance(c, discord.CategoryChannel)
                             and c.id != canal_origem_id]
        for ch in canais_pra_apagar:
            try:
                await ch.delete(reason=REASON)
            except Exception as e:
                log.append(f"⚠️ falha apagando `{ch.name}`: `{e}`")

        # 3.2 Categorias (agora vazias)
        for cat in [c for c in guild.channels if isinstance(c, discord.CategoryChannel)]:
            try:
                await cat.delete(reason=REASON)
            except Exception as e:
                log.append(f"⚠️ falha apagando categoria `{cat.name}`: `{e}`")

        # 3.3 Cargo Suporte antigo
        cargo_antigo = discord.utils.find(
            lambda r: r.name.lower() == CARGO_SUPORTE.lower(), guild.roles
        )
        if cargo_antigo is not None and not cargo_antigo.managed:
            try:
                await cargo_antigo.delete(reason=REASON)
            except Exception as e:
                log.append(f"⚠️ falha apagando cargo antigo: `{e}`")

        log.append(f"{AWAITING} servidor limpo")

        # ── 4. CRIAR cargo Suporte ───────────────────────────────────
        cargo = None
        try:
            cargo = await guild.create_role(
                name=CARGO_SUPORTE,
                colour=discord.Colour.from_str("#5865F2"),
                mentionable=True,
                reason=REASON,
            )
            log.append(f"{SWORD} cargo `{CARGO_SUPORTE}` criado")
        except Exception as e:
            log.append(f"❌ erro criando cargo: `{e}`")

        # ── 5. CRIAR categorias públicas + canais ────────────────────
        cat_tickets_obj = None
        canais_criados = {}  # nome → TextChannel, pra publicar painéis depois
        for nome_cat, canais in ESTRUTURA:
            try:
                cat = await guild.create_category(name=nome_cat, reason=REASON)
                log.append(f"{SWORD} categoria `{nome_cat}` criada")
            except Exception as e:
                log.append(f"❌ erro categoria `{nome_cat}`: `{e}`")
                continue

            if nome_cat == CATEGORIA_TICKETS:
                cat_tickets_obj = cat

            for nome_canal in canais:
                try:
                    ch = await guild.create_text_channel(
                        name=nome_canal, category=cat, reason=REASON
                    )
                    canais_criados[nome_canal] = ch
                except Exception as e:
                    log.append(f"❌ erro canal `{nome_canal}`: `{e}`")

        # ── 6. CRIAR categoria Logs (privada) + canais de log ────────
        canais_logs_ids = {}
        cat_logs = None

        overwrites_logs = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }
        if cargo is not None:
            overwrites_logs[cargo] = discord.PermissionOverwrite(
                view_channel=True, send_messages=False, read_message_history=True
            )

        try:
            cat_logs = await guild.create_category(
                name=CATEGORIA_LOGS,
                overwrites=overwrites_logs,
                reason=REASON,
            )
            log.append(f"{SWORD} categoria `{CATEGORIA_LOGS}` criada (privada)")
        except Exception as e:
            log.append(f"❌ erro categoria logs: `{e}`")

        if cat_logs is not None:
            for campo, nome in CANAIS_LOGS.items():
                try:
                    ch = await guild.create_text_channel(
                        name=nome, category=cat_logs, reason=REASON
                    )
                    canais_logs_ids[campo] = ch.id
                except Exception as e:
                    log.append(f"❌ erro canal `{nome}`: `{e}`")

        # ── 6.5 PUBLICAR painéis e textos explicativos ───────────────
        async def _enviar(ch_nome, conteudo=None, view=None):
            ch = canais_criados.get(ch_nome)
            if ch is None:
                return
            try:
                kwargs = {}
                if conteudo: kwargs["content"] = conteudo
                if view:     kwargs["view"] = view
                await ch.send(**kwargs)
                log.append(f"{SWORD} painel/texto publicado em {ch.mention}")
            except Exception as e:
                log.append(f"⚠️ erro publicando em `{ch_nome}`: `{e}`")

        # Canais informativos:
        # • anúncios   → vazio (admin publica manualmente)
        # • avaliacoes → vazio (usuários postam)
        # • sujestao   → vazio (cog SugestaoCog escuta on_message)
        # • como-compra → recebe view V2 com passos
        ch_cc = canais_criados.get("❓｜como-compra")
        if ch_cc is not None:
            try:
                sd = canais_criados.get("🔶｜sala-discord")
                st = canais_criados.get("🔷｜sala-telegram")
                sd_mention = sd.mention if sd else "`#sala-discord`"
                st_mention = st.mention if st else "`#sala-telegram`"

                v_cc = LayoutView(timeout=None)
                c_cc = Container(accent_colour=discord.Colour.from_str("#5865F2"))
                c_cc.add_item(TextDisplay(f"# {VISION} Como Comprar"))
                c_cc.add_item(Separator())
                c_cc.add_item(TextDisplay(
                    f"**1.** Vá até {sd_mention} ou {st_mention} "
                    f"e escolha o produto no painel.\n"
                    f"**2.** Confirme a compra — vai gerar um **PIX** na hora.\n"
                    f"**3.** Pague pelo seu banco (copia-e-cola ou QR Code).\n"
                    f"**4.** Clique em **Já paguei — Verificar**.\n"
                    f"**5.** A entrega é **instantânea** após confirmação."
                ))
                c_cc.add_item(TextDisplay(
                    f"-# Dúvidas? Abra um ticket em **Atendimento**"
                ))
                v_cc.add_item(c_cc)
                await ch_cc.send(view=v_cc)
            except Exception as e:
                log.append(f"⚠️ erro como-compra: `{e}`")

        # ─── Painel SALA DISCORD (Components V2) ──────────────────
        ch_disc = canais_criados.get("🔶｜sala-discord")
        if ch_disc is not None:
            try:
                disc_view = LojaDiscordView()
                await disc_view.build(guild.id)  # carrega preço do banco
                await ch_disc.send(view=disc_view)
                log.append(f"{SWORD} painel discord em {ch_disc.mention}")
            except Exception as e:
                log.append(f"⚠️ erro painel discord: `{e}`")

        # Como usar no canal 🔶｜como-usar (Components V2)
        ch_cd = canais_criados.get("🔶｜como-usar")
        if ch_cd is not None:
            try:
                await ch_cd.send(view=ComoUsarDiscordView())
            except Exception as e:
                log.append(f"⚠️ erro como-usar discord: `{e}`")

        # ─── Painel SALA TELEGRAM (Components V2) ─────────────────
        ch_tg = canais_criados.get("🔷｜sala-telegram")
        if ch_tg is not None:
            try:
                tg_view = LojaTelegramView()
                await tg_view.build(guild.id)
                await ch_tg.send(view=tg_view)
                log.append(f"{SWORD} painel telegram em {ch_tg.mention}")
            except Exception as e:
                log.append(f"⚠️ erro painel telegram: `{e}`")

        # Como usar no canal 🔷｜como-usar (Components V2)
        ch_ct = canais_criados.get("🔷｜como-usar")
        if ch_ct is not None:
            try:
                await ch_ct.send(view=ComoUsarTelegramView())
            except Exception as e:
                log.append(f"⚠️ erro como-usar telegram: `{e}`")

        # ─── Painel de TICKETS (Components V2) ────────────────────
        ch_ticket = canais_criados.get("🎫｜abrir-ticket")
        if ch_ticket is not None:
            try:
                await ch_ticket.send(view=PainelTicketView())
                log.append(f"{SWORD} painel ticket em {ch_ticket.mention}")
            except Exception as e:
                log.append(f"⚠️ erro painel ticket: `{e}`")

        # Painéis grátis (já são V2)
        try:
            ch = canais_criados.get("🎁｜sala-teste")
            if ch:
                await ch.send(view=PainelTesteView())
                log.append(f"{SWORD} painel teste em {ch.mention}")
        except Exception as e:
            log.append(f"⚠️ erro painel teste: `{e}`")

        try:
            ch = canais_criados.get("🏆｜ranking")
            if ch:
                await ch.send(view=PainelRankingFixoView())
                log.append(f"{SWORD} painel ranking em {ch.mention}")
        except Exception as e:
            log.append(f"⚠️ erro painel ranking: `{e}`")

        # ── 7. Apaga o canal de origem AGORA (já podemos) ────────────
        canal_origem = guild.get_channel(canal_origem_id)
        try:
            if canal_origem is not None:
                # Manda mensagem final em outro canal antes de apagar
                novo_anuncios = discord.utils.find(
                    lambda c: isinstance(c, discord.TextChannel)
                              and "anúncios" in c.name.lower(),
                    guild.channels,
                )
                resumo = (
                    f"{SWORD} **Setup concluído**\n"
                    + "\n".join(f"• {l}" for l in log)
                )
                if len(resumo) > 1900:
                    resumo = resumo[:1900] + "\n... (truncado)"
                if novo_anuncios:
                    await novo_anuncios.send(resumo)
                await canal_origem.delete(reason=REASON + " (canal de origem)")
        except Exception:
            # se algo der errado, pelo menos tenta editar a mensagem original
            try:
                await msg_status.edit(content=resumo)
            except Exception:
                pass

        # ── 8. Salva guild_config ────────────────────────────────────
        update_kwargs = {}
        if cargo is not None:
            update_kwargs["cargo_suporte_id"] = cargo.id
        if cat_tickets_obj is not None:
            update_kwargs["categoria_tickets_id"] = cat_tickets_obj.id
        update_kwargs.update(canais_logs_ids)
        if update_kwargs:
            try:
                await database.update_guild_config(guild.id, **update_kwargs)
            except Exception as e:
                print(f"⚠️ erro salvando guild_config: {e}")

    @setar.error
    async def setar_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply(
                f"{AWAITING} Apenas administradores podem usar `.setar`.",
                mention_author=False,
            )
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.reply("Esse comando só funciona em servidor.", mention_author=False)


async def setup(bot):
    await bot.add_cog(SetarCog(bot))
