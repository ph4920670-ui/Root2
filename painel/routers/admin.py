"""Admin panel routes — only accessible by the bot owner."""

from datetime import datetime, timezone
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from painel.auth import get_session, is_admin
from utils.database import get_db

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="painel/templates")


def _require_admin(request: Request):
    sess = get_session(request)
    if not sess or not is_admin(sess.get("id", "")):
        return None
    return sess


# ── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    sess = _require_admin(request)
    if not sess:
        return RedirectResponse("/login?next=/admin/")

    try:
        res_guilds = get_db().table("guild_config").select("*").execute()
        guilds = res_guilds.data or []

        res_orgs = get_db().table("orgs").select("*").execute()
        orgs = {o["guild_id"]: o for o in (res_orgs.data or [])}

        # Merge org info into guild list
        for g in guilds:
            gid = g.get("id", "")
            g["org"] = orgs.get(gid)
            # Compatibilidade com templates que usam g["_id"]
            g.setdefault("_id", gid)

        res_saques = get_db().table("saques").select("*").eq("status", "pendente").execute()
        pending_saques = res_saques.data or []
    except Exception as e:
        import logging
        logging.getLogger("salasff.site.admin").error(f"[admin_dashboard] {e}")
        guilds = []
        pending_saques = []

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "user":    sess,
        "guilds":  guilds,
        "pending_saques": pending_saques,
    })


# ── Server detail / config ────────────────────────────────────────────────

@router.get("/server/{guild_id}", response_class=HTMLResponse)
async def admin_server(request: Request, guild_id: str):
    sess = _require_admin(request)
    if not sess:
        return RedirectResponse("/login?next=/admin/")

    try:
        res_cfg = get_db().table("guild_config").select("*").eq("id", guild_id).maybe_single().execute()
        guild_cfg = res_cfg.data or {}
        guild_cfg.setdefault("_id", guild_id)

        res_org = get_db().table("orgs").select("*").eq("guild_id", guild_id).maybe_single().execute()
        org = res_org.data or {}
        org.setdefault("_id", guild_id)

        res_cmd = get_db().table("guild_commands").select("commands").eq("guild_id", guild_id).maybe_single().execute()
        commands = dict((res_cmd.data or {}).get("commands") or {})
    except Exception:
        guild_cfg = {}
        org = {}
        commands = {}

    all_cmds = ["c", "c1", "c2", "c3", "painel"]
    for cmd in all_cmds:
        if cmd not in commands:
            commands[cmd] = True

    return templates.TemplateResponse("admin/server.html", {
        "request":   request,
        "user":      sess,
        "guild_id":  guild_id,
        "guild_cfg": guild_cfg,
        "org":       org,
        "commands":  commands,
        "all_cmds":  all_cmds,
    })


@router.post("/server/{guild_id}/org", response_class=HTMLResponse)
async def admin_server_org(
    request: Request,
    guild_id: str,
    ativo: str = Form("off"),
    porcentagem: int = Form(70),
    owner_discord_id: str = Form(""),
    nome: str = Form(""),
):
    sess = _require_admin(request)
    if not sess:
        return RedirectResponse("/login?next=/admin/")

    ativo_bool = ativo == "on"

    try:
        res = get_db().table("orgs").select("*").eq("guild_id", guild_id).maybe_single().execute()
        existing = res.data or {}

        update = {
            "guild_id":         guild_id,
            "ativo":            ativo_bool,
            "nome":             nome or existing.get("nome", ""),
            "owner_discord_id": owner_discord_id or existing.get("owner_discord_id", ""),
            "porcentagem":      max(1, min(99, porcentagem)),
        }
        if not existing:
            update["faturamento_total"] = 0.0
            update["saldo_acumulado"]   = 0.0
            update["salas_total"]       = 0
            update["criado_em"]         = datetime.now(timezone.utc).isoformat()

        get_db().table("orgs").upsert(update).execute()
    except Exception as e:
        import logging
        logging.getLogger("salasff.site.admin").error(f"[admin_server_org] {e}")

    return RedirectResponse(f"/admin/server/{guild_id}?saved=1", status_code=303)


@router.post("/server/{guild_id}/commands", response_class=HTMLResponse)
async def admin_server_commands(request: Request, guild_id: str):
    sess = _require_admin(request)
    if not sess:
        return RedirectResponse("/login?next=/admin/")

    form = await request.form()
    all_cmds = ["c", "c1", "c2", "c3", "painel"]
    commands = {cmd: (form.get(f"cmd_{cmd}") == "on") for cmd in all_cmds}

    try:
        get_db().table("guild_commands").upsert({
            "guild_id": guild_id,
            "commands": commands,
        }).execute()
    except Exception as e:
        import logging
        logging.getLogger("salasff.site.admin").error(f"[admin_server_commands] {e}")

    return RedirectResponse(f"/admin/server/{guild_id}?saved=1", status_code=303)


# ── Saques ────────────────────────────────────────────────────────────────

@router.get("/saques", response_class=HTMLResponse)
async def admin_saques(request: Request):
    sess = _require_admin(request)
    if not sess:
        return RedirectResponse("/login?next=/admin/saques")

    try:
        res = (get_db().table("saques").select("*")
               .eq("status", "pendente")
               .order("criado_em", desc=True)
               .execute())
        saques = res.data or []
    except Exception:
        saques = []

    return templates.TemplateResponse("admin/saques.html", {
        "request": request,
        "user":    sess,
        "saques":  saques,
    })


@router.post("/saques/{saque_id}/aprovar")
async def admin_saque_aprovar(request: Request, saque_id: str):
    sess = _require_admin(request)
    if not sess:
        return RedirectResponse("/login")

    try:
        get_db().table("saques").update({
            "status": "aprovado",
            "resolvido_em": datetime.now(timezone.utc).isoformat(),
        }).eq("id", saque_id).execute()
    except Exception as e:
        import logging
        logging.getLogger("salasff.site.admin").error(f"[admin_saque_aprovar] {e}")

    return RedirectResponse("/admin/saques?msg=aprovado", status_code=303)


@router.post("/saques/{saque_id}/rejeitar")
async def admin_saque_rejeitar(request: Request, saque_id: str, motivo: str = Form("")):
    sess = _require_admin(request)
    if not sess:
        return RedirectResponse("/login")

    try:
        res = get_db().table("saques").select("*").eq("id", saque_id).maybe_single().execute()
        saque = res.data

        if saque:
            # Devolve saldo para a org
            gid = saque.get("guild_id", "")
            if gid:
                res_org = get_db().table("orgs").select("saldo_acumulado").eq("guild_id", gid).maybe_single().execute()
                saldo_atual = float((res_org.data or {}).get("saldo_acumulado") or 0)
                novo_saldo = round(saldo_atual + float(saque.get("valor") or 0), 2)
                get_db().table("orgs").update({"saldo_acumulado": novo_saldo}).eq("guild_id", gid).execute()

        get_db().table("saques").update({
            "status": "rejeitado",
            "resolvido_em": datetime.now(timezone.utc).isoformat(),
            "motivo_rejeicao": motivo,
        }).eq("id", saque_id).execute()
    except Exception as e:
        import logging
        logging.getLogger("salasff.site.admin").error(f"[admin_saque_rejeitar] {e}")

    return RedirectResponse("/admin/saques?msg=rejeitado", status_code=303)
