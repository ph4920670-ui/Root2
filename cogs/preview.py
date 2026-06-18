# cogs/preview.py — /preview: renderiza JSON do Discord Message Builder

import json
import asyncio
import discord
from discord import app_commands
from discord.ext import commands

import config

_ADMIN_GUILDS = [discord.Object(id=gid) for gid in config.OWNER_GUILD_IDS]


def _err(t, d=""):
    em = discord.Embed(title=f"❌  {t}", color=config.COR_ERRO)
    if d:
        em.description = d
    return em


# ═══════════════════════════════════════════
#  Parser JSON → discord.Embed + View
# ═══════════════════════════════════════════

def _parse_embeds(data: dict) -> list[discord.Embed]:
    embeds = []
    for e in data.get("embeds", []):
        color_raw = e.get("color")
        if isinstance(color_raw, str) and color_raw.startswith("#"):
            color = int(color_raw.lstrip("#"), 16)
        elif isinstance(color_raw, int):
            color = color_raw
        else:
            color = 0x2B2D31

        em = discord.Embed(
            title=e.get("title") or None,
            description=e.get("description") or None,
            color=color,
            url=e.get("url") or None,
        )

        # Author
        author = e.get("author")
        if author and author.get("name"):
            em.set_author(
                name=author["name"],
                url=author.get("url") or None,
                icon_url=author.get("icon_url") or None,
            )

        # Thumbnail
        thumb = e.get("thumbnail")
        if thumb and thumb.get("url"):
            em.set_thumbnail(url=thumb["url"])

        # Image
        image = e.get("image")
        if image and image.get("url"):
            em.set_image(url=image["url"])

        # Footer
        footer = e.get("footer")
        if footer and footer.get("text"):
            em.set_footer(
                text=footer["text"],
                icon_url=footer.get("icon_url") or None,
            )

        # Fields
        for f in e.get("fields", []):
            name = f.get("name", "\u200b")
            value = f.get("value", "\u200b")
            inline = f.get("inline", False)
            if name and value:
                em.add_field(name=name, value=value, inline=inline)

        embeds.append(em)
    return embeds


def _parse_buttons(data: dict) -> discord.ui.View | None:
    """Constrói View com botões desabilitados a partir dos components."""
    components = data.get("components", [])
    if not components:
        return None

    view = discord.ui.View(timeout=None)
    row_idx = 0

    for action_row in components:
        if action_row.get("type") != 1:
            continue
        for comp in action_row.get("components", []):
            comp_type = comp.get("type")

            # Botão (type 2)
            if comp_type == 2:
                style_map = {
                    1: discord.ButtonStyle.primary,
                    2: discord.ButtonStyle.secondary,
                    3: discord.ButtonStyle.success,
                    4: discord.ButtonStyle.danger,
                    5: discord.ButtonStyle.link,
                }
                style = style_map.get(comp.get("style", 2), discord.ButtonStyle.secondary)
                label = comp.get("label") or "\u200b"
                url = comp.get("url")

                # Emoji
                emoji = None
                em_raw = comp.get("emoji")
                if em_raw:
                    try:
                        animated = em_raw.get("animated", False)
                        eid = em_raw.get("id")
                        ename = em_raw.get("name", "e")
                        if eid:
                            emoji = discord.PartialEmoji(name=ename, id=int(eid), animated=animated)
                        else:
                            emoji = em_raw.get("name")  # unicode emoji
                    except Exception:
                        emoji = None

                if style == discord.ButtonStyle.link and url:
                    btn = discord.ui.Button(
                        style=style,
                        label=label,
                        url=url,
                        emoji=emoji,
                        row=row_idx,
                    )
                else:
                    btn = discord.ui.Button(
                        style=style,
                        label=label,
                        emoji=emoji,
                        disabled=True,
                        row=row_idx,
                    )

                view.add_item(btn)

            # Select (type 3)
            elif comp_type == 3:
                options_raw = comp.get("options", [])
                options = []
                for opt in options_raw[:25]:
                    em_raw = opt.get("emoji")
                    emoji = None
                    if em_raw:
                        try:
                            eid = em_raw.get("id")
                            ename = em_raw.get("name", "e")
                            animated = em_raw.get("animated", False)
                            emoji = discord.PartialEmoji(name=ename, id=int(eid), animated=animated) if eid else ename
                        except Exception:
                            emoji = None
                    options.append(discord.SelectOption(
                        label=opt.get("label", "Option")[:100],
                        value=opt.get("value", opt.get("label", "opt"))[:100],
                        description=(opt.get("description") or "")[:100] or None,
                        emoji=emoji,
                    ))

                if not options:
                    options = [discord.SelectOption(label="Opção", value="opt")]

                sel = discord.ui.Select(
                    placeholder=comp.get("placeholder") or "Selecione...",
                    options=options,
                    disabled=True,
                    row=row_idx,
                )
                view.add_item(sel)

        row_idx += 1
        if row_idx >= 5:
            break

    return view if view.children else None


# ═══════════════════════════════════════════
#  Modal — cola o JSON
# ═══════════════════════════════════════════

class PreviewModal(discord.ui.Modal, title="📋 Preview — Cole o JSON"):
    json_input = discord.ui.TextInput(
        label="JSON do Discord Message Builder",
        style=discord.TextStyle.paragraph,
        placeholder='{"embeds": [...], "components": [...]}',
        min_length=2,
        max_length=4000,
    )

    async def on_submit(self, inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)

        # Parse
        raw = self.json_input.value.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as ex:
            return await inter.followup.send(
                embed=_err("JSON inválido", f"> `{ex}`"),
                ephemeral=True,
            )

        embeds = _parse_embeds(data)
        view = _parse_buttons(data)
        content = data.get("content") or ""

        if not embeds and not content and not view:
            return await inter.followup.send(
                embed=_err("Nada para renderizar", "> O JSON não tem `embeds`, `content` nem `components`."),
                ephemeral=True,
            )

        # Limites do Discord
        embeds = embeds[:10]

        kwargs = {"ephemeral": True}
        if content:
            kwargs["content"] = content
        if embeds:
            kwargs["embeds"] = embeds
        if view:
            kwargs["view"] = view

        try:
            await inter.followup.send(**kwargs)
        except discord.HTTPException as ex:
            await inter.followup.send(
                embed=_err("Erro ao renderizar", f"> `{ex}`"),
                ephemeral=True,
            )


# ═══════════════════════════════════════════
#  View com botão Preview
# ═══════════════════════════════════════════

class PreviewLauncherView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Colar JSON e Pré-visualizar", emoji="📋", style=discord.ButtonStyle.primary)
    async def btn_preview(self, inter: discord.Interaction, btn: discord.ui.Button):
        await inter.response.send_modal(PreviewModal())


# ═══════════════════════════════════════════
#  Cog
# ═══════════════════════════════════════════

class PreviewCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="preview", description="[ADMIN] Pré-visualiza uma mensagem a partir de JSON.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_preview(self, inter: discord.Interaction):
        if inter.user.id not in config.ADMIN_IDS:
            return await inter.response.send_message(
                embed=_err("Sem permissão."), ephemeral=True
            )

        em = discord.Embed(
            title="📋  Preview de Mensagem",
            description=(
                "> Cole o JSON do **Discord Message Builder** ou qualquer JSON de mensagem.\n"
                "> Suporta `embeds`, `components` (botões e selects) e `content`."
            ),
            color=0x5865F2,
        )
        await inter.response.send_message(embed=em, view=PreviewLauncherView(), ephemeral=True)


async def setup(bot):
    await bot.add_cog(PreviewCog(bot))
