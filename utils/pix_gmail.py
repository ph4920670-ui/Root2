# utils/pix_gmail.py — Adapter Gmail API para leitura de PIX por email
#
# Fluxo de configuração (1x por mediador):
#   1. Google Cloud Console → criar projeto → ativar Gmail API
#   2. Credenciais → OAuth2 Client ID (tipo "Aplicativo Web")
#      → Em "URIs de redirecionamento autorizados":
#        cole https://fmediador.discloud.app/oauth/gmail/callback
#      → client_id + client_secret
#   3. No Discord: /configurar_pix → Gmail → cola client_id + client_secret
#   4. Bot responde com URL de autorização do Google
#   5. Mediador abre URL, autoriza, é redirecionado pro fmed-site,
#      que mostra o código numa página (botão Copiar)
#   6. /configurar_pix_gmail_code → cola o código → bot salva refresh_token
#
# Depois disso: funciona sozinho pra sempre (refresh_token não expira em prod).
#
# Bancos suportados (remetente + parser):
#   Nubank       noreply@nubank.com.br
#   Inter        noreply@bancointer.com.br  /  contato@bancointer.com.br
#   C6 Bank      naoresponda@c6bank.com.br
#   PicPay       noreply@picpay.com
#   Bradesco     emails do domínio bradesco.com.br
#   Itaú         emails do domínio itau.com.br
#   Santander    emails do domínio santander.com.br
#   Genérico     qualquer email com "R$" + "Pix" no corpo

import asyncio
import base64
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header
from typing import Optional

import aiohttp

_log = logging.getLogger("salasff.pix_gmail")

# ── Constantes ────────────────────────────────────────────────
GMAIL_API   = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL   = "https://oauth2.googleapis.com/token"
OAUTH_URL   = "https://accounts.google.com/o/oauth2/auth"
SCOPE       = "https://www.googleapis.com/auth/gmail.readonly"
# Redirect URI: aponta pra rota /oauth/gmail/callback do fmed-site.
# Esta MESMA URL precisa estar cadastrada no Google Cloud Console como
# "URI de redirecionamento autorizado" do cliente OAuth (tipo Aplicativo Web).
# Pra trocar de domínio, defina a env GMAIL_REDIRECT_URI.
import os as _os
REDIRECT    = _os.environ.get("GMAIL_REDIRECT_URI",
                              "https://fmediador.discloud.app/oauth/gmail/callback")

# Remetentes conhecidos de bancos BR (minúsculos)
REMETENTES_BANCOS = [
    "noreply@nubank.com.br",
    "noreply@bancointer.com.br",
    "contato@bancointer.com.br",
    "naoresponda@c6bank.com.br",
    "noreply@picpay.com",
    "atendimento@bradesco.com.br",
    "nao-responda@bradesco.com.br",
    "naoresponda@bradesco.com.br",
    "internetbanking@itau-unibanco.com.br",
    "naoresponda@santander.com.br",
    "notificacoes@santander.com.br",
    "noreply@original.com.br",
    "noreply@bs2.com.br",
    "avisos@sicoob.com.br",
    "notificacao@sicoob.com.br",
    # Email de teste
    "ph4920670@gmail.com",
]

# Query Gmail pra buscar emails de bancos com palavras-chave de PIX/transferência
_GMAIL_QUERY_TEMPLATE = (
    "("
    + " OR ".join(f"from:{r}" for r in REMETENTES_BANCOS)
    + ") "
    "newer_than:{horas}h "
    "(subject:pix OR subject:recebeu OR subject:transferencia OR subject:credito)"
)


# ── OAuth2 helpers ────────────────────────────────────────────

def _get_client_creds() -> tuple[str, str]:
    """Retorna (client_id, client_secret) globais do app.
    Prioridade: env vars → MongoDB (configurado via painel do bot).
    Lança ValueError se não estiverem configurados.
    """
    import config as _cfg
    cid  = getattr(_cfg, "GMAIL_OAUTH_CLIENT_ID", "") or ""
    csec = getattr(_cfg, "GMAIL_OAUTH_CLIENT_SECRET", "") or ""

    # Fallback: busca no MongoDB (configurado via painel)
    if not cid or not csec:
        try:
            from utils.database import _get_db
            doc = _get_db()["bot_global_config"].find_one({"_id": "gmail_oauth"}) or {}
            cid  = cid  or doc.get("client_id", "")
            csec = csec or doc.get("client_secret", "")
        except Exception:
            pass

    if not cid or not csec:
        raise ValueError(
            "Credenciais OAuth do Gmail não configuradas. "
            "Use o botão **Configurar OAuth** no painel `/configurar_pix`."
        )
    return cid, csec


