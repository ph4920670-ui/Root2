"""
Helpers de escopo de comandos.

Em discord.py 2.6+, `@app_commands.command()` NÃO aceita `guilds=`,
`allowed_contexts=` nem `allowed_installs=` como kwargs.
Esses precisam ser aplicados como decorators separados depois do `@command`.

Esse módulo expõe funções que retornam decoradores prontos pra empilhar.

Uso:
    @app_commands.command(name="c", description="...")
    @user_app()
    async def c(self, interaction): ...

    @app_commands.command(name="painelcompras", description="...")
    @admin_guilds()
    async def painelcompras(self, interaction): ...
"""

import os
import discord
from discord import app_commands

ADMIN_GUILD_ID = int(os.getenv("GUILD_ID", "0")) or None


def admin_guilds():
    """Decorator: restringe o comando ao GUILD admin (se setado).
    Sem GUILD_ID configurado, vira no-op (global)."""
    if ADMIN_GUILD_ID:
        return app_commands.guilds(discord.Object(id=ADMIN_GUILD_ID))
    # No-op decorator
    def _noop(func):
        return func
    return _noop


def user_app():
    """Decorator: libera o comando APENAS via User Install (Meus Apps).

    O comando NÃO é instalado como comando de servidor — só aparece pra
    quem adicionou o bot na própria conta ("Adicionar aos meus apps").
    Funciona em qualquer guild, DM ou canal privado, mas sempre por
    instalação no usuário.
    """
    def _apply(func):
        # Pode ser usado em qualquer contexto (guild/DM/privado)...
        func = app_commands.allowed_contexts(
            guilds=True, dms=True, private_channels=True
        )(func)
        # ...mas SOMENTE via instalação no usuário (não guild-install).
        func = app_commands.allowed_installs(
            guilds=False, users=True
        )(func)
        return func
    return _apply


def guild_app():
    """Decorator: comando de servidor disponível em QUALQUER guild.

    Instalado via guild-install (aparece pra todos no servidor), mas sem
    restrição a um guild específico — diferente de @admin_guilds(), que
    trava no GUILD_ID. Usado pelo /botconfig dos servidores de fora.
    """
    def _apply(func):
        func = app_commands.allowed_contexts(
            guilds=True, dms=False, private_channels=False
        )(func)
        func = app_commands.allowed_installs(
            guilds=True, users=False
        )(func)
        return func
    return _apply


# ─── Compatibilidade: kwargs antigos (não usar mais) ──────────────────
def admin_guilds_kwarg():
    """⚠️ Deprecated — usa @admin_guilds() em vez disso.
    Retorna {} pra não quebrar imports antigos."""
    return {}


def user_app_kwargs():
    """⚠️ Deprecated — usa @user_app() em vez disso.
    Retorna {} pra não quebrar imports antigos."""
    return {}


def is_admin_guild(interaction: discord.Interaction) -> bool:
    """Confirma em runtime se o comando admin está sendo usado no guild correto."""
    if ADMIN_GUILD_ID is None:
        return True
    return interaction.guild_id == ADMIN_GUILD_ID
