"""
Integração com a API MisticPay (v1).

Autenticação: headers `ci` (Client ID) + `cs` (Client Secret).
Docs: https://api.misticpay.com/api

As credenciais podem vir de duas fontes (nesta ordem de prioridade):
1. Parâmetros passados direto pra função (ci=..., cs=...).
2. Configuração salva no banco via /botconfig (get_credenciais_misticpay).
3. Variáveis de ambiente MISTICPAY_CI / MISTICPAY_CS (.env).

Se nenhuma credencial existir, as funções entram em MODO DE TESTE e
geram dados fake pra permitir testar a interface.
"""

import os
import uuid
import aiohttp
from typing import Optional

from utils import database


MISTICPAY_BASE_URL = "https://api.misticpay.com/api"

# Fallback via .env (opcional)
_ENV_CI = os.getenv("MISTICPAY_CI", "")
_ENV_CS = os.getenv("MISTICPAY_CS", "")


async def _resolver_credenciais(
    ci: Optional[str] = None, cs: Optional[str] = None
) -> tuple[str, str]:
    """Resolve as credenciais ci/cs: parâmetro > banco > .env."""
    if ci and cs:
        return ci, cs
    # Tenta o banco (config global salva pelo /botconfig)
    try:
        banco_ci, banco_cs = await database.get_credenciais_misticpay()
        if banco_ci and banco_cs:
            return banco_ci, banco_cs
    except Exception:
        pass
    return _ENV_CI, _ENV_CS


async def criar_pix(
    valor: float,
    descricao: str = "Compra Discord",
    *,
    payer_name: str = "Cliente Discord",
    payer_document: str = "00000000000",
    ci: Optional[str] = None,
    cs: Optional[str] = None,
) -> Optional[dict]:
    """
    Cria uma transação PIX (cash-in) no MisticPay.

    Retorna dict com: txid, qrcode (base64), copia_cola, valor.
    Retorna None em caso de erro.
    """
    ci, cs = await _resolver_credenciais(ci, cs)

    # ─── MODO DE TESTE — sem credenciais ──────────────────────────
    if not ci or not cs:
        txid = f"TEST{uuid.uuid4().hex[:16].upper()}"
        return {
            "txid": txid,
            "qrcode": None,
            "copia_cola": (
                "00020126360014BR.GOV.BCB.PIX0114+5511999999999"
                f"5204000053039865402{valor:.2f}5802BR5913TESTE PAGADOR"
                "6009SAO PAULO62070503***6304ABCD"
            ),
            "valor": valor,
            "teste": True,
        }

    # transactionId — id próprio pra identificar a transação
    transaction_id = f"discord-{uuid.uuid4().hex[:20]}"

    headers = {
        "ci": ci,
        "cs": cs,
        "Content-Type": "application/json",
    }
    payload = {
        "amount": round(valor, 2),
        "payerName": payer_name,
        "payerDocument": payer_document,
        "transactionId": transaction_id,
        "description": descricao,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MISTICPAY_BASE_URL}/transactions/create",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                texto = await resp.text()
                if resp.status not in (200, 201):
                    print(f"⚠️ MisticPay criar_pix erro {resp.status}: {texto}")
                    return None
                data = (await resp.json()).get("data", {})
                return {
                    # transactionId da MisticPay — usado pra consultar depois
                    "txid": str(data.get("transactionId") or transaction_id),
                    "qrcode": data.get("qrCodeBase64"),
                    "qrcode_url": data.get("qrcodeUrl"),
                    "copia_cola": data.get("copyPaste"),
                    "valor": valor,
                }
    except Exception as e:
        print(f"⚠️ Erro ao criar PIX MisticPay: {e}")
        return None


async def consultar_pix(
    txid: str, *, ci: Optional[str] = None, cs: Optional[str] = None
) -> Optional[str]:
    """
    Consulta o status de uma transação.

    Retorna: 'pendente', 'pago', 'falha' ou None se erro.
    (Normaliza os status da API: PENDENTE/COMPLETO/FALHA.)
    """
    ci, cs = await _resolver_credenciais(ci, cs)

    # MODO DE TESTE
    if not ci or not cs:
        return "pendente"

    headers = {
        "ci": ci,
        "cs": cs,
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MISTICPAY_BASE_URL}/transactions/check",
                headers=headers,
                json={"transactionId": txid},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                estado = (
                    data.get("transaction", {}).get("transactionState")
                    or data.get("data", {}).get("transactionState")
                    or "PENDENTE"
                ).upper()
                return {
                    "PENDENTE": "pendente",
                    "COMPLETO": "pago",
                    "FALHA": "falha",
                    "CANCELADO": "falha",
                }.get(estado, "pendente")
    except Exception as e:
        print(f"⚠️ Erro ao consultar PIX MisticPay: {e}")
        return None


async def testar_credenciais(ci: str, cs: str) -> tuple[bool, str]:
    """
    Testa um par ci/cs consultando /users/info.
    Retorna (ok, mensagem).
    """
    if not ci or not cs:
        return False, "Client ID e Client Secret são obrigatórios."

    headers = {"ci": ci, "cs": cs, "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{MISTICPAY_BASE_URL}/users/info",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = (await resp.json()).get("data", {})
                    nome = data.get("name", "conta")
                    saldo = data.get("availableBalance", 0)
                    return True, f"Conectado como **{nome}** (saldo: R$ {saldo})."
                if resp.status == 401:
                    return False, "Credenciais inválidas."
                if resp.status == 400:
                    return False, "Credenciais não enviadas corretamente."
                return False, f"Erro {resp.status} ao validar."
    except Exception as e:
        return False, f"Falha de conexão: {e}"
