# utils/pix.py — Multi-banco: Efí, MisticPay 1.5%, MisticPay 0.35%

import os, json, logging

_log = logging.getLogger("salasff.pix")
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CERT     = os.path.join(_BASE_DIR, "cert.pem")

# ══════════════════════════════════════════════════════════════
#  CREDENCIAIS — Efí
# ══════════════════════════════════════════════════════════════

EFI_CLIENT_ID     = "Client_Id_3053c4d31d32b0e37e15bf27d96e86e856441e48"
EFI_CLIENT_SECRET = "Client_Secret_9327383ccf59a18473b895fe12869b3669531d69"
EFI_CHAVE_PIX     = "586c7c6f-c18e-439e-9024-311b791e05a2"
EFI_BASE_URL      = "https://pix.api.efipay.com.br"

# ══════════════════════════════════════════════════════════════
#  CREDENCIAIS — MisticPay 1.5% (novo)
# ══════════════════════════════════════════════════════════════

MISTIC15_CI       = "ci_cunu46fvegaicwj"
MISTIC15_CS       = "cs_iq6dywizqwbqksbrs75e6m7sg"

# ══════════════════════════════════════════════════════════════
#  CREDENCIAIS — MisticPay 0.35% (antigo)
# ══════════════════════════════════════════════════════════════

MISTIC35_CI       = "ci_bypri3jaiulonrc"
MISTIC35_CS       = "cs_xp1dacpiltkeofjif6u126lwq"

MISTIC_BASE_URL   = "https://api.misticpay.com/api"

# ══════════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════════

_PRECO_DEFAULT = 0.09
PRECO_POR_SALA = _PRECO_DEFAULT

def _cfg_path():
    return os.path.join(_BASE_DIR, "botconfig.json")

