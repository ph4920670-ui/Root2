# utils/database.py — Supabase-based storage
#
# Migração MongoDB → Supabase.
# Interface pública 100% compatível — nenhum cog precisa mudar.
#
# COMPAT: KEYS_PATH, SALAS_PATH, PEDIDOS_PATH, DATA_DIR, _CODE_DIR
# são mantidos como aliases para não quebrar imports existentes.

import uuid, secrets, string, os, logging
from datetime import datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo

_log = logging.getLogger("salasff.db")

BRASILIA = ZoneInfo("America/Sao_Paulo")

# ══════════════════════════════════════════════════════════════
#  SUPABASE CONNECTION
# ══════════════════════════════════════════════════════════════

from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service role key

_client: Client = None


def get_db() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_KEY)
        _log.info("[Supabase] Client criado")
    return _client


def _all(table: str, columns: str = "*", **filters) -> list:
    """Busca TODAS as linhas paginando 1000 de cada vez (evita limite default do PostgREST)."""
    PAGE = 1000
    rows: list = []
    offset = 0
    while True:
        q = get_db().table(table).select(columns)
        for k, v in filters.items():
            q = q.eq(k, v)
        batch = (q.range(offset, offset + PAGE - 1).execute().data) or []
        rows.extend(batch)
        if len(batch) < PAGE:
            break
        offset += PAGE
    return rows


# ══════════════════════════════════════════════════════════════
#  COMPAT — aliases para imports antigos
# ══════════════════════════════════════════════════════════════

# _get_db: alias de compatibilidade para código legado que ainda usa _get_db().
# Retorna um wrapper que suporta subscript ["table"] → SupabaseCollectionCompat.
class _SupabaseCollectionCompat:
    """Camada de compatibilidade para código legado que usa sintaxe MongoDB."""
    def __init__(self, table_name: str):
        self._t = table_name

    def find_one(self, query: dict = None) -> dict | None:
        try:
            q = get_db().table(self._t).select("*")
            for k, v in (query or {}).items():
                if k == "_id":
                    q = q.eq("id", v)
                elif isinstance(v, dict):
                    for op, val in v.items():
                        if op == "$exists":
                            if val:
                                q = q.not_.is_(k, "null")
                            else:
                                q = q.is_(k, "null")
                        elif op == "$ne":
                            q = q.neq(k, val)
                else:
                    q = q.eq(k, v)
            res = q.limit(1).execute()
            return res.data[0] if res.data else None
        except Exception as e:
            _log.error(f"[compat find_one:{self._t}] {e}")
            return None

    def find(self, query: dict = None, projection: dict = None, limit: int = None) -> "_FindResultList":
        try:
            q = get_db().table(self._t).select("*")
            for k, v in (query or {}).items():
                if k == "_id":
                    if isinstance(v, dict) and "$in" in v:
                        q = q.in_("id", v["$in"])
                    else:
                        q = q.eq("id", v)
                elif isinstance(v, dict):
                    for op, val in v.items():
                        if op == "$exists":
                            if val:
                                q = q.not_.is_(k, "null")
                            else:
                                q = q.is_(k, "null")
                        elif op == "$ne":
                            if val is None:
                                q = q.not_.is_(k, "null")
                            else:
                                q = q.neq(k, val)
                        elif op == "$gte":
                            q = q.gte(k, val)
                        elif op == "$lt":
                            q = q.lt(k, val)
                else:
                    q = q.eq(k, v)
            if limit:
                q = q.limit(limit)
            res = q.execute()
            return _FindResultList(res.data or [])
        except Exception as e:
            _log.error(f"[compat find:{self._t}] {e}")
            return _FindResultList()

    def update_one(self, query: dict, update: dict, upsert: bool = False):
        try:
            data = {}
            if "$set" in update:
                data.update({k: v for k, v in update["$set"].items()})
            if "$unset" in update:
                for k in update["$unset"]:
                    data[k] = None
            data.pop("_id", None)

            q_id = query.get("_id")
            if q_id:
                if upsert:
                    data["id"] = q_id
                    get_db().table(self._t).upsert(data).execute()
                else:
                    get_db().table(self._t).update(data).eq("id", q_id).execute()
            else:
                filt = get_db().table(self._t).update(data)
                for k, v in query.items():
                    filt = filt.eq(k, v)
                filt.execute()
        except Exception as e:
            _log.error(f"[compat update_one:{self._t}] {e}")

    def insert_one(self, doc: dict):
        try:
            d = dict(doc)
            if "_id" in d:
                d["id"] = d.pop("_id")
            get_db().table(self._t).insert(d).execute()
        except Exception as e:
            _log.error(f"[compat insert_one:{self._t}] {e}")

    def delete_one(self, query: dict):
        try:
            q_id = query.get("_id")
            if q_id:
                get_db().table(self._t).delete().eq("id", q_id).execute()
        except Exception as e:
            _log.error(f"[compat delete_one:{self._t}] {e}")

    def delete_many(self, query: dict):
        try:
            q = get_db().table(self._t).delete()
            for k, v in (query or {}).items():
                if isinstance(v, dict) and "$in" in v:
                    q = q.in_(k, v["$in"])
                else:
                    q = q.eq(k, v)
            q.execute()
        except Exception as e:
            _log.error(f"[compat delete_many:{self._t}] {e}")

    def count_documents(self, query: dict = None) -> int:
        return len(self.find(query))

    def create_index(self, *args, **kwargs):
        pass  # índices criados via SQL schema


class _FindResultList(list):
    """List subclass retornado por find() que suporta .sort(field, dir).limit(n)."""
    def sort(self, field=None, direction=1, *args, **kwargs):
        if field is None:
            super().sort(*args, **kwargs)
            return None
        reverse = (direction == -1)
        self[:] = sorted(self, key=lambda x: (x.get(field) or ""), reverse=reverse)
        return self

    def limit(self, n: int):
        return _FindResultList(self[:n])


class _SupabaseDBCompat:
    """Compatibilidade com _get_db()["collection"] do MongoDB."""
    def __getitem__(self, table_name: str) -> _SupabaseCollectionCompat:
        return _SupabaseCollectionCompat(table_name)

    def __call__(self):
        return self


_db_compat = _SupabaseDBCompat()


def _get_db():
    """Alias de compatibilidade para código legado. Retorna wrapper MongoDB-like."""
    return _db_compat


DATA_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CODE_DIR  = DATA_DIR
KEYS_PATH    = "keys"
SALAS_PATH   = "salas"
PEDIDOS_PATH = "pedidos"

_lock = Lock()

# Mapeamento para compatibilidade com código que usa _load("salas") etc.
_COMPAT_TABLE = {
    "keys":    "keys",
    "salas":   "salas",
    "pedidos": "pedidos_pix",
}


def _load(col_name_or_path: str) -> dict:
    """Compatibilidade: carrega todos os docs de uma tabela como dict {id: doc}.
    Aceita nomes de coleção antigos (keys, salas, pedidos) ou o path do JSON.
    """
    # Resolve alias
    col = _COMPAT_TABLE.get(col_name_or_path)
    if col is None:
        # Tenta resolver pelo path: extrai basename sem extensão
        import os as _os
        base = _os.path.splitext(_os.path.basename(col_name_or_path))[0]
        col = _COMPAT_TABLE.get(base, base)
    try:
        res = get_db().table(col).select("*").execute()
        return {row["id"]: row for row in (res.data or [])}
    except Exception as e:
        _log.error(f"[_load compat] {col}: {e}")
        return {}


def _save(col_name_or_path: str, data: dict = None) -> None:
    """No-op compat shim — MongoDB _save não existe no Supabase (dados já salvos por upsert)."""
    pass


def _col_convites() -> _SupabaseCollectionCompat:
    """Compat: retorna wrapper MongoDB-like para invites_system."""
    return _SupabaseCollectionCompat("invites_system")


def _now():
    return datetime.now(BRASILIA).isoformat()


def _now_utc():
    return datetime.now(timezone.utc).isoformat()


def _code():
    ch = string.ascii_uppercase + string.digits
    return "-".join("".join(secrets.choice(ch) for _ in range(4)) for _ in range(4))


# ══════════════════════════════════════════════════════════════
#  BOTCONFIG
# ══════════════════════════════════════════════════════════════

_botconfig_cache: dict = {}


def botconfig_load() -> dict:
    """Carrega botconfig do Supabase (com cache em memória)."""
    global _botconfig_cache
    if _botconfig_cache:
        return dict(_botconfig_cache)
    try:
        res = get_db().table("botconfig").select("data").eq("id", "main").maybe_single().execute()
        if res.data:
            _botconfig_cache = dict(res.data.get("data") or {})
            return dict(_botconfig_cache)
    except Exception as e:
        _log.warning(f"[botconfig_load] {e}")
    return {}


def botconfig_save(data: dict):
    """Salva botconfig no Supabase e atualiza cache."""
    global _botconfig_cache
    _botconfig_cache = dict(data)
    try:
        get_db().table("botconfig").upsert({"id": "main", "data": data}).execute()
    except Exception as e:
        _log.error(f"[botconfig_save] {e}")


# ══════════════════════════════════════════════════════════════
#  INIT / PRUNING
# ══════════════════════════════════════════════════════════════

def init_db():
    """Inicializa conexão com Supabase e faz pruning de salas antigas."""
    try:
        get_db()
        _log.info("[init_db] Supabase conectado OK")
    except Exception as e:
        _log.error(f"[init_db] Erro ao conectar Supabase: {e}")
        return

    try:
        limite = (datetime.now(BRASILIA) - timedelta(days=30)).isoformat()
        res = get_db().table("salas").delete().lt("criado_em", limite).execute()
        _log.info(f"[init_db] Pruning salas antigas: {len(res.data or [])} removidas")
    except Exception as ex:
        _log.warning(f"[init_db] Pruning erro: {ex}")


def prunar_salas_antigas(dias=30):
    """Remove salas com mais de N dias."""
    limite = (datetime.now(BRASILIA) - timedelta(days=dias)).isoformat()
    try:
        res = get_db().table("salas").delete().lt("criado_em", limite).execute()
        removidas = len(res.data or [])
        _log.info(f"[pruning] Removidas {removidas} salas antigas (>{dias} dias).")
        return removidas
    except Exception as e:
        _log.error(f"[prunar_salas_antigas] {e}")
        return 0


def flush_all():
    """Compatibilidade — no Supabase todas as escritas são síncronas."""
    pass


