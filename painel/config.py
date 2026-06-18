"""
Config do site — lê credenciais OAuth2 do botconfig (Supabase) que você já
configurou pelo painel /botconfig, com fallback para variáveis de ambiente.

Ordem de prioridade:
  1. Variável de ambiente (se definida)
  2. botconfig no Supabase (oauth2_client_id / oauth2_client_secret / etc)
  3. Valor padrão
"""

import os
import logging

_log = logging.getLogger("salasff.site.config")

DISCORD_API = "https://discord.com/api/v10"


def _botcfg() -> dict:
    """Carrega o botconfig do Supabase (mesma fonte do painel /botconfig)."""
    try:
        from utils.database import botconfig_load
        return botconfig_load() or {}
    except Exception as e:
        _log.warning(f"[config] botconfig_load falhou: {e}")
        return {}


def get_client_id() -> str:
    # env > botconfig (oauth2_client_id ou application_id) > vazio
    env = os.environ.get("DISCORD_CLIENT_ID", "").strip()
    if env:
        return env
    cfg = _botcfg()
    return str(cfg.get("oauth2_client_id") or cfg.get("application_id") or "").strip()


def get_client_secret() -> str:
    env = os.environ.get("DISCORD_CLIENT_SECRET", "").strip()
    if env:
        return env
    cfg = _botcfg()
    return str(cfg.get("oauth2_client_secret") or "").strip()


def get_site_url() -> str:
    env = os.environ.get("SITE_URL", "").strip()
    if env:
        return env.rstrip("/")
    cfg = _botcfg()
    # gratis_oauth2_redirect pode ser tipo https://app.discloud.app/oauth/callback
    redir = str(cfg.get("gratis_oauth2_redirect") or "").strip()
    if redir:
        # extrai a base (sem /caminho)
        from urllib.parse import urlparse
        p = urlparse(redir)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    return "http://localhost:8080"


def get_redirect() -> str:
    return f"{get_site_url()}/auth/callback"


def get_admin_id() -> str:
    env = os.environ.get("ADMIN_DISCORD_ID", "").strip()
    if env:
        return env
    cfg = _botcfg()
    # tenta admin_discord_id no botconfig, senão primeiro de ADMIN_IDS
    if cfg.get("admin_discord_id"):
        return str(cfg["admin_discord_id"]).strip()
    try:
        import config as _bot_config
        if _bot_config.ADMIN_IDS:
            return str(_bot_config.ADMIN_IDS[0])
    except Exception:
        pass
    return ""


# ── Compat: constantes lidas na importação (fallback estático) ──────────────
# Mantidas para código que importa direto, mas prefira as funções get_*.
DISCORD_CLIENT_ID     = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
SITE_URL              = os.environ.get("SITE_URL", "http://localhost:8080").rstrip("/")
ADMIN_DISCORD_ID      = os.environ.get("ADMIN_DISCORD_ID", "")
COOKIE_SECRET         = os.environ.get("COOKIE_SECRET", "change-me-in-production")
OAUTH2_REDIRECT       = f"{SITE_URL}/auth/callback"
