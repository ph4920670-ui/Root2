# utils/pix_consulta.py — Consulta de PIX recebidos (não cobrança)
#
# Cada banco tem um adapter que recebe (creds, desde_iso) e retorna
# uma lista de PIX recebidos no formato comum:
#
#   [{
#     "id":     "<endToEndId ou similar>",   # único, evita reprocessar
#     "valor":  Decimal/float em reais,
#     "nome":   "Nome do Pagador" (pode ser vazio),
#     "horario": "ISO 8601 string",
#   }, ...]

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

_log = logging.getLogger("salasff.pix_consulta")

# ════════════════════════════════════════════════════════════════
#  EFI Pay
#  Auth: OAuth2 (Client Id + Secret) + cert.pem mTLS
#  Endpoint: GET /v2/pix?inicio=...&fim=...
# ════════════════════════════════════════════════════════════════

EFI_BASE = "https://pix.api.efipay.com.br"
EFI_BASE_SANDBOX = "https://pix-h.api.efipay.com.br"


def _efi_base_url(creds: dict) -> str:
    return EFI_BASE_SANDBOX if creds.get("ambiente") == "sandbox" else EFI_BASE


async def _efi_get_token(creds: dict, cert_path: str) -> str | None:
    """Busca access_token via OAuth2 + mTLS."""
    import base64
    cid = creds.get("client_id", "")
    csec = creds.get("client_secret", "")
    if not (cid and csec and cert_path):
        return None
    cred_b64 = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    headers = {
        "Authorization": f"Basic {cred_b64}",
        "Content-Type": "application/json",
    }
    url = f"{_efi_base_url(creds)}/oauth/token"
    body = {"grant_type": "client_credentials"}
    try:
        # aiohttp não suporta cert.pem mTLS facilmente; vamos usar requests sync
        # via to_thread pra não bloquear o loop.
        return await asyncio.to_thread(_efi_get_token_sync, url, headers, body, cert_path)
    except Exception as e:
        _log.warning(f"[efi] erro ao obter token: {e}")
        return None


def _efi_get_token_sync(url: str, headers: dict, body: dict, cert_path: str) -> str | None:
    import requests
    r = requests.post(url, headers=headers, json=body, cert=cert_path, timeout=15)
    r.raise_for_status()
    return r.json().get("access_token")


async def _efi_listar_pix_sync(token: str, base: str, cert_path: str,
                                inicio_iso: str, fim_iso: str) -> list[dict]:
    """Sync (chamado via to_thread) — usa cert.pem."""
    import requests
    url = f"{base}/v2/pix"
    h = {"Authorization": f"Bearer {token}"}
    params = {"inicio": inicio_iso, "fim": fim_iso}
    r = requests.get(url, headers=h, params=params, cert=cert_path, timeout=15)
    if r.status_code != 200:
        _log.warning(f"[efi] /v2/pix HTTP {r.status_code}: {r.text[:200]}")
        return []
    data = r.json()
    pix_list = data.get("pix") or []
    out = []
    for p in pix_list:
        out.append({
            "id":      p.get("endToEndId") or p.get("txid") or "",
            "valor":   float(str(p.get("valor", "0")).replace(",", ".")),
            "nome":    (p.get("pagador") or {}).get("nome", "") or p.get("infoPagador", ""),
            "horario": p.get("horario", ""),
        })
    return out


