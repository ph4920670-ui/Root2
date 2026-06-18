# utils/pix_credentials.py — Storage criptografado de credenciais PIX por guild
#
# Cada guild (servidor Discord) tem suas próprias credenciais bancárias.
# As credenciais são criptografadas usando Fernet (AES-128) e salvas no MongoDB.
# A chave de criptografia vem de PIX_CREDS_KEY (env var) ou é gerada/salva
# em data/.pix_creds_key (com permissão 600).
#
# Coleção MongoDB: pix_creds
# {
#   "_id": "1234567890",                  # guild_id
#   "banco_ativo": "mercadopago",
#   "creds": {
#     "efi":         "<encrypted blob>",  # {"client_id":"...","client_secret":"..."}
#     "mercadopago": "<encrypted blob>",  # {"access_token":"..."}
#     "pagbank":     "<encrypted blob>",  # {"token":"..."}
#     "macrodroid":  "<encrypted blob>"   # {"site_url":"..."}
#   },
#   "atualizado_em": "ISO 8601"
# }
#
# cert.pem do EFI: salvo em data/efi_certs/<guild_id>.pem (local, pode perder no restart)
# — aceitavel pois o upload e facil de refazer via /configurar_pix_efi_cert.

import os
import json
import logging
from datetime import datetime
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

_log = logging.getLogger("salasff.pix_creds")

_BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR  = os.path.join(_BASE_DIR, "data")
_KEY_PATH  = os.path.join(_DATA_DIR, ".pix_creds_key")
_CERTS_DIR = os.path.join(_DATA_DIR, "efi_certs")

os.makedirs(_DATA_DIR,  exist_ok=True)
os.makedirs(_CERTS_DIR, exist_ok=True)

# Bancos suportados
BANCOS = ("efi", "mercadopago", "pagbank", "macrodroid", "gmail")

# Bancos que aparecem no painel /configurar_pix.
# Os outros ficam ocultos (código permanece, só não exibimos no UI ainda).
# Pra reativar: adicione na tupla.
BANCOS_VISIVEIS = ("gmail",)

# Labels amigaveis
BANCOS_LABEL = {
    "efi":         "EFI Pay",
    "mercadopago": "Mercado Pago",
    "pagbank":     "PagBank",
    "macrodroid":  "MacroDroid (Notificacoes)",
    "gmail":       "Gmail",
}

# Campos esperados por banco (para validacao e modal)
BANCOS_CAMPOS = {
    "efi":         ["client_id", "client_secret"],  # cert.pem e upload separado
    "mercadopago": ["access_token"],
    "pagbank":     ["token"],
    "macrodroid":  ["site_url"],
    "gmail":       ["client_id", "client_secret", "refresh_token"],
}


# ── Chave de criptografia ─────────────────────────────────────

def _get_fernet() -> Fernet:
    """Carrega ou gera a chave Fernet. Prioridade: env > arquivo."""
    env_key = os.environ.get("PIX_CREDS_KEY")
    if env_key:
        try:
            return Fernet(env_key.encode())
        except Exception:
            _log.warning("[pix_creds] PIX_CREDS_KEY invalida, usando arquivo")
    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            return Fernet(f.read())
    # Gera nova
    key = Fernet.generate_key()
    with open(_KEY_PATH, "wb") as f:
        f.write(key)
    try:
        os.chmod(_KEY_PATH, 0o600)
    except Exception:
        pass
    _log.info(f"[pix_creds] Nova chave gerada em {_KEY_PATH}")
    return Fernet(key)


_fernet: Optional[Fernet] = None

def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = _get_fernet()
    return _fernet


# ── MongoDB ───────────────────────────────────────────────────

def _col():
    from utils.database import _get_db
    return _get_db()["pix_creds"]


# ── Cache em memoria (evita bater no Mongo a cada 'pg') ───────

_cache: dict[str, dict] = {}   # guild_id (str) -> doc


# ── Encrypt/Decrypt ───────────────────────────────────────────

def _encrypt(data: dict) -> str:
    raw = json.dumps(data, ensure_ascii=False).encode()
    return _f().encrypt(raw).decode()


def _decrypt(blob: str) -> dict:
    try:
        raw = _f().decrypt(blob.encode())
        return json.loads(raw.decode())
    except (InvalidToken, Exception) as e:
        _log.error(f"[pix_creds] erro ao descriptografar: {e}")
        return {}


# ── CRUD ──────────────────────────────────────────────────────

def _load_guild(gid: str) -> dict:
    """Carrega doc da guild (cache primeiro, depois Mongo)."""
    if gid in _cache:
        return _cache[gid]
    try:
        doc = _col().find_one({"_id": gid}) or {}
    except Exception as e:
        _log.error(f"[pix_creds] erro ao carregar guild {gid}: {e}")
        doc = {}
    if doc:
        _cache[gid] = doc
    return doc


