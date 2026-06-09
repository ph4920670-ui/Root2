"""
Carteira (Wallet) — cliente Supabase SÍNCRONO (requests).

Usado pelo painel-cliente (Flask) pra ler saldo e extrato. As escritas
de verdade (creditar/debitar) acontecem no bot, mas as funções estão aqui
também pra reuso.

Credenciais via env:
  SUPABASE_URL          (ex: https://xxxx.supabase.co)
  SUPABASE_SERVICE_KEY  (service_role — só no backend, nunca no front)

Se as envs não existirem, entra em MODO VAZIO: saldo 0, extrato vazio.
Assim o painel não quebra antes de você configurar o Supabase.
"""

import os
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

SUPABASE_URL = (os.getenv("SUPABASE_URL", "") or "").strip().rstrip("/")
SUPABASE_KEY = (os.getenv("SUPABASE_SERVICE_KEY", "") or "").strip()

_TIMEOUT = 10


def disponivel() -> bool:
    """True se as credenciais do Supabase estão configuradas."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def get_saldo(client_id: str) -> float:
    """Saldo atual da carteira do cliente. 0 se não existe ou Supabase off."""
    if not disponivel() or not client_id:
        return 0.0
    url = f"{SUPABASE_URL}/rest/v1/wallets"
    params = {"client_id": f"eq.{client_id}", "select": "balance"}
    try:
        r = requests.get(url, headers=_headers(), params=params, timeout=_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if data:
                return float(data[0].get("balance") or 0)
        return 0.0
    except Exception as e:
        print(f"[WALLET] erro get_saldo: {e}", flush=True)
        return 0.0


def get_extrato(client_id: str, limite: int = 20) -> list:
    """Últimas movimentações da carteira (mais recentes primeiro)."""
    if not disponivel() or not client_id:
        return []
    url = f"{SUPABASE_URL}/rest/v1/wallet_transactions"
    base = {
        "client_id": f"eq.{client_id}",
        "order": "created_at.desc",
        "limit": str(int(limite)),
    }
    # Tenta um select rico — inclui o NOME do recebedor/pagador do PIX pra que o
    # comprovante do site (gerado a partir do extrato) mostre "o nome do cara"
    # do pagamento .pix. Se o schema não tiver alguma coluna, o PostgREST
    # devolve 400 e a gente cai no próximo select, até o seguro.
    selects = [
        "id,tipo,valor,status,txid,pix_key,descricao,nome,nome_recebedor,pagador,banco,created_at",
        "id,tipo,valor,status,txid,pix_key,descricao,nome,created_at",
        "id,tipo,valor,status,txid,pix_key,descricao,created_at",
    ]
    for sel in selects:
        try:
            params = dict(base, select=sel)
            r = requests.get(url, headers=_headers(), params=params, timeout=_TIMEOUT)
            if r.status_code == 200:
                return r.json() or []
        except Exception as e:
            print(f"[WALLET] erro get_extrato: {e}", flush=True)
    return []


def resumo(client_id: str) -> dict:
    """Saldo + totais (entradas/saídas concluídas) pra montar a tela."""
    saldo = get_saldo(client_id)
    extrato = get_extrato(client_id, limite=50)
    entradas = sum(float(t["valor"]) for t in extrato
                   if t.get("tipo") == "DEPOSITO" and t.get("status") == "COMPLETO")
    saidas = sum(float(t["valor"]) for t in extrato
                 if t.get("tipo") == "SAQUE" and t.get("status") == "COMPLETO")
    return {
        "saldo": saldo,
        "entradas": entradas,
        "saidas": saidas,
        "extrato": extrato[:20],
        "supabase_ok": disponivel(),
    }