async def listar_efi(creds: dict, desde_iso: str | None = None) -> list[dict]:
    """Lista PIX recebidos via EFI no intervalo [desde, agora].

    creds = { client_id, client_secret, cert_pem, ambiente? }
    cert_pem é o conteúdo do arquivo (string PEM).
    """
    cert_pem = creds.get("cert_pem", "")
    if not cert_pem:
        _log.warning("[efi] cert_pem ausente nas credenciais")
        return []

    # Escreve cert num arquivo temporário (requests precisa de filepath)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(cert_pem)
        cert_path = f.name

    try:
        token = await _efi_get_token(creds, cert_path)
        if not token:
            return []

        base = _efi_base_url(creds)
        agora = datetime.now(timezone.utc)
        inicio = desde_iso or (agora - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        fim = agora.strftime("%Y-%m-%dT%H:%M:%SZ")

        return await asyncio.to_thread(_efi_listar_pix_sync, token, base, cert_path, inicio, fim)
    finally:
        try:
            os.unlink(cert_path)
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════
#  Mercado Pago
#  Auth: Bearer Access Token
#  Endpoint: GET /v1/payments/search
# ════════════════════════════════════════════════════════════════

MP_BASE = "https://api.mercadopago.com"


async def listar_mercadopago(creds: dict, desde_iso: str | None = None) -> list[dict]:
    """Lista pagamentos PIX aprovados no MP."""
    token = creds.get("access_token", "")
    if not token:
        return []

    agora = datetime.now(timezone.utc)
    inicio = desde_iso or (agora - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S.000-00:00")
    fim = agora.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

    url = f"{MP_BASE}/v1/payments/search"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "status": "approved",
        "payment_type_id": "pix",
        "begin_date": inicio,
        "end_date": fim,
        "range": "date_approved",
        "sort": "date_approved",
        "criteria": "desc",
        "limit": 50,
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(url, headers=headers, params=params) as r:
                if r.status != 200:
                    txt = await r.text()
                    _log.warning(f"[mp] HTTP {r.status}: {txt[:200]}")
                    return []
                data = await r.json()
        results = data.get("results") or []
        out = []
        for p in results:
            payer = p.get("payer") or {}
            ident = payer.get("identification") or {}
            nome = (
                payer.get("first_name", "") + " " + payer.get("last_name", "")
            ).strip() or ident.get("number", "") or ""
            out.append({
                "id":      str(p.get("id", "")),
                "valor":   float(p.get("transaction_amount", 0)),
                "nome":    nome,
                "horario": p.get("date_approved", "") or p.get("date_created", ""),
            })
        return out
    except asyncio.TimeoutError:
        _log.warning("[mp] timeout")
        return []
    except Exception as e:
        _log.warning(f"[mp] erro: {e}")
        return []


# ════════════════════════════════════════════════════════════════
#  PagBank — API EDI (Extrato Eletrônico)
#  Filtra por DATA ÚNICA (não intervalo) → buscamos hoje + se cruzar
#  meia-noite, hoje e ontem.
# ════════════════════════════════════════════════════════════════

PAGBANK_BASE = "https://api.pagseguro.com"


async def listar_pagbank(creds: dict, desde_iso: str | None = None) -> list[dict]:
    """Lista PIX recebidos via PagBank EDI."""
    token = creds.get("token", "")
    if not token:
        return []

    # Busca hoje (e se desde_iso for ontem, busca ontem também)
    hoje = datetime.now(timezone.utc).date()
    datas = [hoje.isoformat()]
    if desde_iso:
        try:
            d = datetime.fromisoformat(desde_iso.replace("Z", "+00:00")).date()
            if d < hoje:
                datas.append(d.isoformat())
        except Exception:
            pass

    out = []
    headers = {"Authorization": f"Bearer {token}", "accept": "application/json"}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            for data_str in datas:
                # Endpoint EDI: /movimentacoes-financeiras
                url = f"{PAGBANK_BASE}/movimentacoes-financeiras"
                params = {
                    "data": data_str,
                    "tipoMovimento": "TRANSFERENCIA_RECEBIDA",
                    "pageSize": 1000,
                }
                async with s.get(url, headers=headers, params=params) as r:
                    if r.status != 200:
                        txt = await r.text()
                        _log.warning(f"[pagbank] HTTP {r.status} ({data_str}): {txt[:200]}")
                        continue
                    data = await r.json()

                for m in data.get("data", []) or data.get("movimentos", []) or []:
                    # Filtra só PIX
                    tipo = (m.get("tipoTransacao") or m.get("tipo") or "").upper()
                    if "PIX" not in tipo and "TRANSFERENCIA" not in tipo:
                        continue
                    valor = m.get("valor") or m.get("valorMovimento") or 0
                    if isinstance(valor, str):
                        valor = float(valor.replace(",", "."))
                    nome = (
                        (m.get("origem") or {}).get("nome", "")
                        or m.get("nomePagador", "")
                        or m.get("descricao", "")
                    )
                    out.append({
                        "id":      str(m.get("id", "")) or str(m.get("idTransacao", "")),
                        "valor":   float(valor),
                        "nome":    nome,
                        "horario": m.get("data", "") or m.get("dataMovimento", ""),
                    })
        return out
    except asyncio.TimeoutError:
        _log.warning("[pagbank] timeout")
        return []
    except Exception as e:
        _log.warning(f"[pagbank] erro: {e}")
        return []


# ════════════════════════════════════════════════════════════════
#  Dispatcher
# ════════════════════════════════════════════════════════════════

async def listar_macrodroid(creds: dict, guild_id: int | str | None = None) -> list[dict]:
    """Consulta o site Fmed (PIX via notificação MacroDroid).
    
    Diferente dos outros adapters, esse precisa do guild_id pra filtrar
    PIX deste mediador (sem isso retornaria PIX de outros mediadores também).
    """
    site_url = (creds.get("site_url") or "").rstrip("/")
    if not site_url:
        return []
    if guild_id is None:
        _log.warning("[macrodroid] listar sem guild_id — vai retornar PIX de TODOS os mediadores!")
    url = f"{site_url}/pix/pending"
    if guild_id is not None:
        url += f"?guild_id={guild_id}"
    headers = {}
    token = creds.get("site_token")  # opcional (esta versão não usa)
    if token:
        headers["X-Auth"] = token
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(url, headers=headers) as r:
                if r.status != 200:
                    _log.warning(f"[macrodroid] HTTP {r.status} em {url}")
                    return []
                data = await r.json(content_type=None)
                pix_list = data.get("pix") or []
                # Normaliza pro formato comum
                out = []
                for p in pix_list:
                    try:
                        valor_str = str(p.get("valor", "0")).replace(",", ".")
                        valor = float(valor_str)
                    except Exception:
                        valor = 0.0
                    out.append({
                        "id": p.get("id", ""),
                        "valor": valor,
                        "nome": p.get("nome", ""),
                        "horario": p.get("recebidoEm", ""),
                    })
                return out
    except asyncio.TimeoutError:
        _log.warning("[macrodroid] timeout")
        return []
    except Exception as e:
        _log.warning(f"[macrodroid] erro: {e}")
        return []


async def listar_pix_recebidos(banco: str, creds: dict, desde_iso: str | None = None,
                                guild_id: int | str | None = None) -> list[dict]:
    """Roteador — chama o adapter do banco escolhido."""
    if banco == "efi":
        return await listar_efi(creds, desde_iso)
    if banco == "mercadopago":
        return await listar_mercadopago(creds, desde_iso)
    if banco == "pagbank":
        return await listar_pagbank(creds, desde_iso)
    if banco == "macrodroid":
        return await listar_macrodroid(creds, guild_id)
    if banco == "gmail":
        from utils.pix_gmail import listar_gmail
        return await listar_gmail(creds, desde_iso, guild_id)
    _log.warning(f"[pix_consulta] banco desconhecido: {banco}")
    return []


async def testar_credenciais(banco: str, creds: dict) -> tuple[bool, str]:
    """Testa se as creds funcionam — usado no painel pra validar antes de salvar.
    Retorna (ok, mensagem)."""
    try:
        if banco == "macrodroid":
            url = (creds.get("site_url") or "").rstrip("/")
            if not url:
                return (False, "❌ URL do site não configurada.")
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                    async with s.get(f"{url}/health") as r:
                        if r.status != 200:
                            return (False, f"❌ /health retornou HTTP {r.status}")
                        data = await r.json(content_type=None)
                        if not data.get("ok"):
                            return (False, "❌ /health não retornou ok=true")
                        return (True, f"✅ Site Fmed online. Pendentes: {data.get('pendentes', 0)}")
            except Exception as e:
                return (False, f"❌ Não consegui acessar {url}: {e}")

        if banco == "mercadopago":
            token = creds.get("access_token", "")
            if not token:
                return (False, "❌ Access Token não informado.")
            # GET /users/me valida o token e retorna info da conta
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.get(
                    f"{MP_BASE}/users/me",
                    headers={"Authorization": f"Bearer {token}"},
                ) as r:
                    if r.status == 401:
                        return (False, "❌ Access Token inválido (401).")
                    if r.status == 403:
                        return (False, "❌ Token sem permissões (403).")
                    if r.status != 200:
                        txt = await r.text()
                        return (False, f"❌ HTTP {r.status}: {txt[:120]}")
                    data = await r.json()
                    nome = data.get("nickname") or data.get("first_name") or "OK"
                    return (True, f"✅ Conectado como **{nome}** (ID {data.get('id', '?')}).")

        if banco == "pagbank":
            token = creds.get("token", "")
            if not token:
                return (False, "❌ Token não informado.")
            # PagBank não tem /me, então tenta listar movimentações de hoje
            from datetime import datetime, timezone
            hoje = datetime.now(timezone.utc).date().isoformat()
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.get(
                    f"{PAGBANK_BASE}/movimentacoes-financeiras",
                    headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                    params={"data": hoje, "pageSize": 1},
                ) as r:
                    if r.status == 401:
                        return (False, "❌ Token inválido (401).")
                    if r.status == 403:
                        return (False, "❌ Sem permissão (403). Verifique se o token tem acesso ao extrato.")
                    if r.status >= 500:
                        return (False, f"❌ PagBank com erro: HTTP {r.status}")
                    # 200 ou 404 (sem movimentos hoje) ambos significam que o token é válido
                    return (True, f"✅ PagBank respondeu HTTP {r.status} — token aceito.")

        if banco == "efi":
            cert_pem = creds.get("cert_pem", "")
            if not cert_pem:
                return (False, "❌ Certificado .pem não enviado. Use `/configurar_pix_efi_cert`.")
            if not (creds.get("client_id") and creds.get("client_secret")):
                return (False, "❌ Client ID ou Secret faltando.")
            # Tenta gerar token
            with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
                f.write(cert_pem)
                path = f.name
            try:
                token = await _efi_get_token(creds, path)
                if not token:
                    return (False, "❌ Falha ao gerar token (verifique cert+credenciais).")
                return (True, f"✅ EFI autenticado. Token: `{token[:20]}...`")
            finally:
                try: os.unlink(path)
                except Exception: pass

        if banco == "gmail":
            from utils.pix_gmail import testar_gmail
            return await testar_gmail(creds)

        return (False, f"❌ Banco desconhecido: {banco}")
    except Exception as e:
        return (False, f"❌ Erro inesperado: {e}")