# ══════════════════════════════════════════════════════════════
#  KEYS
# ══════════════════════════════════════════════════════════════

def criar_keys(quantia, modo, quantidade, criado_por):
    criadas = []
    docs = []
    for _ in range(quantidade):
        kid  = str(uuid.uuid4())
        code = _code()
        docs.append({
            "id": kid, "code": code, "quantia": quantia,
            "modo": modo, "salas_usadas": 0,
            "criado_por": criado_por, "criado_em": _now(),
            "dono_id": None, "dono_nome": None, "resgatado_em": None,
            "origem": "venda",
        })
        criadas.append({"id": kid, "code": code})
    try:
        get_db().table("keys").insert(docs).execute()
    except Exception as e:
        _log.error(f"[criar_keys] {e}")
    return criadas


def buscar_key(code):
    code = code.upper().strip()
    try:
        res = get_db().table("keys").select("*").eq("code", code).maybe_single().execute()
        return res.data
    except Exception as e:
        _log.error(f"[buscar_key] {e}")
        return None


def validar_key(code):
    row = buscar_key(code)
    if not row:
        return False, "❌ Key não encontrada. Verifique o código.", None
    if row["salas_usadas"] >= row["quantia"]:
        return False, "❌ Esta key não tem mais salas disponíveis.", None
    return True, "OK", row


def resgatar_key(code, user_id, user_nome):
    ok, msg, row = validar_key(code)
    if not ok:
        return False, msg, None
    if row.get("dono_id") and row["dono_id"] != user_id:
        return False, "❌ Esta key já pertence a outro usuário.", None
    try:
        if not row.get("dono_id"):
            get_db().table("keys").update({
                "dono_id":      user_id,
                "dono_nome":    user_nome,
                "resgatado_em": _now(),
            }).eq("id", row["id"]).execute()
        # Relê para retornar dados atualizados
        res = get_db().table("keys").select("*").eq("id", row["id"]).maybe_single().execute()
        return True, "OK", res.data
    except Exception as e:
        _log.error(f"[resgatar_key] {e}")
        return False, "Erro interno ao resgatar key.", None


def consumir_sala_key(key_id):
    try:
        res = get_db().table("keys").select("salas_usadas").eq("id", key_id).maybe_single().execute()
        if res.data:
            novo = (res.data["salas_usadas"] or 0) + 1
            get_db().table("keys").update({"salas_usadas": novo}).eq("id", key_id).execute()
    except Exception as e:
        _log.error(f"[consumir_sala_key] {e}")


def saldo_total_usuario(user_id, user_nome=None):
    if user_nome:
        _sincronizar_pendentes(user_id, user_nome)
    try:
        res = get_db().table("keys").select("quantia,salas_usadas").eq("dono_id", str(user_id)).execute()
        keys = res.data or []
        return sum(
            (k["quantia"] - k["salas_usadas"])
            for k in keys
            if k["salas_usadas"] < k["quantia"]
        )
    except Exception as e:
        _log.error(f"[saldo_total_usuario] {e}")
        return 0


def usuarios_com_saldo() -> list:
    """Retorna lista única de user_ids com saldo > 0."""
    try:
        keys = _all("keys", "dono_id,quantia,salas_usadas")
        ativos = set()
        for k in keys:
            dono = k.get("dono_id")
            if not dono or str(dono).startswith("PENDENTE_"):
                continue
            if k["salas_usadas"] < k["quantia"]:
                ativos.add(str(dono))
        return list(ativos)
    except Exception as e:
        _log.error(f"[usuarios_com_saldo] {e}")
        return []


# Prioridade de consumo: venda → ranking → indicacao
_ORIGEM_PRIORIDADE = {"venda": 0, "ranking": 1, "indicacao": 2}


def _origem_de(k: dict) -> str:
    o = k.get("origem")
    if o in ("venda", "ranking", "indicacao"):
        return o
    return "venda"


def reservar_sala_key(user_id, modo, user_nome=None):
    """Atômico: encontra a melhor key com saldo e consome 1 sala."""
    if user_nome:
        _sincronizar_pendentes(user_id, user_nome)
    with _lock:
        try:
            res = get_db().table("keys").select("*").eq("dono_id", str(user_id)).execute()
            keys = res.data or []
            elegiveis = [k for k in keys
                         if k["salas_usadas"] < k["quantia"]
                         and (k["modo"] == modo or k["modo"] == 0)]
            if not elegiveis:
                return None, 0

            # Ordena por prioridade de origem, depois resgatado_em desc
            candidatas = sorted(elegiveis, key=lambda k: k.get("resgatado_em") or "", reverse=True)
            candidatas = sorted(candidatas, key=lambda k: _ORIGEM_PRIORIDADE.get(_origem_de(k), 99))

            chosen = candidatas[0]
            novo_usado = chosen["salas_usadas"] + 1
            get_db().table("keys").update({"salas_usadas": novo_usado}).eq("id", chosen["id"]).execute()
            chosen["salas_usadas"] = novo_usado
            restante = chosen["quantia"] - novo_usado
            return dict(chosen), restante
        except Exception as e:
            _log.error(f"[reservar_sala_key] {e}")
            return None, 0


def reverter_sala_key(key_id):
    """Reverte 1 sala consumida (rollback se a API falhar)."""
    try:
        res = get_db().table("keys").select("salas_usadas").eq("id", key_id).maybe_single().execute()
        if res.data and res.data["salas_usadas"] > 0:
            novo = res.data["salas_usadas"] - 1
            get_db().table("keys").update({"salas_usadas": novo}).eq("id", key_id).execute()
    except Exception as e:
        _log.error(f"[reverter_sala_key] {e}")


def _sincronizar_pendentes(user_id, user_nome):
    """Sincroniza keys com dono PENDENTE_ para o user_id real."""
    try:
        nome_lower = user_nome.lower().strip()
        # Filtra direto no Supabase — evita paginar 10k+ rows
        res = get_db().table("keys").select("id,dono_id,dono_nome").filter(
            "dono_id", "like", "PENDENTE_%"
        ).execute()
        keys = res.data or []
        for k in keys:
            dono = k.get("dono_id") or ""
            nome_key = dono.replace("PENDENTE_", "").replace("_", " ").lower().strip()
            if nome_key == nome_lower or (k.get("dono_nome") or "").lower().strip() == nome_lower:
                get_db().table("keys").update({
                    "dono_id": user_id,
                    "dono_nome": user_nome,
                }).eq("id", k["id"]).execute()
    except Exception as e:
        _log.warning(f"[_sincronizar_pendentes] {e}")


def keys_do_usuario(user_id, user_nome=None):
    if user_nome:
        _sincronizar_pendentes(user_id, user_nome)
    try:
        res = get_db().table("keys").select("*").eq("dono_id", str(user_id)).execute()
        keys = res.data or []
        return sorted(keys, key=lambda k: k.get("resgatado_em") or "", reverse=True)
    except Exception as e:
        _log.error(f"[keys_do_usuario] {e}")
        return []


def todas_keys_com_saldo():
    try:
        keys = _all("keys")
        return sorted(
            [k for k in keys if k.get("dono_id") and k["salas_usadas"] < k["quantia"]],
            key=lambda k: (k.get("dono_nome") or "").lower()
        )
    except Exception as e:
        _log.error(f"[todas_keys_com_saldo] {e}")
        return []


def remover_salas_cliente(user_id, quantidade):
    try:
        res = get_db().table("keys").select("*").eq("dono_id", str(user_id)).execute()
        user_keys = sorted(
            [k for k in (res.data or []) if k["salas_usadas"] < k["quantia"]],
            key=lambda k: k.get("resgatado_em") or ""
        )
        removidas = 0
        for k in user_keys:
            if removidas >= quantidade:
                break
            disp  = k["quantia"] - k["salas_usadas"]
            remov = min(disp, quantidade - removidas)
            novo_usado = k["salas_usadas"] + remov
            get_db().table("keys").update({"salas_usadas": novo_usado}).eq("id", k["id"]).execute()
            k["salas_usadas"] = novo_usado
            removidas += remov

        # Recalcula total restante
        res2 = get_db().table("keys").select("quantia,salas_usadas").eq("dono_id", str(user_id)).execute()
        total = sum(
            k["quantia"] - k["salas_usadas"]
            for k in (res2.data or [])
            if k["salas_usadas"] < k["quantia"]
        )
        return removidas, int(total)
    except Exception as e:
        _log.error(f"[remover_salas_cliente] {e}")
        return 0, 0


def adicionar_saldo_usuario(user_id, user_nome, quantidade, origem: str = "venda"):
    """Adiciona saldo creditando uma key."""
    if origem not in ("venda", "ranking", "indicacao"):
        origem = "venda"
    kid  = str(uuid.uuid4())
    code = _code()
    try:
        get_db().table("keys").insert({
            "id": kid, "code": code, "quantia": quantidade,
            "modo": 0, "salas_usadas": 0,
            "criado_por": "admin_restauracao", "criado_em": _now(),
            "dono_id": user_id, "dono_nome": user_nome, "resgatado_em": _now(),
            "origem": origem,
        }).execute()
    except Exception as e:
        _log.error(f"[adicionar_saldo_usuario] {e}")
    return code


def saldo_por_origem(user_id: str) -> dict:
    """Retorna o saldo do user separado por origem: {venda, ranking, indicacao}."""
    try:
        res = get_db().table("keys").select("quantia,salas_usadas,origem").eq("dono_id", str(user_id)).execute()
        keys = res.data or []
        out = {"venda": 0, "ranking": 0, "indicacao": 0}
        for k in keys:
            if k["salas_usadas"] >= k["quantia"]:
                continue
            rest = k["quantia"] - k["salas_usadas"]
            out[_origem_de(k)] += rest
        return out
    except Exception as e:
        _log.error(f"[saldo_por_origem] {e}")
        return {"venda": 0, "ranking": 0, "indicacao": 0}


# ══════════════════════════════════════════════════════════════
#  SALAS
# ══════════════════════════════════════════════════════════════

def registrar_sala(user_id, user_nome, modo, guild_id=None, saldo_origem="pessoal"):
    sid = str(uuid.uuid4())
    try:
        get_db().table("salas").insert({
            "id": sid, "user_id": user_id, "user_nome": user_nome,
            "modo": modo, "pedidoid": None, "sala_id": None,
            "sala_senha": None, "sala_nome": None, "criado_em": _now(),
            "guild_id": str(guild_id) if guild_id else None,
            "saldo_origem": saldo_origem,
        }).execute()
    except Exception as e:
        _log.error(f"[registrar_sala] {e}")
    return sid


