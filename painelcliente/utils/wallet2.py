"""
Wallet2 — helper SÍNCRONO pro painel Flask.

Cuida do DEPÓSITO: gera cobrança PIX na Wallet2 e credita o saldo quando
pago. O saque (+p) acontece no bot (selfbot), não aqui.

Saldo por client_id, no Supabase (tabelas wallet2 / wallet2_transactions e RPCs
wallet2_credit / wallet2_debit — o mesmo schema usado pelo bot).
"""
import os
import time
import hmac
import json
import hashlib
import requests

TURBOFY_URL = (os.getenv("TURBOFY_URL", "https://api.turbofypay.com") or "").strip().rstrip("/")
TURBOFY_CLIENT_ID = (os.getenv("TURBOFY_CLIENT_ID", "") or "").strip()
TURBOFY_SECRET = (os.getenv("TURBOFY_SECRET", "") or "").strip()

SUPABASE_URL = (
    os.getenv("WALLET2_SUPABASE_URL", "")
    or os.getenv("SUPABASE_URL", "") or ""
).strip().rstrip("/")
SUPABASE_KEY = (
    os.getenv("WALLET2_SUPABASE_KEY", "")
    or os.getenv("SUPABASE_SERVICE_KEY", "") or ""
).strip()

_TIMEOUT = 20

# Taxa cobrada sobre DEPÓSITOS: o cliente paga o PIX cheio e recebe (1 - taxa)
# no saldo. Ex.: taxa 0.05 (5%) → depósito de R$ 10 credita R$ 9,50.
# NÃO se aplica a estornos de saque (esses devolvem o valor cheio).
TAXA_DEPOSITO = 0.05


def turbofy_ok() -> bool:
    return bool(TURBOFY_CLIENT_ID and TURBOFY_SECRET)


def supabase_ok() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


# ───────────────────── saldo ─────────────────────
def saldo(client_id: str) -> float:
    if not supabase_ok() or not client_id:
        return 0.0
    url = f"{SUPABASE_URL}/rest/v1/wallet2"
    params = {"client_id": f"eq.{client_id}", "select": "balance"}
    try:
        r = requests.get(url, headers=_sb_headers(), params=params, timeout=_TIMEOUT)
        if r.status_code == 200:
            rows = r.json()
            if rows:
                return float(rows[0].get("balance") or 0)
    except Exception:
        pass
    return 0.0


