"""SalasFF Web Panel — FastAPI application."""

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from painel.auth import (
    get_oauth_url, exchange_code, fetch_user,
    get_session, set_session, clear_session_from_request, is_admin,
)
from painel.routers import admin as admin_router
from painel.routers import org   as org_router

app = FastAPI(title="SalasFF Panel")

app.mount("/static", StaticFiles(directory="painel/static"), name="static")
templates = Jinja2Templates(directory="painel/templates")

app.include_router(admin_router.router)
app.include_router(org_router.router)


# ── Public routes ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    sess = get_session(request)
    if not sess:
        return RedirectResponse("/login")
    if is_admin(sess.get("id", "")):
        return RedirectResponse("/admin/")
    # Check if this user is an org owner
    from utils.database import get_db
    uid = str(sess.get("id", ""))
    try:
        res = (get_db().table("orgs").select("guild_id")
               .eq("owner_discord_id", uid)
               .eq("ativo", True)
               .maybe_single()
               .execute())
        org = res.data
    except Exception:
        org = None
    if org:
        return RedirectResponse("/org/")
    return templates.TemplateResponse("no_access.html", {"request": request, "user": sess})


@app.get("/login", response_class=HTMLResponse)
async def login(request: Request):
    sess = get_session(request)
    if sess:
        return RedirectResponse("/")
    next_url = request.query_params.get("next", "/")
    oauth_url = get_oauth_url(state=next_url)
    return templates.TemplateResponse("login.html", {
        "request":  request,
        "oauth_url": oauth_url,
    })


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = ""):
    if error or not code:
        return RedirectResponse("/login?error=1")

    try:
        token_data = await exchange_code(code)
        user_data  = await fetch_user(token_data["access_token"])
    except Exception:
        return RedirectResponse("/login?error=oauth")

    sess = {
        "id":            user_data["id"],
        "username":      user_data.get("username", ""),
        "discriminator": user_data.get("discriminator", "0"),
        "avatar":        user_data.get("avatar"),
        "global_name":   user_data.get("global_name") or user_data.get("username", ""),
    }

    state = request.query_params.get("state", "/")
    redirect_to = state if state.startswith("/") else "/"

    response = RedirectResponse(redirect_to, status_code=303)
    set_session(response, sess)
    return response


@app.get("/logout")
async def logout(request: Request):
    response = RedirectResponse("/login")
    clear_session_from_request(request, response)
    return response


# ── Entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run("painel.main:app", host="0.0.0.0", port=8080, reload=False)
