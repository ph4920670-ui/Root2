"""Discord OAuth2 helpers + Supabase-based session management."""

import uuid
import logging
import httpx
from datetime import datetime, timedelta, timezone
from fastapi import Request, HTTPException

from painel.config import (
    DISCORD_API,
    get_client_id, get_client_secret, get_redirect, get_admin_id,
)

_log = logging.getLogger("salasff.site.auth")

COOKIE_NAME = "salasff_sid"
SESSION_TTL_DAYS = 7


# ── Supabase session helpers ───────────────────────────────────────────────

def _supa():
    from utils.database import get_db
    return get_db()


def _expires_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_TTL_DAYS)).isoformat()


def _session_create(data: dict) -> str:
    """Cria sessão no Supabase e retorna o session_id."""
    sid = str(uuid.uuid4())
    try:
        _supa().table("sessions").insert({
            "id": sid,
            "user_id": str(data.get("id", "")),
            "user_name": data.get("username", ""),
            "user_avatar": data.get("avatar", ""),
            "data": data,
            "expires_at": _expires_at(),
            "criado_em": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        _log.error(f"[_session_create] {e}")
    return sid


def _session_get(sid: str) -> dict | None:
    """Lê sessão do Supabase. Retorna None se não existir ou expirada."""
    try:
        res = _supa().table("sessions").select("*").eq("id", sid).maybe_single().execute()
        if not res.data:
            return None
        row = res.data
        exp = row.get("expires_at")
        if exp:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= exp_dt:
                _session_delete(sid)
                return None
        return row.get("data") or {}
    except Exception as e:
        _log.error(f"[_session_get] {e}")
        return None


def _session_delete(sid: str):
    """Remove sessão do Supabase."""
    try:
        _supa().table("sessions").delete().eq("id", sid).execute()
    except Exception as e:
        _log.error(f"[_session_delete] {e}")


# ── Public session interface ───────────────────────────────────────────────

def set_session(response, data: dict):
    """Cria sessão no Supabase e define cookie com o session_id."""
    sid = _session_create(data)
    response.set_cookie(
        COOKIE_NAME,
        sid,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
    )


def clear_session(response):
    """Remove cookie e apaga sessão do Supabase se o cookie existir."""
    # Não temos acesso ao request aqui, então apenas apagamos o cookie.
    # A sessão expirada será limpa automaticamente por TTL.
    response.delete_cookie(COOKIE_NAME)


def get_session(request: Request) -> dict | None:
    """Lê sessão a partir do cookie da requisição."""
    sid = request.cookies.get(COOKIE_NAME)
    if not sid:
        return None
    return _session_get(sid)


def clear_session_from_request(request: Request, response):
    """Remove cookie e apaga sessão do Supabase."""
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        _session_delete(sid)
    response.delete_cookie(COOKIE_NAME)


def require_session(request: Request) -> dict:
    sess = get_session(request)
    if not sess:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return sess


def is_admin(user_id: str) -> bool:
    return str(user_id) == str(get_admin_id())


# ── Discord OAuth2 ────────────────────────────────────────────────────────

def get_oauth_url(state: str = "") -> str:
    from urllib.parse import quote
    params = (
        f"client_id={get_client_id()}"
        f"&redirect_uri={quote(get_redirect(), safe='')}"
        f"&response_type=code"
        f"&scope=identify"
        + (f"&state={state}" if state else "")
    )
    return f"https://discord.com/api/oauth2/authorize?{params}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": get_client_id(),
                "client_secret": get_client_secret(),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": get_redirect(),
            },
        )
        r.raise_for_status()
        return r.json()


async def fetch_user(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()