def atualizar_sala(sid, pedidoid, sala_id, senha, nome, link=None):
    try:
        update_data = {"pedidoid": pedidoid, "sala_id": sala_id, "sala_senha": senha, "sala_nome": nome}
        if link:
            update_data["sala_link"] = link
        get_db().table("salas").update(update_data).eq("id", sid).execute()
    except Exception as e:
        _log.error(f"[atualizar_sala] {e}")


def salas_usuario_periodo(user_id, horas):
    desde = (datetime.now(BRASILIA) - timedelta(hours=horas)).isoformat()
    try:
        res = get_db().table("salas").select("id").eq("user_id", str(user_id)).gte("criado_em", desde).execute()
        return len(res.data or [])
    except Exception as e:
        _log.error(f"[salas_usuario_periodo] {e}")
        return 0


def salas_usuario_ontem(user_id):
    agora = datetime.now(BRASILIA)
    hoje_meia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_meia = (hoje_meia - timedelta(days=1)).isoformat()
    ate_ontem = hoje_meia.isoformat()
    try:
        res = (get_db().table("salas").select("id")
               .eq("user_id", str(user_id))
               .gte("criado_em", ontem_meia)
               .lt("criado_em", ate_ontem)
               .execute())
        return len(res.data or [])
    except Exception as e:
        _log.error(f"[salas_usuario_ontem] {e}")
        return 0


def perfil_usuario(user_id):
    """Retorna estatísticas de salas e saldo do usuário."""
    agora = datetime.now(BRASILIA)
    hoje_meia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_meia = hoje_meia - timedelta(days=1)
    desde_hoje = hoje_meia.isoformat()
    desde_ontem = ontem_meia.isoformat()
    ate_ontem = hoje_meia.isoformat()

    try:
        res_salas = get_db().table("salas").select("criado_em").eq("user_id", str(user_id)).execute()
        user_salas = res_salas.data or []

        res_keys = get_db().table("keys").select("quantia,salas_usadas").eq("dono_id", str(user_id)).execute()
        keys = res_keys.data or []

        def _contar_desde(dias):
            desde = (agora - timedelta(days=dias)).isoformat()
            return sum(1 for s in user_salas if (s["criado_em"] or "") >= desde)

        hoje_count = sum(1 for s in user_salas if (s["criado_em"] or "") >= desde_hoje)
        ontem_count = sum(1 for s in user_salas
                          if desde_ontem <= (s["criado_em"] or "") < ate_ontem)
        saldo = sum(
            k["quantia"] - k["salas_usadas"]
            for k in keys
            if k["salas_usadas"] < k["quantia"]
        )

        return {
            "hoje":   hoje_count,
            "ontem":  ontem_count,
            "3dias":  _contar_desde(3),
            "semana": _contar_desde(7),
            "mes":    _contar_desde(30),
            "total":  len(user_salas),
            "saldo":  saldo,
        }
    except Exception as e:
        _log.error(f"[perfil_usuario] {e}")
        return {"hoje": 0, "ontem": 0, "3dias": 0, "semana": 0, "mes": 0, "total": 0, "saldo": 0}


# ══════════════════════════════════════════════════════════════
#  PEDIDOS PIX
# ══════════════════════════════════════════════════════════════

def expirar_pedidos_velhos():
    limite = (datetime.now(BRASILIA) - timedelta(hours=2)).isoformat()
    try:
        get_db().table("pedidos_pix").update({"status": "expirado"}).eq("status", "pendente").lt("criado_em", limite).execute()
    except Exception as e:
        _log.error(f"[expirar_pedidos_velhos] {e}")


def criar_pedido_pix(user_id, user_nome, txid, quantia, valor, banco: str = None, guild_id: str = None, guild_nome: str = None):
    pid = str(uuid.uuid4())
    try:
        get_db().table("pedidos_pix").insert({
            "id": pid, "user_id": user_id, "user_nome": user_nome,
            "txid": txid, "quantia": quantia, "valor": valor,
            "status": "pendente", "criado_em": _now(),
            "pago_em": None, "key_gerada": None,
            "banco": banco or "mistic",
            "guild_id": str(guild_id) if guild_id else None,
            "guild_nome": guild_nome,
        }).execute()
    except Exception as e:
        _log.error(f"[criar_pedido_pix] {e}")
    return pid


def buscar_pedido_por_txid(txid):
    try:
        res = get_db().table("pedidos_pix").select("*").eq("txid", str(txid)).maybe_single().execute()
        return res.data
    except Exception as e:
        _log.error(f"[buscar_pedido_por_txid] {e}")
        return None


def confirmar_pedido_pix(txid, nome_pagador: str = None, endtoend: str = None):
    try:
        update_data = {"status": "pago", "pago_em": _now()}
        if nome_pagador:
            update_data["nome_pagador"] = nome_pagador
        if endtoend:
            update_data["endtoend"] = endtoend
        get_db().table("pedidos_pix").update(update_data).eq("txid", str(txid)).execute()
    except Exception as e:
        _log.error(f"[confirmar_pedido_pix] {e}")


def pedidos_pendentes():
    try:
        res = get_db().table("pedidos_pix").select("*").eq("status", "pendente").execute()
        return res.data or []
    except Exception as e:
        _log.error(f"[pedidos_pendentes] {e}")
        return []


def salvar_key_pedido(txid, key_code):
    try:
        get_db().table("pedidos_pix").update({"key_gerada": key_code}).eq("txid", str(txid)).execute()
    except Exception as e:
        _log.error(f"[salvar_key_pedido] {e}")


def pedidos_por_nome(nome, limite=None):
    try:
        res = get_db().table("pedidos_pix").select("*").ilike("user_nome", f"%{nome}%").execute()
        result = sorted(res.data or [], key=lambda p: p["criado_em"] or "", reverse=True)
        return result[:limite] if limite else result
    except Exception as e:
        _log.error(f"[pedidos_por_nome] {e}")
        return []


def pedidos_por_id(user_id, limite=None):
    try:
        res = get_db().table("pedidos_pix").select("*").eq("user_id", str(user_id)).execute()
        result = sorted(res.data or [], key=lambda p: p["criado_em"] or "", reverse=True)
        return result[:limite] if limite else result
    except Exception as e:
        _log.error(f"[pedidos_por_id] {e}")
        return []


def vendas_usuario(user_id: str = None) -> dict:
    agora   = datetime.now(BRASILIA)
    hoje    = agora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    ontem_i = (agora.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)).isoformat()
    d7      = (agora - timedelta(days=7)).isoformat()

    try:
        q = get_db().table("pedidos_pix").select("quantia,pago_em,user_id").eq("status", "pago")
        if user_id:
            q = q.eq("user_id", str(user_id))
        res = q.execute()
        pagos = res.data or []

        v_hoje  = sum(p["quantia"] for p in pagos if (p.get("pago_em") or "") >= hoje)
        v_ontem = sum(p["quantia"] for p in pagos if ontem_i <= (p.get("pago_em") or "") < hoje)
        v7d     = sum(p["quantia"] for p in pagos if (p.get("pago_em") or "") >= d7)
        vtot    = sum(p["quantia"] for p in pagos)

        return {"hoje": v_hoje, "ontem": v_ontem, "semana": v7d, "total": vtot}
    except Exception as e:
        _log.error(f"[vendas_usuario] {e}")
        return {"hoje": 0, "ontem": 0, "semana": 0, "total": 0}


def salas_criadas_usuario(user_id: str) -> dict:
    agora   = datetime.now(BRASILIA)
    hoje_d  = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    hoje    = hoje_d.isoformat()
    ontem_i = (hoje_d - timedelta(days=1)).isoformat()
    d7      = (agora - timedelta(days=7)).isoformat()

    try:
        res = get_db().table("salas").select("criado_em").eq("user_id", str(user_id)).execute()
        minhas = res.data or []

        def _ts(s):
            return s.get("criado_em") or ""

        return {
            "hoje":   sum(1 for s in minhas if _ts(s) >= hoje),
            "ontem":  sum(1 for s in minhas if ontem_i <= _ts(s) < hoje),
            "semana": sum(1 for s in minhas if _ts(s) >= d7),
            "total":  len(minhas),
        }
    except Exception as e:
        _log.error(f"[salas_criadas_usuario] {e}")
        return {"hoje": 0, "ontem": 0, "semana": 0, "total": 0}


def lucro_resumo(user_id: str = None) -> dict:
    agora   = datetime.now(BRASILIA)
    hoje_d  = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    hoje    = hoje_d.isoformat()
    ontem_i = (hoje_d - timedelta(days=1)).isoformat()
    d7      = (agora - timedelta(days=7)).isoformat()

    try:
        res = get_db().table("pedidos_pix").select("quantia,valor,pago_em").eq("status", "pago").execute()
        pagos = res.data or []
    except Exception as e:
        _log.error(f"[lucro_resumo] pedidos {e}")
        pagos = []

    def _no_periodo(p, ini, fim=None):
        ts = p.get("pago_em") or ""
        if fim:
            return ini <= ts < fim
        return ts >= ini

    def _agg(filtro):
        salas = sum(int(p.get("quantia", 0)) for p in pagos if filtro(p))
        receita = sum(float(p.get("valor", 0) or 0) for p in pagos if filtro(p))
        return salas, receita

    s_hoje, r_hoje   = _agg(lambda p: _no_periodo(p, hoje))
    s_ont,  r_ont    = _agg(lambda p: _no_periodo(p, ontem_i, hoje))
    s_7d,   r_7d     = _agg(lambda p: _no_periodo(p, d7))
    s_tot,  r_tot    = _agg(lambda p: True)

    # Bônus dado: keys com criado_por começando em "EVENTO_" + bonus_resgatado total
    try:
        all_keys = _all("keys", "quantia,criado_por,criado_em")
    except Exception:
        all_keys = []

    def _bonus_keys_periodo(ini, fim=None):
        total = 0
        for k in all_keys:
            if not str(k.get("criado_por", "")).startswith(("EVENTO_", "bonus_evento_")):
                continue
            ts = k.get("criado_em") or ""
            if fim:
                if ini <= ts < fim:
                    total += int(k.get("quantia", 0))
            else:
                if ts >= ini:
                    total += int(k.get("quantia", 0))
        return total

    b_hoje  = _bonus_keys_periodo(hoje)
    b_ont   = _bonus_keys_periodo(ontem_i, hoje)
    b_7d    = _bonus_keys_periodo(d7)
    b_tot   = sum(int(k.get("quantia", 0)) for k in all_keys
                  if str(k.get("criado_por", "")).startswith(("EVENTO_", "bonus_evento_")))

    try:
        res_bonus = get_db().table("bonus_data").select("bonus_resgatado").execute()
        bonus_resgatado_tot = sum(int(r.get("bonus_resgatado", 0)) for r in (res_bonus.data or []))
        b_tot += bonus_resgatado_tot
    except Exception:
        pass

    valor_compra = 0.03
    if user_id:
        try:
            cfg = lucro_config_get(user_id)
            v = float(cfg.get("valor_por_sala", 0) or 0)
            if v > 0:
                valor_compra = v
        except Exception:
            pass

    def _calc(salas, receita, bonus):
        custo = (salas + bonus) * valor_compra
        return {
            "salas":       salas,
            "receita":     round(receita, 2),
            "bonus":       bonus,
            "custo":       round(custo, 2),
            "perda_bonus": round(bonus * valor_compra, 2),
            "lucro":       round(receita - custo, 2),
        }

    return {
        "valor_compra_por_sala": valor_compra,
        "hoje":   _calc(s_hoje, r_hoje, b_hoje),
        "ontem":  _calc(s_ont,  r_ont,  b_ont),
        "semana": _calc(s_7d,   r_7d,   b_7d),
        "total":  _calc(s_tot,  r_tot,  b_tot),
    }


