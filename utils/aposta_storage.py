# utils/aposta_storage.py — Storage MongoDB pro sistema de apostas automáticas
#
# Cada canal de aposta tem uma config (parseada da embed pinada) + uma fila atual.
#
# Coleção: aposta_canais
# {
#   "_id": "<channel_id>",
#   "guild_id": "<guild_id>",
#   "partida_id": "2322541",
#   "modo": 2,                         # 1=Normal, 2=Infinito, 3=Full Capa
#   "modo_label": "1x1 Mobile Gelo Infinito",
#   "max_jogadores": 2,
#   "valor_esperado": 0.90,
#   "jogadores_autorizados": ["123...", "456..."],   # user_ids da whitelist
#   "fila_atual": [                                  # quem já pagou
#     {"user_id": "...", "user_name": "Mika",
#      "nome_pagador": "Mikael Souza", "valor": 0.90,
#      "pix_id": "...", "ts": "ISO"}
#   ],
#   "sala_criada": false,
#   "sala_info": null,                 # {"id": "...", "senha": "...", "modo": 2, ...} quando criada
#   "criado_em": "ISO",
#   "atualizado_em": "ISO",
#   "tentativas_criar_sala": 0,        # contador de retry
#   "lock_ate": null                   # timestamp do cooldown anti-race
# }
#
# Coleção: aposta_pix_orfaos
# {
#   "_id": "<pix_id>",
#   "guild_id": "...",
#   "channel_id": "...",
#   "valor": 0.90,
#   "nome_pagador": "Mikael Souza",
#   "recebido_em": "ISO",
#   "avisado_admin": false,
#   "ts_aviso_dm": null,
#   "resolvido": false,
#   "resolvido_por_admin_id": null,
#   "resolvido_user_id": null
# }

import logging
from datetime import datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo

_log = logging.getLogger("salasff.aposta_storage")

BRASILIA = ZoneInfo("America/Sao_Paulo")


def _now() -> str:
    return datetime.now(BRASILIA).isoformat()


def _now_utc():
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════
#  Acesso ao MongoDB (reusa a conexão de utils/database.py)
# ══════════════════════════════════════════════════════════════

def _col_canais():
    from utils.database import _get_db
    return _get_db()["aposta_canais"]


def _col_orfaos():
    from utils.database import _get_db
    return _get_db()["aposta_pix_orfaos"]


# ══════════════════════════════════════════════════════════════
#  Cache em memória + lock por canal
# ══════════════════════════════════════════════════════════════

_lock = Lock()
_cache: dict[str, dict] = {}   # channel_id (str) -> doc
_locks_canal: dict[str, Lock] = {}  # channel_id -> Lock pra evitar race


def _lock_canal(channel_id: str | int) -> Lock:
    cid = str(channel_id)
    with _lock:
        if cid not in _locks_canal:
            _locks_canal[cid] = Lock()
        return _locks_canal[cid]


# ══════════════════════════════════════════════════════════════
#  Init / índices
# ══════════════════════════════════════════════════════════════

def init_apostas():
    """Cria índices uma vez."""
    try:
        _col_canais().create_index("guild_id")
        _col_canais().create_index("partida_id")
        _col_canais().create_index("sala_criada")
        _col_orfaos().create_index("guild_id")
        _col_orfaos().create_index("resolvido")
        _col_orfaos().create_index("recebido_em")
        _log.info("[init_apostas] Índices OK")
    except Exception as e:
        _log.warning(f"[init_apostas] Erro ao criar índices: {e}")


# ══════════════════════════════════════════════════════════════
#  CRUD canais
# ══════════════════════════════════════════════════════════════

def canal_get(channel_id: str | int) -> dict | None:
    """Retorna config do canal de aposta. None se não for canal de aposta."""
    cid = str(channel_id)
    if cid in _cache:
        return _cache[cid]
    try:
        doc = _col_canais().find_one({"_id": cid})
    except Exception as e:
        _log.warning(f"[canal_get] erro: {e}")
        return None
    if doc:
        _cache[cid] = doc
    return doc


