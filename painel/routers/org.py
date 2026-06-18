"""Org owner panel routes."""

import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from painel.auth import get_session
from utils.database import get_db

router = APIRouter(prefix="/org")
templates = Jinja2Templates(directory="painel/templates")


def _require_org(request: Request):
    """Returns (session, org_doc) or (None, None) if not authorized."""
    sess = get_session(request)
    if not sess:
        return None, None
    uid = sess.get("id", "")
    try:
        res = (get_db().table("orgs").select("*")
               .eq("owner_discord_id", str(uid))
               .eq("ativo", True)
               .maybe_single()
               .execute())
        org = res.data
    except Exception:
        org = None
    if not org:
        return None, None
    return sess, org


# ── Dashboard ─────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def org_dashboard(request: Request):
    sess, org = _require_org(request)
    if not sess:
        if not get_session(request):
            return RedirectResponse("/login?next=/org/")
        return templates.TemplateResponse("org/nao_autorizado.html", {"request": request, "user": get_session(request)})

    guild_id = org["guild_id"]

    now = datetime.now(timezone.utc)
    inicio_semana = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    inicio_mes = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    try:
        res_sem = (get_db().table("salas").select("id")
                   .eq("guild_id", guild_id)
                   .gte("criado_em", inicio_semana)
                   .execute())
        salas_semana = len(res_sem.data or [])

        res_mes = (get_db().table("salas").select("id")
                   .eq("guild_id", guild_id)
                   .gte("criado_em", inicio_mes)
                   .execute())
        salas_mes = len(res_mes.data or [])
    except Exception:
        salas_semana = 0
        salas_mes = 0

    # Price per sala from guild_config
    try:
        res_cfg = get_db().table("guild_config").select("preco_sala").eq("id", guild_id).maybe_single().execute()
        preco_sala = float((res_cfg.data or {}).get("preco_sala") or 1.5)
    except Exception:
        preco_sala = 1.5

    faturamento_semana = salas_semana * preco_sala
    faturamento_mes    = salas_mes    * preco_sala

    porcentagem = float(org.get("porcentagem", 70))
    saldo       = float(org.get("saldo_acumulado") or 0.0)

    try:
        res_saques = (get_db().table("saques").select("*")
                      .eq("guild_id", guild_id)
                      .eq("status", "pendente")
                      .execute())
        saques_pendentes = res_saques.data or []
    except Exception:
        saques_pendentes = []

    return templates.TemplateResponse("org/dashboard.html", {
        "request":            request,
        "user":               sess,
        "org":                org,
        "salas_semana":       salas_semana,
        "salas_mes":          salas_mes,
        "faturamento_semana": faturamento_semana,
        "faturamento_mes":    faturamento_mes,
        "preco_sala":         preco_sala,
        "porcentagem":        porcentagem,
        "saldo":              saldo,
        "saques_pendentes":   saques_pendentes,
    })


# ── Saques ─────────────────────────────────────────────────────────────────

@router.get("/saques", response_class=HTMLResponse)
async def org_saques(request: Request):
    sess, org = _require_org(request)
    if not sess:
        if not get_session(request):
            return RedirectResponse("/login?next=/org/saques")
        return templates.TemplateResponse("org/nao_autorizado.html", {"request": request, "user": get_session(request)})

    guild_id = org["guild_id"]
    try:
        res = (get_db().table("saques").select("*")
               .eq("guild_id", guild_id)
               .order("criado_em", desc=True)
               .execute())
        saques = res.data or []
    except Exception:
        saques = []
    saldo = float(org.get("saldo_acumulado") or 0.0)

    return templates.TemplateResponse("org/saques.html", {
        "request": request,
        "user":    sess,
        "org":     org,
        "saques":  saques,
        "saldo":   saldo,
    })


@router.post("/saques/solicitar")
async def org_saque_solicitar(
    request: Request,
    valor: float = Form(...),
    pix_key: str = Form(...),
):
    sess, org = _require_org(request)
    if not sess:
        return RedirectResponse("/login", status_code=303)

    guild_id = org["guild_id"]
    saldo    = float(org.get("saldo_acumulado") or 0.0)

    if valor <= 0 or valor > saldo:
        return RedirectResponse("/org/saques?erro=saldo_insuficiente", status_code=303)

    saque_id = str(uuid.uuid4())
    novo_saldo = round(saldo - valor, 2)

    try:
        get_db().table("saques").insert({
            "id":               saque_id,
            "guild_id":         guild_id,
            "owner_discord_id": str(sess.get("id", "")),
            "valor":            valor,
            "pix_key":          pix_key.strip(),
            "status":           "pendente",
            "criado_em":        datetime.now(timezone.utc).isoformat(),
            "resolvido_em":     None,
            "motivo_rejeicao":  None,
        }).execute()

        # Deduz do saldo da org
        get_db().table("orgs").update({"saldo_acumulado": novo_saldo}).eq("guild_id", guild_id).execute()
    except Exception as e:
        import logging
        logging.getLogger("salasff.site.org").error(f"[org_saque_solicitar] {e}")
        return RedirectResponse("/org/saques?erro=interno", status_code=303)

    return RedirectResponse("/org/saques?msg=solicitado", status_code=303)