def lucro_periodo():
    agora  = datetime.now(BRASILIA)
    hoje   = agora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    d3     = (agora - timedelta(days=3)).isoformat()
    d7     = (agora - timedelta(days=7)).isoformat()

    try:
        todas = _all("salas", "criado_em")
        c_hoje = sum(1 for s in todas if (s["criado_em"] or "") >= hoje)
        c3d    = sum(1 for s in todas if (s["criado_em"] or "") >= d3)
        c7d    = sum(1 for s in todas if (s["criado_em"] or "") >= d7)
        ctot   = len(todas)

        res_ped = get_db().table("pedidos_pix").select("quantia,pago_em").eq("status", "pago").execute()
        pagos = res_ped.data or []
        v_hoje  = sum(p["quantia"] for p in pagos if (p["pago_em"] or "") >= hoje)
        v3d     = sum(p["quantia"] for p in pagos if (p["pago_em"] or "") >= d3)
        v7d     = sum(p["quantia"] for p in pagos if (p["pago_em"] or "") >= d7)
        vtot    = sum(p["quantia"] for p in pagos)

        lps = 0.03
        return {
            "vendidas_hoje": v_hoje, "lucro_hoje": round(v_hoje * lps, 2),
            "vendidas_3d":   v3d,    "lucro_3d":  round(v3d  * lps, 2),
            "vendidas_7d":   v7d,    "lucro_7d":  round(v7d  * lps, 2),
            "vendidas_tot":  vtot,   "lucro_tot": round(vtot * lps, 2),
            "criadas_hoje":  c_hoje,
            "criadas_3d":    c3d,
            "criadas_7d":    c7d,
            "criadas_tot":   ctot,
        }
    except Exception as e:
        _log.error(f"[lucro_periodo] {e}")
        return {k: 0 for k in ["vendidas_hoje","lucro_hoje","vendidas_3d","lucro_3d","vendidas_7d","lucro_7d","vendidas_tot","lucro_tot","criadas_hoje","criadas_3d","criadas_7d","criadas_tot"]}


def stats_globais():
    agora = datetime.now(BRASILIA)
    hoje_meia  = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_meia = hoje_meia - timedelta(days=1)
    desde_hoje  = hoje_meia.isoformat()
    desde_ontem = ontem_meia.isoformat()
    ate_ontem   = hoje_meia.isoformat()
    d7   = (agora - timedelta(days=7)).isoformat()

    try:
        salas = _all("salas", "criado_em")
        pagos = _all("pedidos_pix", "quantia,valor,pago_em", status="pago")

        return {
            "salas_hoje":   sum(1 for s in salas if (s["criado_em"] or "") >= desde_hoje),
            "salas_ontem":  sum(1 for s in salas if desde_ontem <= (s["criado_em"] or "") < ate_ontem),
            "salas_7d":     sum(1 for s in salas if (s["criado_em"] or "") >= d7),
            "salas_tot":    len(salas),
            "vendas_hoje":  sum(p["quantia"] for p in pagos if (p["pago_em"] or "") >= desde_hoje),
            "vendas_ontem": sum(p["quantia"] for p in pagos if desde_ontem <= (p["pago_em"] or "") < ate_ontem),
            "vendas_7d":    sum(p["quantia"] for p in pagos if (p["pago_em"] or "") >= d7),
            "vendas_tot":   sum(p["quantia"] for p in pagos),
            "pedidos_hoje":  sum(1 for p in pagos if (p["pago_em"] or "") >= desde_hoje),
            "pedidos_ontem": sum(1 for p in pagos if desde_ontem <= (p["pago_em"] or "") < ate_ontem),
            "pedidos_7d":    sum(1 for p in pagos if (p["pago_em"] or "") >= d7),
            "pedidos_tot":   len(pagos),
            "receita_hoje":  sum(float(p["valor"] or 0) for p in pagos if (p["pago_em"] or "") >= desde_hoje),
            "receita_ontem": sum(float(p["valor"] or 0) for p in pagos if desde_ontem <= (p["pago_em"] or "") < ate_ontem),
            "receita_7d":    sum(float(p["valor"] or 0) for p in pagos if (p["pago_em"] or "") >= d7),
            "receita_tot":   sum(float(p["valor"] or 0) for p in pagos),
        }
    except Exception as e:
        _log.error(f"[stats_globais] {e}")
        return {k: 0 for k in ["salas_hoje","salas_ontem","salas_7d","salas_tot","vendas_hoje","vendas_ontem","vendas_7d","vendas_tot","pedidos_hoje","pedidos_ontem","pedidos_7d","pedidos_tot","receita_hoje","receita_ontem","receita_7d","receita_tot"]}


def ultimas_compras(limite=None):
    try:
        res = get_db().table("pedidos_pix").select("*").eq("status", "pago").order("pago_em", desc=True).execute()
        result = res.data or []
        return result[:limite] if limite else result
    except Exception as e:
        _log.error(f"[ultimas_compras] {e}")
        return []


def top_compradores(limite=None):
    try:
        res = get_db().table("pedidos_pix").select("user_id,user_nome,quantia,valor").eq("status", "pago").execute()
        pagos = res.data or []
        totais = {}
        for p in pagos:
            uid = p["user_id"]
            if uid not in totais:
                totais[uid] = {"user_nome": p["user_nome"], "pedidos": 0, "total_salas": 0, "total_valor": 0.0}
            totais[uid]["pedidos"]     += 1
            totais[uid]["total_salas"] += p["quantia"]
            totais[uid]["total_valor"] += float(p["valor"] or 0)
        result = sorted(totais.values(), key=lambda x: x["total_salas"], reverse=True)
        return result[:limite] if limite else result
    except Exception as e:
        _log.error(f"[top_compradores] {e}")
        return []


def stats_por_guild(limite=None):
    agora = datetime.now(BRASILIA)
    hoje_meia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    desde_hoje = hoje_meia.isoformat()
    d7 = (agora - timedelta(days=7)).isoformat()

    try:
        res = get_db().table("pedidos_pix").select("guild_id,guild_nome,quantia,valor,pago_em").eq("status", "pago").execute()
        pagos = res.data or []
        grupos = {}
        for p in pagos:
            gid = p.get("guild_id") or "desconhecido"
            gnome = p.get("guild_nome") or ("DM / Direto" if gid == "desconhecido" else f"ID {gid}")
            if gid not in grupos:
                grupos[gid] = {
                    "guild_id": gid, "guild_nome": gnome,
                    "pedidos_hoje": 0, "pedidos_7d": 0, "pedidos_tot": 0,
                    "salas_hoje": 0, "salas_7d": 0, "salas_tot": 0,
                    "receita_hoje": 0.0, "receita_7d": 0.0, "receita_tot": 0.0,
                }
            g = grupos[gid]
            pago_em = p.get("pago_em") or ""
            g["pedidos_tot"] += 1
            g["salas_tot"] += p.get("quantia", 0)
            g["receita_tot"] += float(p.get("valor", 0.0))
            if pago_em >= d7:
                g["pedidos_7d"] += 1
                g["salas_7d"] += p.get("quantia", 0)
                g["receita_7d"] += float(p.get("valor", 0.0))
            if pago_em >= desde_hoje:
                g["pedidos_hoje"] += 1
                g["salas_hoje"] += p.get("quantia", 0)
                g["receita_hoje"] += float(p.get("valor", 0.0))
        result = sorted(grupos.values(), key=lambda x: x["salas_tot"], reverse=True)
        return result[:limite] if limite else result
    except Exception as e:
        _log.error(f"[stats_por_guild] {e}")
        return []


def stats_guild(guild_id: str) -> dict:
    agora = datetime.now(BRASILIA)
    hoje_meia  = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_meia = hoje_meia - timedelta(days=1)
    desde_hoje  = hoje_meia.isoformat()
    desde_ontem = ontem_meia.isoformat()
    ate_ontem   = hoje_meia.isoformat()
    d3  = (agora - timedelta(days=3)).isoformat()
    d7  = (agora - timedelta(days=7)).isoformat()
    d30 = (agora - timedelta(days=30)).isoformat()

    try:
        res = (get_db().table("salas").select("criado_em,user_id,user_nome")
               .eq("guild_id", str(guild_id))
               .eq("saldo_origem", "guild")
               .execute())
        guild_salas = res.data or []

        hoje   = sum(1 for s in guild_salas if (s["criado_em"] or "") >= desde_hoje)
        ontem  = sum(1 for s in guild_salas if desde_ontem <= (s["criado_em"] or "") < ate_ontem)
        tres   = sum(1 for s in guild_salas if (s["criado_em"] or "") >= d3)
        semana = sum(1 for s in guild_salas if (s["criado_em"] or "") >= d7)
        mes    = sum(1 for s in guild_salas if (s["criado_em"] or "") >= d30)
        total  = len(guild_salas)

        criadores: dict = {}
        for s in guild_salas:
            uid = s["user_id"]
            if uid not in criadores:
                criadores[uid] = {"nome": s.get("user_nome", "?"), "total": 0}
            criadores[uid]["total"] += 1
        top3 = sorted(criadores.values(), key=lambda x: x["total"], reverse=True)[:3]

        return {
            "hoje": hoje, "ontem": ontem, "3dias": tres,
            "semana": semana, "mes": mes, "total": total,
            "top3": top3,
        }
    except Exception as e:
        _log.error(f"[stats_guild] {e}")
        return {"hoje": 0, "ontem": 0, "3dias": 0, "semana": 0, "mes": 0, "total": 0, "top3": []}