def canal_save(doc: dict):
    """Salva/atualiza canal. doc deve ter _id (channel_id)."""
    cid = str(doc["_id"])
    doc["atualizado_em"] = _now()
    _cache[cid] = doc
    try:
        d = dict(doc)
        d.pop("_id", None)
        _col_canais().update_one({"_id": cid}, {"$set": d}, upsert=True)
    except Exception as e:
        _log.error(f"[canal_save] erro: {e}")


def canal_criar(
    channel_id: str | int,
    guild_id: str | int,
    partida_id: str,
    modo: int,
    modo_label: str,
    max_jogadores: int,
    valor_esperado: float,
    jogadores_autorizados: list[str],
) -> dict:
    """Cria/sobrescreve config do canal a partir do parse da embed."""
    cid = str(channel_id)
    doc = {
        "_id": cid,
        "guild_id": str(guild_id),
        "partida_id": str(partida_id),
        "modo": int(modo),
        "modo_label": modo_label,
        "max_jogadores": int(max_jogadores),
        "valor_esperado": round(float(valor_esperado), 2),
        "jogadores_autorizados": [str(x) for x in jogadores_autorizados],
        "fila_atual": [],
        "sala_criada": False,
        "sala_info": None,
        "criado_em": _now(),
        "atualizado_em": _now(),
        "tentativas_criar_sala": 0,
        "lock_ate": None,
    }
    canal_save(doc)
    return doc


def canal_remover(channel_id: str | int) -> bool:
    """Apaga canal de aposta (quando a partida termina ou canal é deletado)."""
    cid = str(channel_id)
    _cache.pop(cid, None)
    try:
        r = _col_canais().delete_one({"_id": cid})
        return r.deleted_count > 0
    except Exception as e:
        _log.warning(f"[canal_remover] erro: {e}")
        return False


def canal_listar_guild(guild_id: str | int, somente_ativos: bool = True) -> list[dict]:
    """Lista canais de aposta da guild. somente_ativos = sem sala criada ainda."""
    gid = str(guild_id)
    try:
        q = {"guild_id": gid}
        if somente_ativos:
            q["sala_criada"] = False
        return list(_col_canais().find(q))
    except Exception as e:
        _log.warning(f"[canal_listar_guild] erro: {e}")
        return []


# ══════════════════════════════════════════════════════════════
#  Fila de jogadores (com lock por canal pra evitar race)
# ══════════════════════════════════════════════════════════════

def fila_adicionar(
    channel_id: str | int,
    user_id: str | int,
    user_name: str,
    nome_pagador: str,
    valor: float,
    pix_id: str,
) -> tuple[bool, str, dict | None]:
    """Adiciona jogador na fila. Retorna (ok, motivo, canal_doc).
    
    Motivos de falha: 'canal_nao_aposta', 'sala_ja_criada', 'fila_cheia',
    'ja_na_fila', 'nao_autorizado', 'lock'.
    """
    cid = str(channel_id)
    uid = str(user_id)
    with _lock_canal(cid):
        canal = canal_get(cid)
        if not canal:
            return False, "canal_nao_aposta", None
        if canal.get("sala_criada"):
            return False, "sala_ja_criada", canal
        # Lock anti-race (cooldown de 10s após criar)
        lock_ate = canal.get("lock_ate")
        if lock_ate:
            try:
                t = datetime.fromisoformat(lock_ate)
                if t > _now_utc():
                    return False, "lock", canal
            except Exception:
                pass

        # Whitelist desativada: qualquer jogador pode entrar no canal
        # (autorizados ainda é salvo pra exibição em /aposta_status)

        fila = canal.get("fila_atual") or []
        # Anti-duplicata: mesmo user_id não entra 2x
        if any(str(j.get("user_id")) == uid for j in fila):
            return False, "ja_na_fila", canal
        # Fila cheia
        if len(fila) >= int(canal.get("max_jogadores", 2)):
            return False, "fila_cheia", canal

        # Adiciona
        fila.append({
            "user_id": uid,
            "user_name": str(user_name),
            "nome_pagador": str(nome_pagador),
            "valor": round(float(valor), 2),
            "pix_id": str(pix_id),
            "ts": _now(),
        })
        canal["fila_atual"] = fila
        canal_save(canal)
        return True, "ok", canal


def fila_esta_cheia(canal: dict) -> bool:
    return len(canal.get("fila_atual") or []) >= int(canal.get("max_jogadores", 2))


