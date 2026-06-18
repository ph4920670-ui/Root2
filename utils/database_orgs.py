# utils/database_orgs.py
# Extensões do banco de dados para o sistema de Orgs, Guild Commands e Saques.
# Importado pelo site e pelo bot. Usa Supabase via utils/database.get_db().

import uuid, logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_log = logging.getLogger("salasff.db")
BRASILIA = ZoneInfo("America/Sao_Paulo")


def _now():
    return datetime.now(BRASILIA).isoformat()


# ── Lazy import do client Supabase para não criar dependência circular ──────────

def _get_supa():
    from utils.database import get_db
    return get_db()


# ══════════════════════════════════════════════════════════════
#  GUILD COMMANDS TOGGLE
# ══════════════════════════════════════════════════════════════

def guild_commands_get(guild_id: str) -> dict:
    """Retorna {comando: bool} para o servidor. Default: todos True."""
    try:
        res = _get_supa().table("guild_commands").select("commands").eq("guild_id", str(guild_id)).maybe_single().execute()
        if res.data:
            return dict(res.data.get("commands") or {})
    except Exception as e:
        _log.warning(f"[guild_commands_get] {e}")
    return {}


def guild_commands_set(guild_id: str, commands: dict):
    """Salva {comando: bool} no Supabase."""
    try:
        _get_supa().table("guild_commands").upsert({
            "guild_id": str(guild_id),
            "commands": dict(commands),
        }).execute()
    except Exception as e:
        _log.error(f"[guild_commands_set] {e}")


def command_enabled(guild_id: str, command_name: str) -> bool:
    """Verifica se um comando está ativo para o servidor. Default True."""
    cmds = guild_commands_get(str(guild_id))
    return bool(cmds.get(command_name, True))


# ══════════════════════════════════════════════════════════════
#  ORGS
# ══════════════════════════════════════════════════════════════

def org_get(guild_id: str) -> dict:
    """Retorna o documento da org ou dict vazio."""
    try:
        res = _get_supa().table("orgs").select("*").eq("guild_id", str(guild_id)).maybe_single().execute()
        if res.data:
            return dict(res.data)
    except Exception as e:
        _log.warning(f"[org_get] {e}")
    return {}


def org_set(guild_id: str, data: dict):
    """Cria ou atualiza o documento da org."""
    try:
        d = dict(data)
        d["guild_id"] = str(guild_id)
        _get_supa().table("orgs").upsert(d).execute()
    except Exception as e:
        _log.error(f"[org_set] {e}")


def org_registrar_venda(guild_id: str, valor: float, qtd_salas: int):
    """Acumula faturamento e saldo da org após uma venda confirmada."""
    try:
        org = org_get(guild_id)
        if not org or not org.get("ativo"):
            return
        porcentagem = float(org.get("porcentagem", 70))
        ganho_org = round(float(valor) * (porcentagem / 100.0), 4)

        novo_fat = round(float(org.get("faturamento_total") or 0) + float(valor), 4)
        novo_saldo = round(float(org.get("saldo_acumulado") or 0) + ganho_org, 4)
        novo_salas = int(org.get("salas_total") or 0) + int(qtd_salas)

        _get_supa().table("orgs").update({
            "faturamento_total": novo_fat,
            "saldo_acumulado": novo_saldo,
            "salas_total": novo_salas,
        }).eq("guild_id", str(guild_id)).execute()

        _log.info(f"[org_registrar_venda] guild={guild_id} valor={valor} ganho={ganho_org}")
    except Exception as e:
        _log.error(f"[org_registrar_venda] {e}")


def org_saldo(guild_id: str) -> float:
    """Retorna o saldo acumulado do dono da org."""
    try:
        res = _get_supa().table("orgs").select("saldo_acumulado").eq("guild_id", str(guild_id)).maybe_single().execute()
        if res.data:
            return float(res.data.get("saldo_acumulado") or 0.0)
    except Exception as e:
        _log.warning(f"[org_saldo] {e}")
    return 0.0