# ══════════════════════════════════════════════════════════════
#  LUCRO CONFIG POR USUÁRIO
# ══════════════════════════════════════════════════════════════

def lucro_config_get(user_id: str) -> dict:
    try:
        res = get_db().table("lucro_config").select("*").eq("user_id", str(user_id)).maybe_single().execute()
        if res.data:
            return res.data
    except Exception as e:
        _log.warning(f"[lucro_config_get] {e}")
    return {"valor_por_sala": 0, "orgs": [], "go_tempo": 0, "sala_senha": ""}


def lucro_config_set_valor(user_id: str, valor: float):
    try:
        get_db().table("lucro_config").upsert({
            "user_id": str(user_id), "valor_por_sala": valor
        }).execute()
    except Exception as e:
        _log.error(f"[lucro_config_set_valor] {e}")


def lucro_config_add_org(user_id: str, nome: str, guild_id: str, valor: float):
    try:
        cfg = lucro_config_get(user_id)
        orgs = list(cfg.get("orgs") or [])
        orgs.append({"nome": nome, "guild_id": guild_id, "valor": valor})
        get_db().table("lucro_config").upsert({
            "user_id": str(user_id), "orgs": orgs
        }).execute()
    except Exception as e:
        _log.error(f"[lucro_config_add_org] {e}")


def lucro_config_set_org(user_id: str, idx: int, nome: str, guild_id: str, valor: float):
    try:
        cfg = lucro_config_get(user_id)
        orgs = list(cfg.get("orgs") or [])
        if idx < len(orgs):
            orgs[idx] = {"nome": nome, "guild_id": guild_id, "valor": valor}
            get_db().table("lucro_config").upsert({
                "user_id": str(user_id), "orgs": orgs
            }).execute()
    except Exception as e:
        _log.error(f"[lucro_config_set_org] {e}")


def lucro_config_remove_org(user_id: str, idx: int):
    try:
        cfg = lucro_config_get(user_id)
        orgs = list(cfg.get("orgs") or [])
        if idx < len(orgs):
            orgs.pop(idx)
            get_db().table("lucro_config").upsert({
                "user_id": str(user_id), "orgs": orgs
            }).execute()
    except Exception as e:
        _log.error(f"[lucro_config_remove_org] {e}")


def go_config_get(user_id: str) -> int:
    cfg = lucro_config_get(user_id)
    return int(cfg.get("go_tempo") or 0)


def go_config_set(user_id: str, minutos: int):
    try:
        get_db().table("lucro_config").upsert({
            "user_id": str(user_id), "go_tempo": max(1, min(10, minutos))
        }).execute()
    except Exception as e:
        _log.error(f"[go_config_set] {e}")


def senha_config_get(user_id: str) -> str:
    cfg = lucro_config_get(user_id)
    return (cfg.get("sala_senha") or "")


def senha_config_set(user_id: str, senha: str):
    try:
        s = (senha or "").strip()[:32]
        get_db().table("lucro_config").upsert({
            "user_id": str(user_id), "sala_senha": s
        }).execute()
    except Exception as e:
        _log.error(f"[senha_config_set] {e}")


def clientes_por_guild(guild_id: str) -> dict:
    try:
        res = (get_db().table("salas").select("user_id,user_nome")
               .eq("guild_id", str(guild_id))
               .eq("saldo_origem", "guild")
               .execute())
        clientes = {}
        for s in (res.data or []):
            uid = s.get("user_id", "")
            if not uid:
                continue
            if uid not in clientes:
                clientes[uid] = {"nome": s.get("user_nome") or uid, "salas_usadas": 0}
            clientes[uid]["salas_usadas"] += 1
        return clientes
    except Exception as e:
        _log.error(f"[clientes_por_guild] {e}")
        return {}


def salas_usuario_por_guild(user_id: str, guild_id: str, horas: int) -> int:
    desde = (datetime.now(BRASILIA) - timedelta(hours=horas)).isoformat()
    try:
        res = (get_db().table("salas").select("id")
               .eq("user_id", str(user_id))
               .eq("guild_id", str(guild_id))
               .gte("criado_em", desde)
               .execute())
        return len(res.data or [])
    except Exception as e:
        _log.error(f"[salas_usuario_por_guild] {e}")
        return 0


def stats_usuario_por_guilds(user_id: str, guild_ids: list) -> dict:
    agora = datetime.now(BRASILIA)
    hoje_meia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
    ontem_meia = hoje_meia - timedelta(days=1)
    desde_hoje = hoje_meia.isoformat()
    desde_ontem = ontem_meia.isoformat()
    ate_ontem = hoje_meia.isoformat()
    desde_3d = (agora - timedelta(days=3)).isoformat()
    desde_7d = (agora - timedelta(days=7)).isoformat()

    result = {gid: {"hoje": 0, "ontem": 0, "3dias": 0, "7dias": 0, "total": 0} for gid in guild_ids}
    gid_set = set(guild_ids)

    try:
        res = get_db().table("salas").select("guild_id,criado_em").eq("user_id", str(user_id)).execute()
        for s in (res.data or []):
            sgid = s.get("guild_id")
            if sgid not in gid_set:
                continue
            r = result[sgid]
            r["total"] += 1
            criado = s["criado_em"] or ""
            if criado >= desde_hoje:
                r["hoje"] += 1
            elif criado >= desde_ontem and criado < ate_ontem:
                r["ontem"] += 1
            if criado >= desde_3d:
                r["3dias"] += 1
            if criado >= desde_7d:
                r["7dias"] += 1
        return result
    except Exception as e:
        _log.error(f"[stats_usuario_por_guilds] {e}")
        return result


# ══════════════════════════════════════════════════════════════
#  SERVIDOR (GUILD) — saldo de salas por servidor
# ══════════════════════════════════════════════════════════════

_guild_lock = Lock()


def guild_config_get(guild_id: str) -> dict:
    try:
        res = get_db().table("guild_config").select("*").eq("id", str(guild_id)).maybe_single().execute()
        if res and res.data:
            return res.data
    except Exception as e:
        _log.error(f"[guild_config_get] {e}")
    return {"saldo": 0, "cargo_sala_id": None, "criado_por": None, "criado_em": None, "canal_compras_id": None}


def guild_config_set(guild_id: str, cfg: dict):
    with _guild_lock:
        try:
            existing = guild_config_get(guild_id)
            merged = {**existing, **cfg, "id": str(guild_id)}
            merged.pop("criado_em", None)
            get_db().table("guild_config").upsert(merged).execute()
        except Exception as e:
            _log.error(f"[guild_config_set] {e}")


def guild_adicionar_saldo(guild_id: str, quantidade: int, comprador_id: str = None):
    with _guild_lock:
        try:
            cfg = guild_config_get(guild_id)
            novo_saldo = int(cfg.get("saldo") or 0) + quantidade
            dados = {"id": str(guild_id), "saldo": novo_saldo}
            if not cfg.get("criado_por"):
                dados["criado_por"] = comprador_id
            get_db().table("guild_config").upsert(dados).execute()
            return novo_saldo
        except Exception as e:
            _log.error(f"[guild_adicionar_saldo] {e}")
            return 0


def guild_consumir_sala(guild_id: str) -> bool:
    with _guild_lock:
        try:
            cfg = guild_config_get(guild_id)
            if int(cfg.get("saldo") or 0) <= 0:
                return False
            novo_saldo = int(cfg.get("saldo") or 0) - 1
            get_db().table("guild_config").update({"saldo": novo_saldo}).eq("id", str(guild_id)).execute()
            return True
        except Exception as e:
            _log.error(f"[guild_consumir_sala] {e}")
            return False


def guild_reverter_sala(guild_id: str):
    with _guild_lock:
        try:
            cfg = guild_config_get(guild_id)
            novo_saldo = int(cfg.get("saldo") or 0) + 1
            get_db().table("guild_config").update({"saldo": novo_saldo}).eq("id", str(guild_id)).execute()
        except Exception as e:
            _log.error(f"[guild_reverter_sala] {e}")


def guild_set_cargo_sala(guild_id: str, cargo_id: int):
    guild_config_set(str(guild_id), {"cargo_sala_id": cargo_id})


def guild_set_cargo_cliente(guild_id: str, cargo_id):
    guild_config_set(str(guild_id), {"cargo_cliente_id": cargo_id})


def guild_set_cargos_por_qtd(guild_id: str, cargos: dict):
    guild_config_set(str(guild_id), {"cargos_por_qtd": cargos})


def guild_get_cargo_cliente(guild_id: str):
    cfg = guild_config_get(guild_id)
    return cfg.get("cargo_cliente_id")


def guild_get_cargos_por_qtd(guild_id: str) -> dict:
    cfg = guild_config_get(guild_id)
    return cfg.get("cargos_por_qtd") or {}


def guild_set_avaliacao(guild_id: str, canal_id=None, cargo_id=None, ativo=None):
    cfg = guild_config_get(guild_id)
    av = dict(cfg.get("avaliacao") or {})
    if canal_id is not None:
        av["canal_id"] = canal_id
    if cargo_id is not None:
        av["cargo_id"] = cargo_id
    if ativo is not None:
        av["ativo"] = bool(ativo)
    guild_config_set(str(guild_id), {"avaliacao": av})
    avaliacao_rebuild_cache()


def guild_get_avaliacao(guild_id: str) -> dict:
    cfg = guild_config_get(guild_id)
    av = cfg.get("avaliacao") or {}
    return {
        "canal_id": av.get("canal_id"),
        "cargo_id": av.get("cargo_id"),
        "ativo": bool(av.get("ativo", False)),
    }


_avaliacao_cache: dict = {}
_avaliacao_cache_loaded = False