def _load_cfg():
    try:
        with open(_cfg_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _cfg() -> dict:
    """Lê config do Supabase (cache em memória) — única fonte da verdade."""
    try:
        from utils.database import botconfig_load
        return botconfig_load()
    except Exception:
        return _load_cfg()  # fallback: arquivo local (só na inicialização antes do DB)

def get_preco_por_sala() -> float:
    return float(_cfg().get("preco_por_sala", _PRECO_DEFAULT))

def get_preco_por_sala_guild(guild_id: str = None) -> float:
    if guild_id:
        from utils.database import guild_config_get
        cfg = guild_config_get(guild_id)
        preco_srv = cfg.get("preco_sala")
        if preco_srv is not None:
            return float(preco_srv)
    return get_preco_por_sala()

def get_banco_ativo(quantidade: int = None) -> str:
    """Retorna o banco ativo.
    Bancos: 'efi', 'mistic_15', 'mistic_35', 'dividido'
    Se 'dividido', usa mistic_15 abaixo do limite e mistic_35 acima.
    """
    data = _cfg()
    banco = data.get("banco_pix", "efi")
    if banco == "dividido" and quantidade is not None:
        limite = int(data.get("banco_dividido_limite", 50))
        return "mistic_15" if quantidade < limite else "mistic_35"
    if banco == "dividido":
        return "mistic_15"  # fallback sem quantidade
    return banco

def set_banco_ativo(banco: str):
    try:
        from utils.database import botconfig_load, botconfig_save
        data = botconfig_load()
        data["banco_pix"] = banco
        botconfig_save(data)
    except Exception:
        cfg = _load_cfg()
        cfg["banco_pix"] = banco
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

def get_banco_dividido_limite() -> int:
    return int(_cfg().get("banco_dividido_limite", 50))

def set_banco_dividido_limite(limite: int):
    cfg = _load_cfg()
    cfg["banco_dividido_limite"] = limite
    with open(_cfg_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def set_preco_global(valor: float):
    cfg = _load_cfg()
    cfg["preco_por_sala"] = round(valor, 4)
    with open(_cfg_path(), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════════
#  EFÍ — Implementação
# ══════════════════════════════════════════════════════════════

def _efi_get_token():
    import requests, base64
    cred = base64.b64encode(f"{EFI_CLIENT_ID}:{EFI_CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        f"{EFI_BASE_URL}/oauth/token",
        headers={"Authorization": f"Basic {cred}", "Content-Type": "application/json"},
        json={"grant_type": "client_credentials"},
        cert=_CERT, timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def _efi_criar_cobranca(valor_total: float, descricao: str, expiracao: int = 3600) -> dict:
    import requests
    token = _efi_get_token()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "calendario": {"expiracao": expiracao},
        "valor": {"original": f"{valor_total:.2f}"},
        "chave": EFI_CHAVE_PIX,
        "solicitacaoPagador": descricao[:140],
    }
    r = requests.post(f"{EFI_BASE_URL}/v2/cob", headers=h, json=body, cert=_CERT, timeout=15)
    r.raise_for_status()
    data   = r.json()
    txid   = data["txid"]
    loc_id = data["loc"]["id"]
    qr = requests.get(f"{EFI_BASE_URL}/v2/loc/{loc_id}/qrcode", headers=h, cert=_CERT, timeout=15)
    qr.raise_for_status()
    qr_data = qr.json()
    return {
        "txid":       txid,
        "qrcode":     qr_data.get("imagemQrcode", ""),
        "copia_cola": qr_data.get("qrcode", ""),
        "valor":      valor_total,
    }

def _efi_consultar(txid: str) -> dict:
    import requests
    token = _efi_get_token()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.get(f"{EFI_BASE_URL}/v2/cob/{txid}", headers=h, cert=_CERT, timeout=15)
    r.raise_for_status()
    return r.json()


# ══════════════════════════════════════════════════════════════
#  MISTICPAY — Implementação genérica (aceita CI/CS)
# ══════════════════════════════════════════════════════════════

def _mistic_headers(ci: str, cs: str) -> dict:
    return {"ci": ci, "cs": cs, "Content-Type": "application/json"}

MISTIC_VALOR_MINIMO = 1.50

def _mistic_criar_cobranca(valor_total: float, descricao: str, ci: str, cs: str, expiracao: int = 3600) -> dict:
    import requests, uuid
    if valor_total < MISTIC_VALOR_MINIMO:
        raise Exception(f"Valor mínimo para compra via PIX: R$ {MISTIC_VALOR_MINIMO:.2f}")
    h = _mistic_headers(ci, cs)
    tx_id = str(uuid.uuid4()).replace("-", "")[:20]
    body = {
        "amount": round(valor_total, 2),
        "payerName": "SalasFF",
        "payerDocument": "52998224725",
        "transactionId": tx_id,
        "description": descricao[:140],
    }
    r = requests.post(f"{MISTIC_BASE_URL}/transactions/create", headers=h, json=body, timeout=15)
    if not r.ok:
        _log.error(f"[MISTIC CREATE] {r.status_code} — {r.text[:500]}")
        try:
            err = r.json()
            msg = err.get("message") or err.get("error") or err.get("msg") or r.text[:300]
        except Exception:
            msg = r.text[:300]
        raise Exception(f"MisticPay {r.status_code}: {msg}")
    data = r.json().get("data", {})
    return {
        "txid":       str(data.get("transactionId", tx_id)),
        "qrcode":     data.get("qrCodeBase64", ""),
        "copia_cola": data.get("copyPaste", ""),
        "valor":      valor_total,
    }

def _mistic_consultar(txid: str, ci: str, cs: str) -> dict:
    import requests
    h = _mistic_headers(ci, cs)
    body = {"transactionId": str(txid)}
    r = requests.post(f"{MISTIC_BASE_URL}/transactions/check", headers=h, json=body, timeout=15)
    r.raise_for_status()
    raw = r.json()
    _log.debug(f"[MISTIC CHECK] raw response: {raw}")

    # API pode retornar em vários formatos: .transaction, .data, ou raiz
    data = (
        raw.get("transaction")
        or raw.get("data")
        or raw.get("transacao")
        or raw
    )
    if not isinstance(data, dict):
        data = raw

    status_map = {
        "COMPLETO":  "CONCLUIDA",
        "COMPLETED": "CONCLUIDA",
        "PAID":      "CONCLUIDA",
        "PAGO":      "CONCLUIDA",
        "PENDENTE":  "ATIVA",
        "PENDING":   "ATIVA",
        "FALHA":     "REMOVIDA_PELO_USUARIO_RECEBEDOR",
        "FAILED":    "REMOVIDA_PELO_USUARIO_RECEBEDOR",
    }

    # Tenta vários campos possíveis para o estado
    raw_status = (
        data.get("transactionState")
        or data.get("status")
        or data.get("state")
        or data.get("situacao")
        or "PENDENTE"
    )
    data["status"] = status_map.get(str(raw_status).upper(), raw_status)
    _log.info(f"[MISTIC CHECK] txid={txid} raw_status={raw_status} → {data['status']}")
    return data


def _mistic_buscar_detalhes(client_tx_id: str, ci: str, cs: str, max_paginas: int = 3) -> dict:
    """
    Busca os detalhes completos da transação na rota de listagem.

    A rota /transactions/check NÃO retorna nome do pagador nem o endToEndId.
    Já a rota /users/transactions/list retorna clientName, endToEndId,
    clientTransactionId etc — então buscamos a transação por clientTransactionId
    nas primeiras páginas (mais recentes).

    Retorna dict com {clientName, endToEndId, transactionId_real, ...} ou {} se
    não encontrar.
    """
    import requests
    h = _mistic_headers(ci, cs)
    target = str(client_tx_id)
    for page in range(1, max_paginas + 1):
        try:
            r = requests.get(
                f"{MISTIC_BASE_URL}/users/transactions/list/{page}?status=COMPLETO",
                headers=h, timeout=10,
            )
            if r.status_code != 200:
                break
            payload = r.json() or {}
            items = payload.get("data") or []
            for tx in items:
                ctid = str(tx.get("clientTransactionId") or "")
                if ctid == target:
                    return {
                        "clientName":     tx.get("clientName") or "",
                        "clientDocument": tx.get("clientDocument") or "",
                        "endToEndId":     tx.get("endToEndId") or "",
                        "transactionId":  str(tx.get("id") or ""),
                        "value":          tx.get("value"),
                        "createdAt":      tx.get("createdAt"),
                    }
            if not items:
                break
        except Exception as ex:
            _log.warning(f"[MISTIC LIST] erro pág {page}: {ex}")
            break
    return {}


def consultar_detalhes_pagador(txid: str, banco: str = None) -> dict:
    """
    Wrapper público: dado o txid (clientTransactionId), retorna dict com
    o nome do pagador e o endToEndId real do PIX.
    Apenas funciona pra MisticPay; pra EFI o nome já vem na consulta normal.
    """
    if banco is None:
        banco = get_banco_ativo()
    if banco == "efi":
        return {}  # EFI já entrega nome em consultar_cobranca
    ci, cs = _ci_cs_para_banco(banco)
    return _mistic_buscar_detalhes(txid, ci, cs)


async def consultar_detalhes_pagador_async(txid: str, banco: str = None) -> dict:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, consultar_detalhes_pagador, txid, banco)

def _ci_cs_para_banco(banco: str):
    """Retorna (ci, cs) para o banco informado."""
    if banco == "mistic_35":
        return MISTIC35_CI, MISTIC35_CS
    return MISTIC15_CI, MISTIC15_CS  # mistic_15 é o padrão mistic


# ══════════════════════════════════════════════════════════════
#  INTERFACE PÚBLICA
# ══════════════════════════════════════════════════════════════

def criar_cobranca_pix(valor_total: float, descricao: str, expiracao: int = 3600, quantidade: int = None) -> dict:
    banco = get_banco_ativo(quantidade)
    _log.info(f"[PIX] Criando cobrança via {banco}: R${valor_total:.2f}")
    if banco == "efi":
        return _efi_criar_cobranca(valor_total, descricao, expiracao)
    ci, cs = _ci_cs_para_banco(banco)
    return _mistic_criar_cobranca(valor_total, descricao, ci, cs, expiracao)

def consultar_cobranca(txid: str, banco: str = None) -> dict:
    if banco is None:
        banco = get_banco_ativo()
    if banco == "efi":
        return _efi_consultar(txid)
    ci, cs = _ci_cs_para_banco(banco)
    return _mistic_consultar(txid, ci, cs)

async def consultar_cobranca_async(txid: str, banco: str = None) -> dict:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, consultar_cobranca, txid, banco)