def org_saque_criar(guild_id: str, valor: float, pix_key: str) -> str:
    """Cria registro de saque pendente. Retorna o id do saque."""
    org = org_get(guild_id)
    saldo_atual = float(org.get("saldo_acumulado") or 0.0)
    if round(float(valor), 2) > round(saldo_atual, 2):
        raise ValueError(f"Saldo insuficiente: disponível R$ {saldo_atual:.2f}")

    saque_id = str(uuid.uuid4())
    doc = {
        "id": saque_id,
        "guild_id": str(guild_id),
        "owner_discord_id": str(org.get("owner_discord_id", "")),
        "valor": round(float(valor), 2),
        "pix_key": str(pix_key),
        "status": "pendente",
        "criado_em": _now(),
        "resolvido_em": None,
        "motivo_rejeicao": None,
    }
    _get_supa().table("saques").insert(doc).execute()

    # Desconta o valor do saldo imediatamente (reserva)
    novo_saldo = round(saldo_atual - round(float(valor), 2), 2)
    _get_supa().table("orgs").update({"saldo_acumulado": novo_saldo}).eq("guild_id", str(guild_id)).execute()

    return saque_id


def org_saque_list(status: str = "pendente") -> list:
    """Retorna lista de saques filtrados por status."""
    try:
        res = _get_supa().table("saques").select("*").eq("status", str(status)).execute()
        result = sorted(res.data or [], key=lambda x: x.get("criado_em") or "", reverse=True)
        return result
    except Exception as e:
        _log.error(f"[org_saque_list] {e}")
        return []


def org_saque_aprovar(saque_id: str):
    """Aprova um saque pendente."""
    try:
        _get_supa().table("saques").update({
            "status": "aprovado",
            "resolvido_em": _now(),
        }).eq("id", str(saque_id)).execute()
    except Exception as e:
        _log.error(f"[org_saque_aprovar] {e}")


def org_saque_rejeitar(saque_id: str, motivo: str):
    """Rejeita um saque pendente e devolve o valor ao saldo da org."""
    try:
        res = _get_supa().table("saques").select("*").eq("id", str(saque_id)).maybe_single().execute()
        doc = res.data
        if not doc:
            return
        _get_supa().table("saques").update({
            "status": "rejeitado",
            "resolvido_em": _now(),
            "motivo_rejeicao": str(motivo),
        }).eq("id", str(saque_id)).execute()

        # Devolve o valor ao saldo da org
        org = org_get(str(doc["guild_id"]))
        novo_saldo = round(float(org.get("saldo_acumulado") or 0) + round(float(doc.get("valor", 0)), 2), 2)
        _get_supa().table("orgs").update({"saldo_acumulado": novo_saldo}).eq("guild_id", str(doc["guild_id"])).execute()
    except Exception as e:
        _log.error(f"[org_saque_rejeitar] {e}")


def org_salas_semana(guild_id: str) -> int:
    """Retorna salas criadas com saldo desta org nesta semana."""
    desde = (datetime.now(BRASILIA) - timedelta(days=7)).isoformat()
    try:
        res = (_get_supa().table("salas").select("id")
               .eq("guild_id", str(guild_id))
               .gte("criado_em", desde)
               .execute())
        return len(res.data or [])
    except Exception as e:
        _log.error(f"[org_salas_semana] {e}")
        return 0


def org_salas_mes(guild_id: str) -> int:
    """Retorna salas criadas com saldo desta org neste mês."""
    desde = (datetime.now(BRASILIA) - timedelta(days=30)).isoformat()
    try:
        res = (_get_supa().table("salas").select("id")
               .eq("guild_id", str(guild_id))
               .gte("criado_em", desde)
               .execute())
        return len(res.data or [])
    except Exception as e:
        _log.error(f"[org_salas_mes] {e}")
        return 0


def org_list_all() -> list:
    """Lista todas as orgs cadastradas."""
    try:
        res = _get_supa().table("orgs").select("*").execute()
        return res.data or []
    except Exception as e:
        _log.error(f"[org_list_all] {e}")
        return []