def _save_guild(gid: str, doc: dict):
    """Salva doc no Mongo e atualiza cache."""
    doc["_id"] = gid
    doc["atualizado_em"] = datetime.utcnow().isoformat() + "Z"
    _cache[gid] = doc
    try:
        d = dict(doc)
        d.pop("_id", None)
        _col().update_one({"_id": gid}, {"$set": d}, upsert=True)
    except Exception as e:
        _log.error(f"[pix_creds] erro ao salvar guild {gid}: {e}")


# ── Interface publica ─────────────────────────────────────────

def set_creds_guild(guild_id: int | str, banco: str, creds: dict) -> None:
    """Salva credenciais de um banco para uma guild (criptografado no Mongo)."""
    if banco not in BANCOS:
        raise ValueError(f"Banco invalido: {banco}")
    gid = str(guild_id)
    doc = _load_guild(gid)
    if not doc:
        doc = {"banco_ativo": banco, "creds": {}}
    doc.setdefault("creds", {})[banco] = _encrypt(creds)
    if not doc.get("banco_ativo"):
        doc["banco_ativo"] = banco
    _save_guild(gid, doc)


def get_creds_guild(guild_id: int | str, banco: str | None = None) -> dict:
    """Recupera credenciais descriptografadas. Se banco=None, usa o banco_ativo."""
    gid = str(guild_id)
    doc = _load_guild(gid)
    if not doc:
        return {}
    if banco is None:
        banco = doc.get("banco_ativo")
    if not banco:
        return {}
    blob = doc.get("creds", {}).get(banco)
    if not blob:
        return {}
    return _decrypt(blob)


def get_banco_ativo(guild_id: int | str) -> str | None:
    """Retorna qual banco a guild escolheu como ativo."""
    doc = _load_guild(str(guild_id))
    return doc.get("banco_ativo") if doc else None


def get_creds_fallback() -> tuple[str | None, dict]:
    """Retorna (banco, creds) de qualquer guild que tenha banco configurado.
    Usado quando a guild atual não tem banco configurado (servidor paralelo).
    """
    try:
        docs = list(_col().find({"banco_ativo": {"$exists": True, "$ne": None}}, limit=5))
        for doc in docs:
            banco = doc.get("banco_ativo")
            if not banco:
                continue
            blob = doc.get("creds", {}).get(banco)
            if not blob:
                continue
            creds = _decrypt(blob)
            if creds:
                return banco, creds
    except Exception as e:
        _log.warning(f"[pix_creds] get_creds_fallback: {e}")
    return None, {}


def set_banco_ativo(guild_id: int | str, banco: str) -> None:
    """Define qual banco a guild vai usar."""
    if banco not in BANCOS:
        raise ValueError(f"Banco invalido: {banco}")
    gid = str(guild_id)
    doc = _load_guild(gid)
    if not doc:
        doc = {"banco_ativo": banco, "creds": {}}
    else:
        doc["banco_ativo"] = banco
    _save_guild(gid, doc)


def remover_creds(guild_id: int | str, banco: str) -> bool:
    """Remove credenciais de um banco da guild."""
    gid = str(guild_id)
    doc = _load_guild(gid)
    if not doc or banco not in doc.get("creds", {}):
        return False
    del doc["creds"][banco]
    if doc.get("banco_ativo") == banco:
        doc["banco_ativo"] = None
    _save_guild(gid, doc)
    _cache.pop(gid, None)   # forca reload no proximo acesso
    return True


def listar_bancos_configurados(guild_id: int | str) -> list[str]:
    """Retorna lista dos bancos que a guild ja configurou."""
    doc = _load_guild(str(guild_id))
    if not doc:
        return []
    return list(doc.get("creds", {}).keys())


# ── Cert.pem do EFI ───────────────────────────────────────────
# O cert fica em disco local (data/efi_certs/<guild_id>.pem).
# Se o bot reiniciar, o admin precisa fazer upload de novo via /configurar_pix_efi_cert.

def cert_path_efi(guild_id: int | str) -> str:
    return os.path.join(_CERTS_DIR, f"{guild_id}.pem")


def has_cert_efi(guild_id: int | str) -> bool:
    return os.path.exists(cert_path_efi(guild_id))


def salvar_cert_efi(guild_id: int | str, conteudo: bytes) -> str:
    path = cert_path_efi(guild_id)
    with open(path, "wb") as f:
        f.write(conteudo)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


def remover_cert_efi(guild_id: int | str) -> bool:
    path = cert_path_efi(guild_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


# ── Resumo para exibir no painel (sem expor segredos) ─────────

def resumo_guild(guild_id: int | str) -> dict:
    """Retorna info para exibir no painel — SEM expor as credenciais."""
    gid = str(guild_id)
    doc = _load_guild(gid)
    if not doc:
        return {
            "banco_ativo":  None,
            "configurados": [],
            "tem_cert_efi": has_cert_efi(gid),
        }
    return {
        "banco_ativo":   doc.get("banco_ativo"),
        "configurados":  list(doc.get("creds", {}).keys()),
        "tem_cert_efi":  has_cert_efi(gid),
        "atualizado_em": doc.get("atualizado_em"),
    }
