"""run.py — Entry point unificado: bot Discord + site FastAPI no mesmo processo."""

import asyncio
import logging
import os
import sys

import discord
from discord import app_commands
from discord.ext import commands
import uvicorn

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
_log = logging.getLogger("salasff.run")

# ── Cogs a carregar ────────────────────────────────────────────────────────
COGS = [
    "cogs.main",
    "cogs.botconfig",
    "cogs.comprar",
    "cogs.convites",
    "cogs.dev",
    "cogs.mediador_painel",
    "cogs.painel_org",
    "cogs.pg_match",
    "cogs.pg_polling",
    "cogs.pg_user_gateway",
    "cogs.pix_banco",
    "cogs.plano",
    "cogs.preview",
    "cogs.ranking",
    "cogs.ticket",
    "cogs.token_mode",
    "cogs.aposta_auto",
    "cogs.migracao",
]


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.members     = True
    intents.message_content = True
    intents.guilds      = True

    bot = commands.Bot(
        command_prefix=[".", "+"],
        intents=intents,
        help_command=None,
    )

    @bot.tree.error
    async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        _log.error(f"[app_cmd_error] /{getattr(interaction.command, 'name', '?')} — {error}", exc_info=True)
        try:
            msg = f"❌ Erro interno: `{error}`"
            if not interaction.response.is_done():
                await interaction.response.send_message(msg, ephemeral=True)
            else:
                await interaction.followup.send(msg, ephemeral=True)
        except Exception:
            pass

    @bot.event
    async def on_ready():
        _log.info(f"[Bot] Logado como {bot.user} (id={bot.user.id})")
        # Inicializa banco de dados Supabase
        try:
            from utils.database import init_db
            init_db()
        except Exception as e:
            _log.error(f"[Bot] init_db erro: {e}")
        # Liga o logger de logs do Discord ao bot
        try:
            from utils import logs as _discord_logs
            _discord_logs.init_logger(bot)
        except Exception as e:
            _log.error(f"[Bot] init_logger erro: {e}")
        # Salva o Client ID (= application id) no botconfig para o site usar no OAuth2
        try:
            from utils.database import botconfig_load, botconfig_save
            app_id = str(bot.application_id or bot.user.id)
            cfg = botconfig_load()
            if cfg.get("oauth2_client_id") != app_id:
                cfg["oauth2_client_id"] = app_id
                botconfig_save(cfg)
                _log.info(f"[Bot] oauth2_client_id salvo no botconfig: {app_id}")
        except Exception as e:
            _log.error(f"[Bot] salvar client_id erro: {e}")

    async def setup_hook_impl():
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                _log.info(f"[Bot] Cog carregado: {cog}")
            except Exception as e:
                _log.error(f"[Bot] Erro ao carregar {cog}: {e}")
        # Sincroniza slash commands nos servidores owner
        for guild_id in config.OWNER_GUILD_IDS:
            try:
                guild = discord.Object(id=guild_id)
                synced = await bot.tree.sync(guild=guild)
                _log.info(f"[Bot] Sincronizados {len(synced)} comandos na guild {guild_id}")
            except Exception as e:
                _log.error(f"[Bot] Erro ao sincronizar guild {guild_id}: {e}")
        # Sincroniza comandos globais também
        try:
            synced = await bot.tree.sync()
            _log.info(f"[Bot] Sincronizados {len(synced)} comandos globais")
        except Exception as e:
            _log.error(f"[Bot] Erro ao sincronizar comandos globais: {e}")

    bot.setup_hook = setup_hook_impl
    return bot


async def main():
    # Importa o app FastAPI do painel
    from painel.main import app as web_app

    bot = create_bot()

    web_config = uvicorn.Config(
        web_app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_level="warning",
    )
    server = uvicorn.Server(web_config)

    _log.info("[run] Iniciando bot + site juntos...")

    await asyncio.gather(
        bot.start(config.DISCORD_TOKEN),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
