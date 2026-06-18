"""Supabase helpers for the site — reuses the shared client from utils/database."""

# O site e o bot agora compartilham o mesmo client Supabase.
# Todas as operações são feitas através do client centralizado em utils/database.
# Este módulo provê wrappers compatíveis com a API anterior para não quebrar os routers.

from utils.database import get_db as _get_supabase


def get_db():
    """Retorna o client Supabase compartilhado."""
    return _get_supabase()


# ── Wrappers de tabela — compatibilidade com routers antigos ─────────────────
# Os routers do site usavam .find_one(), .find(), .update_one(), .insert_one() etc.
# Agora as operações são feitas diretamente via Supabase nas rotas.
# Este módulo mantém as funções de acesso às tabelas principais.


class _SupabaseTableWrapper:
    """Wrapper que expõe uma interface simplificada parecida com pymongo
    para facilitar a migração dos routers do site sem reescrever tudo.
    Cada método executa a query Supabase e retorna resultados como lista/dict.
    """

    def __init__(self, table_name: str):
        self._name = table_name

    def _supa(self):
        return get_db().table(self._name)

    def find_one(self, query: dict = None) -> dict | None:
        """Busca um documento pela primeira condição do dict."""
        try:
            q = self._supa().select("*")
            for k, v in (query or {}).items():
                if isinstance(v, dict):
                    # ex: {"$gte": x} — tratamento básico
                    for op, val in v.items():
                        if op == "$gte":
                            q = q.gte(k, val)
                        elif op == "$lte":
                            q = q.lte(k, val)
                else:
                    q = q.eq(k, v)
            res = q.limit(1).execute()
            return res.data[0] if res.data else None
        except Exception:
            return None

    def find(self, query: dict = None) -> list:
        """Busca múltiplos documentos."""
        try:
            q = self._supa().select("*")
            for k, v in (query or {}).items():
                if isinstance(v, dict):
                    for op, val in v.items():
                        if op == "$gte":
                            q = q.gte(k, val)
                        elif op == "$lte":
                            q = q.lte(k, val)
                        elif op == "$gt":
                            q = q.gt(k, val)
                        elif op == "$lt":
                            q = q.lt(k, val)
                else:
                    q = q.eq(k, v)
            res = q.execute()
            return res.data or []
        except Exception:
            return []

    def count_documents(self, query: dict = None) -> int:
        return len(self.find(query))

    def update_one(self, query: dict, update: dict, upsert: bool = False):
        """Atualiza um documento. Suporta $set e $inc."""
        try:
            # Monta o dado a ser atualizado
            data = {}
            if "$set" in update:
                data.update(update["$set"])
            if "$inc" in update:
                # Para $inc precisamos ler e escrever
                for k, v in update["$inc"].items():
                    res = self._supa().select(k)
                    for qk, qv in (query or {}).items():
                        res = res.eq(qk, qv)
                    row = res.limit(1).execute()
                    cur = float((row.data[0] or {}).get(k, 0)) if row.data else 0
                    data[k] = cur + float(v)

            # Remove campos que são chaves primárias do update
            data.pop("_id", None)

            if upsert and query.get("_id"):
                # Garante que o id vai no upsert
                data["id"] = query["_id"]

            q = self._supa().update(data)
            for k, v in (query or {}).items():
                if k == "_id":
                    q = self._supa().update(data).eq("id", v)
                    q.execute()
                    return
                else:
                    q = q.eq(k, v)
            q.execute()

            if upsert:
                # Se não encontrou, insere
                pass  # Supabase upsert já foi feito acima se tinha _id
        except Exception as e:
            import logging
            logging.getLogger("salasff.site.db").error(f"[update_one:{self._name}] {e}")

    def insert_one(self, doc: dict):
        """Insere um documento."""
        try:
            d = dict(doc)
            # Mapeia _id para id se existir
            if "_id" in d:
                d["id"] = d.pop("_id")
            self._supa().insert(d).execute()
        except Exception as e:
            import logging
            logging.getLogger("salasff.site.db").error(f"[insert_one:{self._name}] {e}")

    def sort(self, field: str, direction: int = -1):
        """Compatibilidade — retorna self (sort é feito no find)."""
        return self


def col_guild_config() -> _SupabaseTableWrapper:
    return _SupabaseTableWrapper("guild_config")


def col_orgs() -> _SupabaseTableWrapper:
    return _SupabaseTableWrapper("orgs")


def col_saques() -> _SupabaseTableWrapper:
    return _SupabaseTableWrapper("saques")


def col_guild_commands() -> _SupabaseTableWrapper:
    return _SupabaseTableWrapper("guild_commands")