def salvar_client_creds(client_id: str, client_secret: str) -> None:
    """Salva client_id e client_secret globais no MongoDB."""
    from utils.database import _get_db
    _get_db()["bot_global_config"].update_one(
        {"_id": "gmail_oauth"},
        {"$set": {"client_id": client_id, "client_secret": client_secret}},
        upsert=True,
    )


def gerar_url_autorizacao(client_id: str | None = None) -> str:
    """Gera a URL que o mediador deve abrir no navegador.
    Se client_id não for passado, usa o global.
    """
    import urllib.parse
    if not client_id:
        client_id, _ = _get_client_creds()
    params = {
        "client_id":     client_id,
        "redirect_uri":  REDIRECT,
        "scope":         SCOPE,
        "response_type": "code",
        "access_type":   "offline",
        "prompt":        "consent",   # garante refresh_token mesmo com re-auth
    }
    return OAUTH_URL + "?" + urllib.parse.urlencode(params)


async def trocar_code_por_tokens(auth_code: str,
                                  client_id: str | None = None,
                                  client_secret: str | None = None) -> dict:
    """Troca o código de autorização por access_token + refresh_token.
    Se client_id/secret não forem passados, usa os globais.
    Retorna dict com os tokens ou lança exceção.
    """
    if not client_id or not client_secret:
        client_id, client_secret = _get_client_creds()
    payload = {
        "code":          auth_code.strip(),
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  REDIRECT,
        "grant_type":    "authorization_code",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        async with s.post(TOKEN_URL, data=payload) as r:
            data = await r.json(content_type=None)
            if "error" in data:
                raise ValueError(f"{data['error']}: {data.get('error_description', '')}")
            if not data.get("refresh_token"):
                raise ValueError("Google não retornou refresh_token. Tente novamente.")
            # Não salvamos client_id/secret no creds da guild (usamos sempre os globais).
            return {
                "refresh_token": data["refresh_token"],
                "access_token":  data.get("access_token", ""),
                "expires_at":    0,   # forçar refresh no próximo uso
            }


async def _refresh_access_token(creds: dict) -> str:
    """Usa refresh_token pra obter novo access_token. Retorna o token ou lança exceção.
    Aceita creds com ou sem client_id/secret (usa globais como fallback).
    """
    cid  = creds.get("client_id")  or None
    csec = creds.get("client_secret") or None
    if not cid or not csec:
        cid, csec = _get_client_creds()
    payload = {
        "client_id":     cid,
        "client_secret": csec,
        "refresh_token": creds["refresh_token"],
        "grant_type":    "refresh_token",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
        async with s.post(TOKEN_URL, data=payload) as r:
            data = await r.json(content_type=None)
            if "error" in data:
                raise ValueError(f"Refresh falhou: {data['error']}: {data.get('error_description','')}")
            return data["access_token"]


# ── Gmail API helpers ─────────────────────────────────────────

async def _gmail_get(access_token: str, path: str, params: dict | None = None) -> dict:
    url = f"{GMAIL_API}/{path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
        async with s.get(url, headers=headers, params=params or {}) as r:
            if r.status == 401:
                raise PermissionError("Token expirado (401)")
            if r.status != 200:
                txt = await r.text()
                raise RuntimeError(f"Gmail API HTTP {r.status}: {txt[:200]}")
            return await r.json()


async def _buscar_ids_mensagens(access_token: str, horas: int = 12,
                                 max_results: int = 50) -> list[str]:
    """Retorna IDs das mensagens recentes de bancos."""
    q = _GMAIL_QUERY_TEMPLATE.format(horas=horas)
    try:
        data = await _gmail_get(access_token, "messages", {
            "q":          q,
            "maxResults": str(max_results),
        })
        return [m["id"] for m in (data.get("messages") or [])]
    except Exception as e:
        _log.warning(f"[gmail] buscar_ids: {e}")
        return []


async def _get_mensagem(access_token: str, msg_id: str) -> dict:
    """Retorna mensagem completa (headers + body decodificado)."""
    data = await _gmail_get(access_token, f"messages/{msg_id}", {"format": "full"})
    return data


def _extrair_header(headers: list[dict], nome: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == nome.lower():
            return h.get("value", "")
    return ""


def _decodificar_parte(part: dict) -> str:
    """Decodifica base64url de uma parte do email pra texto."""
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        raw = base64.urlsafe_b64decode(data + "==")
        # Tenta UTF-8 primeiro, depois latin-1
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1", errors="replace")
    except Exception:
        return ""


def _extrair_texto(payload: dict) -> str:
    """Extrai texto plain do payload (recursivo pra multipart)."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decodificar_parte(payload)
    if mime.startswith("multipart/"):
        partes = payload.get("parts") or []
        # Prefere text/plain; fallback pra text/html
        plain = ""
        html  = ""
        for p in partes:
            t = _extrair_texto(p)
            if p.get("mimeType") == "text/plain":
                plain += t
            elif p.get("mimeType") == "text/html":
                html += t
            elif t:
                plain += t
        return plain or html
    # Fallback: tenta decodificar diretamente
    return _decodificar_parte(payload)


def _limpar_html(txt: str) -> str:
    """Remove tags HTML básicas."""
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&nbsp;", " ", txt)
    txt = re.sub(r"&amp;", "&", txt)
    txt = re.sub(r"&lt;", "<", txt)
    txt = re.sub(r"&gt;", ">", txt)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def _normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


# ── Parsers de email por banco ────────────────────────────────

# Regex genérico pra valor em reais
_RE_VALOR = re.compile(r"R\$\s*([\d.,]+)", re.IGNORECASE)

# Regex pra nome do pagador (vários formatos)
_RE_NOME_DE   = re.compile(r"\bde\s+([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Üa-zà-ü]+){0,4})\b")
_RE_NOME_PAGO = re.compile(r"pago\s+por\s+([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Üa-zà-ü]+){0,4})")
_RE_NOME_ENV  = re.compile(r"enviado\s+por\s+([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Üa-zà-ü]+){0,4})")
_RE_NOME_REM  = re.compile(r"remetente[:\s]+([A-ZÀ-Ü][a-zà-ü]+(?:\s+[A-ZÀ-Üa-zà-ü]+){0,4})")


def _parse_valor(texto: str) -> float | None:
    m = _RE_VALOR.search(texto)
    if not m:
        return None
    v = m.group(1)
    # BR: "1.234,56" → remove ponto de milhar, troca vírgula por ponto
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return round(float(v), 2)
    except ValueError:
        return None


def _parse_nome(texto: str) -> str | None:
    """Tenta extrair nome do pagador em diferentes formatos."""
    for regex in (_RE_NOME_DE, _RE_NOME_PAGO, _RE_NOME_ENV, _RE_NOME_REM):
        m = regex.search(texto)
        if m:
            nome = m.group(1).strip()
            # Descarta se muito curto ou parece lixo
            if len(nome) >= 3 and not any(c.isdigit() for c in nome):
                return nome
    return None


def _parse_nubank(subject: str, body: str) -> dict | None:
    """
    Nubank – vários formatos:
    Subject: "Você recebeu R$ 5,50 de Gabriel Fernando"
    Subject: "Pix de R$ 5,50"  +  body: "de Gabriel Fernando"
    """
    texto = f"{subject}\n{body}"

    # Tenta pegar nome do subject direto
    m = re.search(r"[Rr]\$\s*[\d.,]+\s+de\s+([A-ZÀ-Üa-zà-ü][\w\s]{2,40}?)(?:\s+via|\s*$|\n)", subject)
    nome = m.group(1).strip() if m else None

    valor = _parse_valor(texto)
    if not nome:
        nome = _parse_nome(body)

    if valor and nome:
        return {"valor": valor, "nome": nome}
    return None


def _parse_generico(subject: str, body: str) -> dict | None:
    """Parser genérico — funciona pra maioria dos bancos BR."""
    texto = f"{subject}\n{body}"
    n = _normalizar(texto)

    # Só processa se parece ser notificação de recebimento
    keywords = ["recebeu", "recebido", "credito", "entrada", "pix recebido",
                "transferencia recebida", "deposito recebido"]
    if not any(kw in n for kw in keywords):
        return None

    valor = _parse_valor(texto)
    nome  = _parse_nome(texto)

    if valor and nome:
        return {"valor": valor, "nome": nome}
    # Valor sem nome — retorna com nome genérico
    if valor:
        return {"valor": valor, "nome": "Pagador desconhecido"}
    return None


_PARSERS = {
    "nubank.com.br":       _parse_nubank,
    "bancointer.com.br":   _parse_generico,
    "c6bank.com.br":       _parse_generico,
    "picpay.com":          _parse_generico,
    "bradesco.com.br":     _parse_generico,
    "itau-unibanco.com.br":_parse_generico,
    "santander.com.br":    _parse_generico,
    "original.com.br":     _parse_generico,
    "bs2.com.br":          _parse_generico,
    "sicoob.com.br":       _parse_generico,
}


def _escolher_parser(from_addr: str):
    """Retorna parser correto pelo domínio do remetente."""
    from_lower = from_addr.lower()
    for dominio, parser in _PARSERS.items():
        if dominio in from_lower:
            return parser
    return _parse_generico


# ── Função principal ──────────────────────────────────────────

async def listar_gmail(creds: dict, desde_iso: str | None = None,
                        guild_id=None) -> list[dict]:
    """Lista PIX recebidos lendo emails de bancos via Gmail API.

    creds = { refresh_token, access_token?, expires_at?, ... }
    O client_id/secret são pegos das envs globais (config.GMAIL_OAUTH_*).
    Retorna lista no formato padrão: [{ id, valor, nome, horario }]
    """
    if not creds.get("refresh_token"):
        _log.warning("[gmail] credenciais incompletas (refresh_token ausente)")
        return []

    # Calcula janela de busca em horas
    horas = 2
    if desde_iso:
        try:
            t = datetime.fromisoformat(desde_iso.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - t
            horas = max(1, int(delta.total_seconds() / 3600) + 1)
        except Exception:
            pass
    horas = min(horas, 12)   # máximo 12h pra não sobrecarregar

    # Obtém access_token (refresh se necessário)
    try:
        access_token = await _refresh_access_token(creds)
    except Exception as e:
        _log.error(f"[gmail] refresh_token falhou: {e}")
        return []

    # Busca IDs de mensagens recentes
    ids = await _buscar_ids_mensagens(access_token, horas=horas)
    if not ids:
        return []

    _log.info(f"[gmail] {len(ids)} email(s) de bancos encontrado(s) nos últimos {horas}h")

    # Processa cada mensagem em paralelo (máx 10 simultâneos)
    sem = asyncio.Semaphore(10)

    async def processar(msg_id: str) -> dict | None:
        async with sem:
            try:
                msg = await _get_mensagem(access_token, msg_id)
                payload  = msg.get("payload", {})
                headers  = payload.get("headers", [])
                from_raw = _extrair_header(headers, "From")
                subject  = _extrair_header(headers, "Subject")
                date_raw = _extrair_header(headers, "Date")
                body     = _extrair_texto(payload)
                if body and "<" in body:
                    body = _limpar_html(body)

                parser   = _escolher_parser(from_raw)
                parsed   = parser(subject, body)
                if not parsed:
                    return None

                # Converte data do email pra ISO
                horario = ""
                if date_raw:
                    try:
                        from email.utils import parsedate_to_datetime
                        horario = parsedate_to_datetime(date_raw).isoformat()
                    except Exception:
                        horario = ""

                return {
                    "id":      f"gmail_{msg_id}",   # prefixo evita colisão com outros adapters
                    "valor":   parsed["valor"],
                    "nome":    parsed["nome"],
                    "horario": horario,
                    "_banco":  from_raw,             # debug interno
                }
            except Exception as e:
                _log.warning(f"[gmail] erro ao processar {msg_id}: {e}")
                return None

    resultados = await asyncio.gather(*[processar(mid) for mid in ids])
    pix_list = [r for r in resultados if r is not None]

    _log.info(f"[gmail] {len(pix_list)} PIX encontrado(s) nos emails")
    return pix_list


# ── Teste de credenciais ──────────────────────────────────────

async def obter_email_gmail(creds: dict) -> str | None:
    """Retorna o emailAddress da conta autorizada, ou None se falhar."""
    try:
        access_token = await _refresh_access_token(creds)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.get(
                f"{GMAIL_API}/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as r:
                if r.status != 200:
                    return None
                info = await r.json()
                return info.get("emailAddress")
    except Exception:
        return None


async def testar_gmail(creds: dict) -> tuple[bool, str]:
    """Testa se as creds do Gmail funcionam. Retorna (ok, mensagem)."""
    if not creds.get("refresh_token"):
        return False, "❌ Refresh Token ausente. Autorize o Gmail primeiro."
    try:
        # Valida que tem credenciais OAuth globais configuradas
        _get_client_creds()
    except ValueError as e:
        return False, f"❌ {e}"
    try:
        access_token = await _refresh_access_token(creds)
    except Exception as e:
        return False, f"❌ Falha ao renovar token: `{e}`"
    try:
        # Chama o endpoint de profile da própria Gmail API.
        # IMPORTANTE: não usar /oauth2/v2/userinfo, pois aquele exige escopo
        # userinfo.email/profile e nosso token tem só gmail.readonly → 401.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.get(
                f"{GMAIL_API}/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as r:
                if r.status != 200:
                    txt = await r.text()
                    return False, f"❌ Gmail API rejeitou (HTTP {r.status}): `{txt[:120]}`"
                info = await r.json()
                email  = info.get("emailAddress", "?")
                total  = info.get("messagesTotal", "?")
                return True, f"✅ Gmail autorizado: `{email}` ({total} mensagens na caixa)"
    except Exception as e:
        return False, f"❌ Erro ao verificar conta: `{e}`"