def avaliacao_rebuild_cache():
    global _avaliacao_cache, _avaliacao_cache_loaded
    novo = {}
    try:
        res = get_db().table("guild_config").select("id,avaliacao").execute()
        for row in (res.data or []):
            gid = row["id"]
            av = row.get("avaliacao") or {}
            if av.get("ativo") and av.get("canal_id"):
                novo[int(av["canal_id"])] = {
                    "guild_id": gid,
                    "cargo_id": av.get("cargo_id"),
                }
    except Exception as e:
        _log.error(f"[avaliacao_rebuild_cache] {e}")
    _avaliacao_cache = novo
    _avaliacao_cache_loaded = True


def avaliacao_canal_info(canal_id: int) -> dict | None:
    global _avaliacao_cache_loaded
    if not _avaliacao_cache_loaded:
        avaliacao_rebuild_cache()
    return _avaliacao_cache.get(canal_id)


def guild_set_chat(guild_id: str, canal_id=None, cargo_id=None, ativo=None):
    cfg = guild_config_get(guild_id)
    ch = dict(cfg.get("chat_cmd") or {})
    if canal_id is not None:
        ch["canal_id"] = canal_id
    if cargo_id is not None:
        ch["cargo_id"] = cargo_id
    if ativo is not None:
        ch["ativo"] = bool(ativo)
    guild_config_set(str(guild_id), {"chat_cmd": ch})
    chat_rebuild_cache()


def guild_get_chat(guild_id: str) -> dict:
    cfg = guild_config_get(guild_id)
    ch = cfg.get("chat_cmd") or {}
    return {
        "canal_id": ch.get("canal_id"),
        "cargo_id": ch.get("cargo_id"),
        "ativo": bool(ch.get("ativo", False)),
    }


_chat_cache: dict = {}
_chat_cache_loaded = False


def chat_rebuild_cache():
    global _chat_cache, _chat_cache_loaded
    novo = {}
    try:
        res = get_db().table("guild_config").select("id,chat_cmd").execute()
        for row in (res.data or []):
            gid = row["id"]
            ch = row.get("chat_cmd") or {}
            if ch.get("ativo") and ch.get("canal_id"):
                novo[int(ch["canal_id"])] = {
                    "guild_id": gid,
                    "cargo_id": ch.get("cargo_id"),
                }
    except Exception as e:
        _log.error(f"[chat_rebuild_cache] {e}")
    _chat_cache = novo
    _chat_cache_loaded = True


def chat_canal_info(canal_id: int) -> dict | None:
    global _chat_cache_loaded
    if not _chat_cache_loaded:
        chat_rebuild_cache()
    return _chat_cache.get(canal_id)


# ══════════════════════════════════════════════════════════════
#  SISTEMA DE BÔNUS
# ══════════════════════════════════════════════════════════════

BONUS_RATIO    = 10
BONUS_POR_CICLO = 2


def get_bonus_config() -> tuple:
    try:
        cfg = botconfig_load()
        ratio     = int(cfg.get("bonus_ratio",     BONUS_RATIO))
        por_ciclo = int(cfg.get("bonus_por_ciclo", BONUS_POR_CICLO))
        return max(1, ratio), max(1, por_ciclo)
    except Exception:
        return BONUS_RATIO, BONUS_POR_CICLO


def _calcular_bonus(salas_compradas: int) -> int:
    ratio, por_ciclo = get_bonus_config()
    return (salas_compradas // ratio) * por_ciclo


def bonus_registrar_compra(user_id: str, user_nome: str, salas_compradas: int):
    """Registra a compra e RESGATA AUTOMATICAMENTE qualquer bônus disponível.

    O usuário não precisa mais ir na carteira clicar em "Resgatar": assim que
    completa um ciclo, as salas bônus caem direto no saldo.

    Retorna dict:
        {
          "reg": <registro atualizado>,
          "bonus_concedido": <int salas bônus creditadas agora>,
          "code": <key gerada com o bônus, ou None>,
        }
    """
    try:
        res = get_db().table("bonus_data").select("*").eq("user_id", str(user_id)).maybe_single().execute()
        reg = res.data or {
            "user_id": str(user_id),
            "user_nome": user_nome,
            "total_comprado": 0,
            "bonus_resgatado": 0,
            "historico": [],
        }
        reg["user_nome"] = user_nome
        reg["total_comprado"] = int(reg.get("total_comprado") or 0) + salas_compradas
        historico = list(reg.get("historico") or [])
        historico.append({"salas": salas_compradas, "data": _now()})

        # ── Resgate automático ──────────────────────────────────────────
        total       = int(reg["total_comprado"])
        resgatado   = int(reg.get("bonus_resgatado") or 0)
        bonus_total = _calcular_bonus(total)
        disponivel  = max(0, bonus_total - resgatado)

        bonus_concedido = 0
        code = None
        if disponivel > 0:
            code = adicionar_saldo_usuario(user_id, user_nome, disponivel)
            reg["bonus_resgatado"] = resgatado + disponivel
            bonus_concedido = disponivel
            # registra o evento de bônus no histórico (pra stats de 24h/semana)
            historico.append({"bonus": disponivel, "data": _now()})

        if len(historico) > 100:
            historico = historico[-100:]
        reg["historico"] = historico
        get_db().table("bonus_data").upsert(reg).execute()
        return {"reg": reg, "bonus_concedido": bonus_concedido, "code": code}
    except Exception as e:
        _log.error(f"[bonus_registrar_compra] {e}")
        return {"reg": {}, "bonus_concedido": 0, "code": None}


def bonus_registrar_criacao(user_id: str, user_nome: str, qtd: int = 1):
    """Conta salas CRIADAS para o bônus.

    A cada N salas criadas (bonus_ratio, configurável no /mod) o usuário
    ganha +X salas grátis (bonus_por_ciclo), creditadas automaticamente.
    Reutiliza a mecânica de bonus_registrar_compra — o campo total_comprado
    passa a representar 'salas criadas acumuladas'.
    """
    return bonus_registrar_compra(user_id, user_nome, qtd)


def bonus_ganho_periodo(user_id: str) -> dict:
    """Quanto de bônus (salas grátis) o usuário ganhou nas últimas 24h e 7 dias.

    Lê os eventos {"bonus": n, "data": iso} do histórico de bonus_data.
    Retorna {"dia": int, "semana": int, "total": int}.
    """
    try:
        res = get_db().table("bonus_data").select("historico").eq("user_id", str(user_id)).maybe_single().execute()
        hist = (res.data or {}).get("historico") or []
    except Exception:
        hist = []

    agora = datetime.now(BRASILIA)
    lim_dia    = agora - timedelta(hours=24)
    lim_semana = agora - timedelta(days=7)

    dia = semana = total = 0
    for ev in hist:
        if not isinstance(ev, dict) or "bonus" not in ev:
            continue
        try:
            qtd = int(ev.get("bonus") or 0)
        except (TypeError, ValueError):
            continue
        total += qtd
        dt = None
        try:
            dt = datetime.fromisoformat(str(ev.get("data")))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRASILIA)
        except (TypeError, ValueError):
            dt = None
        if dt is None:
            continue
        if dt >= lim_dia:
            dia += qtd
        if dt >= lim_semana:
            semana += qtd

    return {"dia": dia, "semana": semana, "total": total}