def fila_limpar(channel_id: str | int):
    """Reset da fila (ex: admin pediu, ou após criar sala se quiser permitir nova rodada)."""
    cid = str(channel_id)
    with _lock_canal(cid):
        canal = canal_get(cid)
        if not canal:
            return
        canal["fila_atual"] = []
        canal_save(canal)


def lock_canal_segundos(channel_id: str | int, segundos: int = 10):
    """Trava o canal por N segundos (anti-race após criar sala)."""
    cid = str(channel_id)
    with _lock_canal(cid):
        canal = canal_get(cid)
        if not canal:
            return
        canal["lock_ate"] = (_now_utc() + timedelta(seconds=segundos)).isoformat()
        canal_save(canal)


def marcar_sala_criada(channel_id: str | int, sala_info: dict):
    """Marca canal como tendo sala criada — não aceita mais pg."""
    cid = str(channel_id)
    with _lock_canal(cid):
        canal = canal_get(cid)
        if not canal:
            return
        canal["sala_criada"] = True
        canal["sala_info"] = sala_info
        canal_save(canal)


def incrementar_tentativa_criar_sala(channel_id: str | int) -> int:
    """Incrementa contador de retry. Retorna o novo valor."""
    cid = str(channel_id)
    with _lock_canal(cid):
        canal = canal_get(cid)
        if not canal:
            return 0
        n = int(canal.get("tentativas_criar_sala", 0)) + 1
        canal["tentativas_criar_sala"] = n
        canal_save(canal)
        return n


# ══════════════════════════════════════════════════════════════
#  PIX órfãos (PIX caiu mas ninguém digitou pg)
# ══════════════════════════════════════════════════════════════

def orfao_registrar(
    pix_id: str,
    guild_id: str | int,
    channel_id: str | int | None,
    valor: float,
    nome_pagador: str,
    recebido_em: str,
):
    """Registra um PIX que pode virar órfão se ninguém der pg em 10min."""
    try:
        _col_orfaos().update_one(
            {"_id": str(pix_id)},
            {"$set": {
                "guild_id": str(guild_id),
                "channel_id": str(channel_id) if channel_id else None,
                "valor": round(float(valor), 2),
                "nome_pagador": str(nome_pagador),
                "recebido_em": recebido_em,
                "avisado_admin": False,
                "ts_aviso_dm": None,
                "resolvido": False,
                "resolvido_por_admin_id": None,
                "resolvido_user_id": None,
            }},
            upsert=True,
        )
    except Exception as e:
        _log.warning(f"[orfao_registrar] erro: {e}")


def orfao_marcar_consumido(pix_id: str):
    """PIX foi consumido com sucesso (alguém digitou pg) — não é mais órfão."""
    try:
        _col_orfaos().update_one(
            {"_id": str(pix_id)},
            {"$set": {"resolvido": True, "consumido": True}},
        )
    except Exception as e:
        _log.warning(f"[orfao_marcar_consumido] erro: {e}")


def orfaos_pendentes_avisar(idade_minutos: int = 10) -> list[dict]:
    """Retorna PIX que: passaram de N min sem ser consumidos E ainda não foram avisados."""
    cutoff = (_now_utc() - timedelta(minutes=idade_minutos)).isoformat()
    try:
        return list(_col_orfaos().find({
            "resolvido": False,
            "avisado_admin": False,
            "recebido_em": {"$lt": cutoff},
        }))
    except Exception as e:
        _log.warning(f"[orfaos_pendentes_avisar] erro: {e}")
        return []


def orfao_marcar_avisado(pix_id: str):
    try:
        _col_orfaos().update_one(
            {"_id": str(pix_id)},
            {"$set": {"avisado_admin": True, "ts_aviso_dm": _now()}},
        )
    except Exception as e:
        _log.warning(f"[orfao_marcar_avisado] erro: {e}")


def orfaos_listar_guild(guild_id: str | int, apenas_pendentes: bool = True) -> list[dict]:
    gid = str(guild_id)
    try:
        q = {"guild_id": gid}
        if apenas_pendentes:
            q["resolvido"] = False
        return list(_col_orfaos().find(q).sort("recebido_em", -1).limit(50))
    except Exception as e:
        _log.warning(f"[orfaos_listar_guild] erro: {e}")
        return []