def creditar(client_id: str, valor: float) -> dict:
    if not supabase_ok():
        return {"ok": False, "erro": "supabase off"}
    url = f"{SUPABASE_URL}/rest/v1/rpc/wallet2_credit"
    try:
        r = requests.post(url, headers=_sb_headers(),
                          json={"p_client_id": client_id, "p_amount": float(valor)},
                          timeout=_TIMEOUT)
        if r.status_code in (200, 201):
            return {"ok": True, "saldo": float(r.json())}
        return {"ok": False, "erro": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


# ───────────────────── transações (livro-caixa) ─────────────────────
def registrar_tx(client_id: str, tipo: str, valor: float, *, status="PENDENTE",
                 txid=None, chave=None) -> dict:
    if not supabase_ok():
        return {"ok": False}
    url = f"{SUPABASE_URL}/rest/v1/wallet2_transactions"
    body = {"client_id": client_id, "tipo": tipo, "valor": float(valor),
            "status": status, "txid": txid, "chave": chave}
    try:
        r = requests.post(url, headers={**_sb_headers(), "Prefer": "return=representation"},
                         json=body, timeout=_TIMEOUT)
        if r.status_code in (200, 201):
            rows = r.json()
            return {"ok": True, "row": rows[0] if rows else None}
    except Exception:
        pass
    return {"ok": False}


def tx_por_txid(txid: str) -> dict:
    """Busca a transação pelo txid (pra saber se já foi creditada)."""
    if not supabase_ok() or not txid:
        return None
    url = f"{SUPABASE_URL}/rest/v1/wallet2_transactions"
    params = {"txid": f"eq.{txid}", "select": "*", "limit": "1"}
    try:
        r = requests.get(url, headers=_sb_headers(), params=params, timeout=_TIMEOUT)
        if r.status_code == 200:
            rows = r.json()
            return rows[0] if rows else None
    except Exception:
        pass
    return None


def marcar_tx_paga(row_id) -> bool:
    if not supabase_ok():
        return False
    url = f"{SUPABASE_URL}/rest/v1/wallet2_transactions"
    params = {"id": f"eq.{row_id}"}
    try:
        r = requests.patch(url, headers=_sb_headers(), params=params,
                          json={"status": "PAGO"}, timeout=_TIMEOUT)
        return r.status_code in (200, 204)
    except Exception:
        return False


# ───────────────────── Wallet2: cobrança (depósito) ─────────────────────
def criar_cobranca(client_id: str, valor: float) -> dict:
    """Cria cobrança PIX e registra a transação PENDENTE. Retorna
    {ok, txid, copia_cola, qr, erro}."""
    if not turbofy_ok():
        return {"ok": False, "erro": "Wallet2 não configurada"}
    amount_cents = int(round(float(valor) * 100))
    ext = f"dep-{client_id}-{int(time.time()*1000)}"
    headers = {
        "x-client-id": TURBOFY_CLIENT_ID,
        "x-client-secret": TURBOFY_SECRET,
        "x-idempotency-key": ext,
        "Content-Type": "application/json",
    }
    body = {"amountCents": amount_cents, "description": f"Depósito wallet2 {client_id}",
            "externalRef": ext}
    try:
        r = requests.post(f"{TURBOFY_URL}/sellers/pix", headers=headers,
                         json=body, timeout=_TIMEOUT)
        data = r.json() if r.content else {}
        if r.status_code in (200, 201):
            pix = data.get("pix") or {}
            txid = data.get("id")
            registrar_tx(client_id, "deposito", valor, status="PENDENTE", txid=txid)
            return {"ok": True, "txid": txid,
                    "copia_cola": pix.get("copyPaste"), "qr": pix.get("qrCode")}
        err = (data.get("error") or {})
        return {"ok": False, "erro": err.get("message") or f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "erro": f"rede: {e}"}


def debitar(client_id: str, valor: float) -> dict:
    """Debita o saldo wallet2 de forma atômica (RPC wallet2_debit). O RPC deve
    falhar se o saldo for insuficiente. Retorna {ok, saldo} ou {ok:False, erro}."""
    if not supabase_ok():
        return {"ok": False, "erro": "supabase off"}
    url = f"{SUPABASE_URL}/rest/v1/rpc/wallet2_debit"
    try:
        r = requests.post(url, headers=_sb_headers(),
                          json={"p_client_id": client_id, "p_amount": float(valor)},
                          timeout=_TIMEOUT)
        if r.status_code in (200, 201):
            return {"ok": True, "saldo": float(r.json())}
        # 400/erro do RPC normalmente = saldo insuficiente (raise no Postgres).
        return {"ok": False, "erro": f"saldo insuficiente (HTTP {r.status_code})"}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


# ───────────────────── pixKeyType: normaliza pro padrão Wallet2 ─────────────
_PIXTIPO_MAP = {
    "CPF": "CPF", "CNPJ": "CNPJ", "EMAIL": "EMAIL", "E-MAIL": "EMAIL",
    "TELEFONE": "PHONE", "PHONE": "PHONE", "CELULAR": "PHONE",
    "ALEATORIA": "EVP", "CHAVE_ALEATORIA": "EVP", "EVP": "EVP", "RANDOM": "EVP",
}


def _norm_pixtipo(t: str) -> str:
    return _PIXTIPO_MAP.get(str(t or "").strip().upper(), "EVP")


def _turbofy_signature(method: str, path: str, body_str: str, ts_ms: str) -> str:
    """HMAC SHA-256 (hex) de '{timestamp}.{method}.{path}.{body}', chave =
    x-client-secret. Body vazio em GET. Tem que assinar EXATAMENTE o corpo
    enviado — por isso o corpo é serializado uma vez e reusado."""
    canonical = f"{ts_ms}.{method.upper()}.{path}.{body_str}"
    return hmac.new(TURBOFY_SECRET.encode("utf-8"),
                    canonical.encode("utf-8"), hashlib.sha256).hexdigest()


# ───────────────────── Wallet2: saque (payout) ─────────────────────
def solicitar_saque(client_id: str, valor: float, pix_key: str, pix_key_type: str,
                    recipient_name: str = "", recipient_doc: str = "") -> dict:
    """Debita o saldo wallet2 e envia um payout PIX na Wallet2
    (POST /v1/payouts/batches, com assinatura HMAC). Se o payout falhar, ESTORNA
    o saldo. Retorna {ok, batch_id, status, saldo, erro}."""
    if not turbofy_ok():
        return {"ok": False, "erro": "Wallet2 não configurada"}
    try:
        valor = round(float(valor), 2)
    except (TypeError, ValueError):
        return {"ok": False, "erro": "valor inválido"}
    if valor <= 0:
        return {"ok": False, "erro": "valor tem que ser maior que zero"}
    pix_key = str(pix_key or "").strip()
    if not pix_key:
        return {"ok": False, "erro": "informe a chave PIX"}

    # 1) Debita primeiro (atômico). Se não tem saldo, nem chama a API.
    deb = debitar(client_id, valor)
    if not deb.get("ok"):
        return {"ok": False, "erro": deb.get("erro", "não foi possível debitar")}

    # 2) Monta o payout e assina.
    amount_cents = int(round(valor * 100))
    ext = f"saq-{client_id}-{int(time.time()*1000)}"
    item = {
        "pixKey": pix_key,
        "pixKeyType": _norm_pixtipo(pix_key_type),
        "amountCents": amount_cents,
        "recipientName": (recipient_name or "Cliente")[:120],
    }
    rdoc = "".join(ch for ch in str(recipient_doc or "") if ch.isdigit())
    if rdoc:
        item["recipientDocument"] = rdoc
    body = {"idempotencyKey": ext, "description": f"Saque wallet2 {client_id}"[:255],
            "items": [item]}
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    ts = str(int(time.time() * 1000))
    path = "/v1/payouts/batches"
    headers = {
        "x-client-id": TURBOFY_CLIENT_ID,
        "x-client-secret": TURBOFY_SECRET,
        "x-turbofy-timestamp": ts,
        "x-turbofy-signature": _turbofy_signature("POST", path, body_str, ts),
        "x-idempotency-key": ext,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(f"{TURBOFY_URL}{path}", headers=headers,
                         data=body_str.encode("utf-8"), timeout=_TIMEOUT)
        data = r.json() if r.content else {}
        if r.status_code in (200, 201):
            batch_id = data.get("batchId")
            registrar_tx(client_id, "saque", valor, status="PROCESSANDO",
                        txid=batch_id, chave=pix_key)
            return {"ok": True, "batch_id": batch_id,
                    "status": data.get("status", "PROCESSANDO"),
                    "saldo": deb.get("saldo")}
        # Falhou na API — ESTORNA o saldo que foi debitado.
        creditar(client_id, valor)
        err = (data.get("error") or {})
        return {"ok": False, "estornado": True,
                "erro": err.get("message") or f"HTTP {r.status_code}"}
    except Exception as e:
        # Erro de rede — estorna pra não sumir com o saldo do cliente.
        creditar(client_id, valor)
        return {"ok": False, "estornado": True, "erro": f"rede: {e}"}


def checar_e_creditar(client_id: str, txid: str) -> dict:
    """Consulta a cobrança; se PAGA e ainda não creditada, credita o saldo.
    Retorna {ok, pago, saldo, erro}."""
    if not turbofy_ok() or not txid:
        return {"ok": False, "erro": "sem txid/credencial"}
    headers = {"x-client-id": TURBOFY_CLIENT_ID, "x-client-secret": TURBOFY_SECRET}
    try:
        r = requests.get(f"{TURBOFY_URL}/sellers/pix/{txid}", headers=headers, timeout=_TIMEOUT)
        data = r.json() if r.content else {}
        st = (data.get("status") or "").upper()
        if st != "PAID":
            return {"ok": True, "pago": False, "status": st}
        # PAGO — credita se ainda não creditou (evita crédito duplo)
        tx = tx_por_txid(txid)
        if tx and tx.get("status") == "PAGO":
            return {"ok": True, "pago": True, "saldo": saldo(client_id), "ja_creditado": True}
        valor = float(tx.get("valor")) if tx else float(data.get("amountCents", 0)) / 100
        # Desconta a taxa de depósito: cliente pagou `valor`, recebe (1 - taxa).
        valor_creditado = round(valor * (1.0 - TAXA_DEPOSITO), 2)
        cred = creditar(client_id, valor_creditado)
        if tx:
            marcar_tx_paga(tx.get("id"))
        return {"ok": True, "pago": True, "saldo": cred.get("saldo", saldo(client_id))}
    except Exception as e:
        return {"ok": False, "erro": f"rede: {e}"}