def bonus_info(user_id: str) -> dict:
    try:
        res = get_db().table("bonus_data").select("total_comprado,bonus_resgatado").eq("user_id", str(user_id)).maybe_single().execute()
        reg = res.data or {"total_comprado": 0, "bonus_resgatado": 0}
    except Exception:
        reg = {"total_comprado": 0, "bonus_resgatado": 0}

    total = int(reg.get("total_comprado") or 0)
    resgatado = int(reg.get("bonus_resgatado") or 0)
    ratio, por_ciclo = get_bonus_config()

    bonus_total = _calcular_bonus(total)
    disponivel = max(0, bonus_total - resgatado)

    proximo = ((total // ratio) + 1) * ratio
    falta = proximo - total

    return {
        "total_comprado":  total,
        "bonus_resgatado": resgatado,
        "bonus_total":     bonus_total,
        "bonus_disponivel":disponivel,
        "ratio":           ratio,
        "por_ciclo":       por_ciclo,
        "proximo_bonus":   proximo,
        "falta_proximo":   falta,
    }


def bonus_resgatar(user_id: str, user_nome: str) -> tuple:
    try:
        res = get_db().table("bonus_data").select("*").eq("user_id", str(user_id)).maybe_single().execute()
        reg = res.data
        if not reg or int(reg.get("total_comprado") or 0) == 0:
            return False, 0, "Você ainda não comprou salas suficientes para ter bônus."

        total = int(reg["total_comprado"])
        resgatado = int(reg.get("bonus_resgatado") or 0)
        bonus_total = _calcular_bonus(total)
        disponivel = max(0, bonus_total - resgatado)

        if disponivel <= 0:
            return False, 0, "Sem bônus disponível no momento. Continue comprando para acumular!"

        code = adicionar_saldo_usuario(user_id, user_nome, disponivel)
        get_db().table("bonus_data").update({
            "bonus_resgatado": resgatado + disponivel
        }).eq("user_id", str(user_id)).execute()

        return True, disponivel, code
    except Exception as e:
        _log.error(f"[bonus_resgatar] {e}")
        return False, 0, "Erro interno."


def bonus_resetar(user_id: str) -> tuple:
    try:
        res = get_db().table("bonus_data").select("*").eq("user_id", str(user_id)).maybe_single().execute()
        reg = res.data
        if not reg or int(reg.get("total_comprado") or 0) == 0:
            return False, "Você não tem dados de bônus para resetar."

        total = int(reg["total_comprado"])
        resgatado = int(reg.get("bonus_resgatado") or 0)
        bonus_total = _calcular_bonus(total)
        disponivel = max(0, bonus_total - resgatado)

        if disponivel > 0:
            return False, f"Você tem **{disponivel} sala(s) bônus** pendentes. Resgate antes de resetar!"

        get_db().table("bonus_data").update({
            "total_comprado": 0,
            "bonus_resgatado": 0,
            "historico": [],
        }).eq("user_id", str(user_id)).execute()

        return True, "Faixa resetada! Seu contador voltou a **0**. Compre mais salas para acumular bônus novamente!"
    except Exception as e:
        _log.error(f"[bonus_resetar] {e}")
        return False, "Erro interno."


# ══════════════════════════════════════════════════════════════
#  METAS
# ══════════════════════════════════════════════════════════════

def meta_get(user_id: str) -> dict | None:
    try:
        res = get_db().table("metas").select("*").eq("user_id", str(user_id)).maybe_single().execute()
        return res.data
    except Exception as e:
        _log.warning(f"[meta_get] {e}")
        return None


def meta_set(user_id: str, alvo: int, dias: int):
    try:
        res = get_db().table("salas").select("id").eq("user_id", str(user_id)).execute()
        total_atual = len(res.data or [])
        agora = datetime.now(BRASILIA)
        fim = agora + timedelta(days=dias)
        doc = {
            "user_id": str(user_id),
            "alvo": int(alvo),
            "dias": int(dias),
            "salas_inicio": total_atual,
            "criado_em": agora.isoformat(),
            "expira_em": fim.isoformat(),
        }
        get_db().table("metas").upsert(doc).execute()
        return doc
    except Exception as e:
        _log.error(f"[meta_set] {e}")
        return None


def meta_delete(user_id: str) -> bool:
    try:
        get_db().table("metas").delete().eq("user_id", str(user_id)).execute()
        return True
    except Exception as e:
        _log.warning(f"[meta_delete] {e}")
        return False


def meta_progresso(user_id: str) -> dict | None:
    meta = meta_get(user_id)
    if not meta:
        return None
    try:
        res = get_db().table("salas").select("id").eq("user_id", str(user_id)).execute()
        total_atual = len(res.data or [])
        criadas = max(0, total_atual - int(meta.get("salas_inicio") or 0))
        alvo = int(meta.get("alvo") or 0)

        agora = datetime.now(BRASILIA)
        criado_em = datetime.fromisoformat(meta["criado_em"])
        expira_em = datetime.fromisoformat(meta["expira_em"])
        dias_total = max(1, int(meta.get("dias") or 1))

        segs_passados = max(0, (agora - criado_em).total_seconds())
        segs_restantes = max(0, (expira_em - agora).total_seconds())
        dias_restantes = segs_restantes / 86400.0
        pct = (criadas / alvo * 100) if alvo > 0 else 0
        pct = min(100.0, max(0.0, pct))

        falta = max(0, alvo - criadas)
        media_necessaria = (falta / dias_restantes) if dias_restantes > 0.01 else 0
        media_atual = (criadas / (segs_passados / 86400.0)) if segs_passados > 60 else 0

        expirou = agora >= expira_em
        concluida = criadas >= alvo

        return {
            "alvo": alvo,
            "dias": dias_total,
            "criadas": criadas,
            "falta": falta,
            "pct": pct,
            "dias_restantes": dias_restantes,
            "media_necessaria": media_necessaria,
            "media_atual": media_atual,
            "expirou": expirou,
            "concluida": concluida,
            "criado_em": meta["criado_em"],
            "expira_em": meta["expira_em"],
        }
    except Exception as e:
        _log.error(f"[meta_progresso] {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  TOKEN MODE
# ══════════════════════════════════════════════════════════════

def token_mode_get(user_id: str) -> dict:
    try:
        res = get_db().table("users_config").select("user_token,token_mode_ativo").eq("user_id", str(user_id)).maybe_single().execute()
        doc = (res and res.data) or {}
        return {"token": doc.get("user_token"), "ativo": bool(doc.get("token_mode_ativo", False))}
    except Exception as e:
        _log.error(f"[token_mode_get] {e}")
        return {"token": None, "ativo": False}


def token_mode_set_token(user_id: str, token: str | None):
    try:
        get_db().table("users_config").upsert({
            "user_id": str(user_id), "user_token": token
        }).execute()
    except Exception as e:
        _log.error(f"[token_mode_set_token] {e}")


def token_mode_set_ativo(user_id: str, ativo: bool):
    try:
        get_db().table("users_config").upsert({
            "user_id": str(user_id), "token_mode_ativo": ativo
        }).execute()
    except Exception as e:
        _log.error(f"[token_mode_set_ativo] {e}")


def token_mode_listar_todos() -> list:
    try:
        res = (get_db().table("users_config").select("user_id,user_token,token_mode_ativo,token_mode_servidores")
               .not_.is_("user_token", "null")
               .execute())
        out = []
        for d in (res.data or []):
            uid = d.get("user_id")
            if not uid:
                continue
            out.append({
                "user_id":    str(uid),
                "tem_token":  bool(d.get("user_token")),
                "ativo":      bool(d.get("token_mode_ativo", False)),
                "servidores": [str(s) for s in (d.get("token_mode_servidores") or [])],
            })
        return out
    except Exception as e:
        _log.error(f"[token_mode_listar_todos] {e}")
        return []


def token_mode_servidores_get(user_id: str) -> list:
    try:
        res = get_db().table("users_config").select("token_mode_servidores").eq("user_id", str(user_id)).maybe_single().execute()
        if res.data:
            return [str(s) for s in (res.data.get("token_mode_servidores") or [])]
    except Exception as e:
        _log.error(f"[token_mode_servidores_get] {e}")
    return []


def token_mode_servidores_set(user_id: str, servidores: list):
    try:
        get_db().table("users_config").upsert({
            "user_id": str(user_id),
            "token_mode_servidores": [str(s) for s in servidores]
        }).execute()
    except Exception as e:
        _log.error(f"[token_mode_servidores_set] {e}")


def token_mode_servidor_add(user_id: str, guild_id: str) -> bool:
    try:
        atual = token_mode_servidores_get(user_id)
        gid = str(guild_id)
        if gid in atual:
            return False
        atual.append(gid)
        token_mode_servidores_set(user_id, atual)
        return True
    except Exception as e:
        _log.error(f"[token_mode_servidor_add] {e}")
        return False


def token_mode_servidor_remove(user_id: str, guild_id: str) -> bool:
    try:
        atual = token_mode_servidores_get(user_id)
        gid = str(guild_id)
        if gid not in atual:
            return False
        atual.remove(gid)
        token_mode_servidores_set(user_id, atual)
        return True
    except Exception as e:
        _log.error(f"[token_mode_servidor_remove] {e}")
        return False


def token_mode_dono_do_servidor(guild_id: str) -> str | None:
    try:
        res = get_db().table("users_config").select("user_id,token_mode_servidores,token_mode_ativo,user_token").eq("token_mode_ativo", True).execute()
        gid = str(guild_id)
        for d in (res.data or []):
            if not d.get("user_token"):
                continue
            servs = [str(s) for s in (d.get("token_mode_servidores") or [])]
            if gid in servs:
                return str(d["user_id"])
    except Exception as e:
        _log.error(f"[token_mode_dono_do_servidor] {e}")
    return None


# ══════════════════════════════════════════════════════════════
#  SISTEMA DE CONVITES
# ══════════════════════════════════════════════════════════════

def convite_link_get(inviter_id: str, guild_id: str) -> str | None:
    try:
        doc_id = f"invite:{guild_id}:{inviter_id}"
        res = get_db().table("invites_system").select("codigo").eq("id", doc_id).maybe_single().execute()
        return (res.data or {}).get("codigo")
    except Exception as e:
        _log.error(f"[convite_link_get] {e}")
        return None


def convite_link_set(inviter_id: str, guild_id: str, codigo: str):
    try:
        doc_id = f"invite:{guild_id}:{inviter_id}"
        get_db().table("invites_system").upsert({
            "id": doc_id,
            "tipo": "invite",
            "inviter_id": str(inviter_id),
            "guild_id": str(guild_id),
            "codigo": str(codigo),
            "criado_em": datetime.now(BRASILIA).isoformat(),
        }).execute()
    except Exception as e:
        _log.error(f"[convite_link_set] {e}")


def convidados_resolve_inviter(codigo: str, guild_id: str) -> str | None:
    try:
        res = (get_db().table("invites_system").select("inviter_id")
               .eq("tipo", "invite")
               .eq("codigo", str(codigo))
               .eq("guild_id", str(guild_id))
               .maybe_single()
               .execute())
        return ((res and res.data) or {}).get("inviter_id")
    except Exception as e:
        _log.error(f"[convidados_resolve_inviter] {e}")
        return None


def convidado_registrar(user_id: str, guild_id: str, inviter_id: str, codigo: str, valido: bool, motivo: str = ""):
    try:
        doc_id = f"convidado:{guild_id}:{user_id}"
        res = get_db().table("invites_system").select("id").eq("id", doc_id).maybe_single().execute()
        agora = datetime.now(BRASILIA).isoformat()

        if res.data:
            get_db().table("invites_system").update({
                "valido": False,
                "motivo": "reentrou",
                "saiu": False,
                "rejoined_em": agora,
            }).eq("id", doc_id).execute()
            return

        get_db().table("invites_system").upsert({
            "id": doc_id,
            "tipo": "convidado",
            "user_id": str(user_id),
            "guild_id": str(guild_id),
            "inviter_id": str(inviter_id),
            "codigo": str(codigo),
            "joined_em": agora,
            "valido": bool(valido),
            "motivo": str(motivo),
            "aprovado": False,
            "saiu": False,
        }).execute()
    except Exception as e:
        _log.error(f"[convidado_registrar] {e}")


def convidado_marcar_saiu(user_id: str, guild_id: str):
    try:
        doc_id = f"convidado:{guild_id}:{user_id}"
        get_db().table("invites_system").update({
            "saiu": True, "valido": False, "motivo_saida": "saiu_do_servidor"
        }).eq("id", doc_id).execute()
    except Exception as e:
        _log.error(f"[convidado_marcar_saiu] {e}")


def convidados_validos_24h(inviter_id: str, guild_id: str) -> list:
    try:
        limite = (datetime.now(BRASILIA) - timedelta(hours=24)).isoformat()
        res = (get_db().table("invites_system").select("*")
               .eq("tipo", "convidado")
               .eq("inviter_id", str(inviter_id))
               .eq("guild_id", str(guild_id))
               .eq("valido", True)
               .eq("saiu", False)
               .eq("aprovado", False)
               .gte("joined_em", limite)
               .execute())
        return res.data or []
    except Exception as e:
        _log.error(f"[convidados_validos_24h] {e}")
        return []


def convidados_marcar_aprovados(inviter_id: str, guild_id: str, user_ids: list):
    try:
        agora = datetime.now(BRASILIA).isoformat()
        for uid in user_ids:
            doc_id = f"convidado:{guild_id}:{uid}"
            get_db().table("invites_system").update({
                "aprovado": True, "aprovado_em": agora
            }).eq("id", doc_id).execute()
    except Exception as e:
        _log.error(f"[convidados_marcar_aprovados] {e}")


def convites_canal_aprovacao_get() -> int | None:
    try:
        cfg = botconfig_load() or {}
        v = cfg.get("convites_canal_aprovacao")
        return int(v) if v else None
    except Exception as e:
        _log.error(f"[convites_canal_aprovacao_get] {e}")
        return None


def convites_canal_aprovacao_set(channel_id: int | None):
    try:
        cfg = botconfig_load() or {}
        if channel_id:
            cfg["convites_canal_aprovacao"] = int(channel_id)
        else:
            cfg.pop("convites_canal_aprovacao", None)
        botconfig_save(cfg)
    except Exception as e:
        _log.error(f"[convites_canal_aprovacao_set] {e}")


def token_mode_log_channel_get() -> int | None:
    try:
        cfg = botconfig_load() or {}
        v = cfg.get("token_mode_log_channel")
        return int(v) if v else None
    except Exception as e:
        _log.error(f"[token_mode_log_channel_get] {e}")
        return None


def token_mode_log_channel_set(channel_id: int | None):
    try:
        cfg = botconfig_load() or {}
        if channel_id:
            cfg["token_mode_log_channel"] = int(channel_id)
        else:
            cfg.pop("token_mode_log_channel", None)
        botconfig_save(cfg)
    except Exception as e:
        _log.error(f"[token_mode_log_channel_set] {e}")


# ══════════════════════════════════════════════════════════════
#  RANKING SEMANAL
# ══════════════════════════════════════════════════════════════

def _inicio_semana_db():
    agora = datetime.now(BRASILIA)
    seg   = agora - timedelta(days=agora.weekday())
    return seg.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def top_criadores_semana(limite: int = 10) -> list:
    desde = _inicio_semana_db()
    try:
        res = get_db().table("salas").select("user_id,user_nome,criado_em").gte("criado_em", desde).execute()
        totais: dict = {}
        for s in (res.data or []):
            uid   = s.get("user_id", "?")
            unome = s.get("user_nome") or "Desconhecido"
            if uid not in totais:
                totais[uid] = {"user_id": uid, "user_nome": unome, "total": 0}
            totais[uid]["total"] += 1
        result = sorted(totais.values(), key=lambda x: x["total"], reverse=True)
        return result[:limite] if limite else result
    except Exception as e:
        _log.error(f"[top_criadores_semana] {e}")
        return []


_PREMIOS_TOP5 = [500, 400, 300, 200, 100]


def distribuir_premios_ranking(top5: list) -> list:
    resultados = []
    for idx, u in enumerate(top5[:5]):
        premio = _PREMIOS_TOP5[idx]
        uid    = u["user_id"]
        unome  = u.get("user_nome") or "Desconhecido"
        adicionar_saldo_usuario(uid, unome, premio, "ranking")
        resultados.append((uid, unome, premio))
    return resultados


# ── Ranking diário ────────────────────────────────────────────────────────────

_PREMIOS_SALAS_DEFAULT = [100, 80, 50]
_PREMIOS_REAIS_DEFAULT = [7.0, 5.0, 4.0]


def _inicio_dia_db() -> str:
    agora = datetime.now(BRASILIA)
    return agora.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def top_criadores_hoje(limite: int = 10) -> list:
    desde = _inicio_dia_db()
    try:
        res = get_db().table("salas").select("user_id,user_nome,criado_em").gte("criado_em", desde).execute()
        totais: dict = {}
        for s in (res.data or []):
            uid   = s.get("user_id", "?")
            unome = s.get("user_nome") or "Desconhecido"
            if uid not in totais:
                totais[uid] = {"user_id": uid, "user_nome": unome, "total": 0}
            totais[uid]["total"] += 1
        result = sorted(totais.values(), key=lambda x: x["total"], reverse=True)
        return result[:limite] if limite else result
    except Exception as e:
        _log.error(f"[top_criadores_hoje] {e}")
        return []


def ranking_premios_get() -> dict:
    """Retorna prêmios configurados: {'salas': [100, 80, 50], 'reais': [7.0, 5.0, 4.0]}"""
    try:
        cfg = botconfig_load() or {}
        salas = cfg.get("ranking_premios_salas") or _PREMIOS_SALAS_DEFAULT
        reais = cfg.get("ranking_premios_reais") or _PREMIOS_REAIS_DEFAULT
        return {"salas": list(salas[:3]), "reais": [float(r) for r in reais[:3]]}
    except Exception as e:
        _log.error(f"[ranking_premios_get] {e}")
        return {"salas": list(_PREMIOS_SALAS_DEFAULT), "reais": list(_PREMIOS_REAIS_DEFAULT)}


def ranking_premios_set(salas: list, reais: list):
    try:
        cfg = botconfig_load() or {}
        cfg["ranking_premios_salas"] = [int(s) for s in salas[:3]]
        cfg["ranking_premios_reais"] = [float(r) for r in reais[:3]]
        botconfig_save(cfg)
    except Exception as e:
        _log.error(f"[ranking_premios_set] {e}")


def distribuir_premios_diario(top3: list) -> list:
    """Distribui salas para o top 3 do dia com prêmios configuráveis."""
    premios = ranking_premios_get()
    premios_salas = premios["salas"]
    resultados = []
    for idx, u in enumerate(top3[:3]):
        if idx >= len(premios_salas):
            break
        premio = premios_salas[idx]
        uid    = u["user_id"]
        unome  = u.get("user_nome") or "Desconhecido"
        adicionar_saldo_usuario(uid, unome, premio, "ranking")
        resultados.append((uid, unome, premio))
    return resultados


def ranking_canal_anuncio_get() -> int | None:
    try:
        v = (botconfig_load() or {}).get("ranking_canal_anuncio")
        return int(v) if v else None
    except Exception as e:
        _log.error(f"[ranking_canal_anuncio_get] {e}")
        return None


def ranking_canal_anuncio_set(channel_id: int | None):
    try:
        cfg = botconfig_load() or {}
        if channel_id:
            cfg["ranking_canal_anuncio"] = int(channel_id)
        else:
            cfg.pop("ranking_canal_anuncio", None)
        botconfig_save(cfg)
    except Exception as e:
        _log.error(f"[ranking_canal_anuncio_set] {e}")


def ranking_ativo_get() -> bool:
    try:
        return bool((botconfig_load() or {}).get("ranking_ativo", True))
    except Exception as e:
        _log.error(f"[ranking_ativo_get] {e}")
        return True


def ranking_ativo_set(valor: bool):
    try:
        cfg = botconfig_load() or {}
        cfg["ranking_ativo"] = bool(valor)
        botconfig_save(cfg)
    except Exception as e:
        _log.error(f"[ranking_ativo_set] {e}")


def ranking_ultimo_reset_get() -> str | None:
    try:
        v = (botconfig_load() or {}).get("ranking_ultimo_reset")
        return str(v) if v else None
    except Exception as e:
        _log.error(f"[ranking_ultimo_reset_get] {e}")
        return None


def ranking_ultimo_reset_set(ts: str):
    try:
        cfg = botconfig_load() or {}
        cfg["ranking_ultimo_reset"] = str(ts)
        botconfig_save(cfg)
    except Exception as e:
        _log.error(f"[ranking_ultimo_reset_set] {e}")


# ══════════════════════════════════════════════════════════════
#  PIX Credenciais por Guild
# ══════════════════════════════════════════════════════════════

def pix_creds_get(guild_id: str) -> dict:
    cfg = guild_config_get(str(guild_id))
    return dict(cfg.get("pix_creds") or {})


def pix_creds_set_banco(guild_id: str, banco: str, dados: dict):
    if banco not in ("efi", "mercadopago", "pagbank", "macrodroid"):
        raise ValueError(f"banco inválido: {banco}")
    creds = pix_creds_get(guild_id)
    creds[banco] = dict(dados)
    if not creds.get("banco_ativo"):
        creds["banco_ativo"] = banco
    guild_config_set(str(guild_id), {"pix_creds": creds})


def pix_creds_set_ativo(guild_id: str, banco: str):
    if banco not in ("efi", "mercadopago", "pagbank", "macrodroid"):
        raise ValueError(f"banco inválido: {banco}")
    creds = pix_creds_get(guild_id)
    if banco not in creds:
        raise ValueError(f"banco '{banco}' ainda não foi configurado nesta guild")
    creds["banco_ativo"] = banco
    guild_config_set(str(guild_id), {"pix_creds": creds})


def pix_creds_remove_banco(guild_id: str, banco: str):
    creds = pix_creds_get(guild_id)
    creds.pop(banco, None)
    if creds.get("banco_ativo") == banco:
        creds.pop("banco_ativo", None)
    guild_config_set(str(guild_id), {"pix_creds": creds})


def pix_creds_get_ativo(guild_id: str) -> tuple:
    creds = pix_creds_get(guild_id)
    banco = creds.get("banco_ativo")
    if not banco:
        return (None, {})
    return (banco, dict(creds.get(banco) or {}))


# ── Planos Infinitos ──────────────────────────────────────────────────────────

def planos_inf_ativar(user_id: str, guild_id: str, role_id: str,
                      plano_id: str, duracao_segundos: int):
    """Registra (ou renova) um plano infinito para o usuário."""
    from datetime import datetime, timezone, timedelta
    expires = (datetime.now(timezone.utc) + timedelta(seconds=duracao_segundos)).isoformat()
    db = get_db()
    db.table("planos_inf_ativos").upsert({
        "user_id":    str(user_id),
        "guild_id":   str(guild_id),
        "role_id":    str(role_id),
        "plano_id":   str(plano_id),
        "expires_at": expires,
    }, on_conflict="user_id,guild_id").execute()


def planos_inf_get_usuario(user_id: str, guild_id: str) -> dict | None:
    """Retorna o plano ativo do usuário nessa guild, ou None."""
    db = get_db()
    r = db.table("planos_inf_ativos") \
          .select("*") \
          .eq("user_id", str(user_id)) \
          .eq("guild_id", str(guild_id)) \
          .execute()
    rows = r.data if r else []
    return rows[0] if rows else None


def planos_inf_expirados() -> list:
    """Retorna todos os planos cuja data de expiração já passou."""
    from datetime import datetime, timezone
    agora = datetime.now(timezone.utc).isoformat()
    db = get_db()
    r = db.table("planos_inf_ativos") \
          .select("*") \
          .lt("expires_at", agora) \
          .execute()
    return r.data if r else []


def planos_inf_remover(user_id: str, guild_id: str):
    """Remove o plano ativo do usuário nessa guild."""
    db = get_db()
    db.table("planos_inf_ativos") \
      .delete() \
      .eq("user_id", str(user_id)) \
      .eq("guild_id", str(guild_id)) \
      .execute()
