# cogs/testpix.py — Cog de TESTE para verificação de PIX (Nubank)
#
# Comandos (slash, só dono enxerga):
#   /testpix nubank_email_listar     → lista últimas notificações de PIX no Gmail
#   /testpix nubank_email_buscar     → busca um PIX por nome do pagador
#   /testpix nubank_api_login        → faz login na API do Nubank (gera cert se primeira vez)
#   /testpix nubank_api_extrato      → mostra últimos PIX recebidos (via API)
#   /testpix nubank_api_buscar       → busca PIX por nome via API
#
# Configs em config.py (ou variáveis de ambiente):
#   NUBANK_EMAIL_USER      = "seuemail@gmail.com"
#   NUBANK_EMAIL_APP_PASS  = "senha de app do Gmail (16 chars sem espaço)"
#   NUBANK_CPF             = "00000000000"
#   NUBANK_SENHA           = "senha do app Nubank"
#
# Dependências:
#   pip install pynubank
#   (imaplib e email já são stdlib)

import os
import re
import json
import asyncio
import logging
import imaplib
import email as email_lib
from email.header import decode_header
from datetime import datetime, timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

import config

_log = logging.getLogger("salasff.testpix")

# ── Configs (lidas de config.py com fallback em env) ─────────────────────────

EMAIL_USER     = getattr(config, "NUBANK_EMAIL_USER", os.getenv("NUBANK_EMAIL_USER", ""))
EMAIL_APP_PASS = getattr(config, "NUBANK_EMAIL_APP_PASS", os.getenv("NUBANK_EMAIL_APP_PASS", ""))
NUBANK_CPF     = getattr(config, "NUBANK_CPF", os.getenv("NUBANK_CPF", ""))
NUBANK_SENHA   = getattr(config, "NUBANK_SENHA", os.getenv("NUBANK_SENHA", ""))

CERT_PATH = Path("nubank_cert.p12")   # gerado no primeiro login da API

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Remetente do Nubank nos emails de PIX recebido
NUBANK_FROM = "todomundo@nubank.com.br"

# ── Helpers de embed ─────────────────────────────────────────────────────────

def _emb(t="", c=0x5865F2, d=""):
    return discord.Embed(title=t, color=c, description=d)

def _ok(t, d=""):  return _emb(f"✅  {t}", 0x57F287, d)
def _err(t, d=""): return _emb(f"❌  {t}", 0xED4245, d)
def _info(t, d=""): return _emb(f"ℹ️  {t}", 0x5865F2, d)

def _is_dono(uid: int) -> bool:
    """Só o dono (primeiro ADMIN_ID) pode usar esses comandos de teste."""
    if not getattr(config, "ADMIN_IDS", None):
        return False
    return int(uid) == int(config.ADMIN_IDS[0])

# ── Parser de email do Nubank ────────────────────────────────────────────────

# Exemplo de assunto: "Você recebeu uma transferência de João da Silva"
# Exemplo de corpo  : "Você recebeu R$ 25,50 de João da Silva..."

_RE_VALOR = re.compile(r"R\$\s*([\d\.]+,\d{2})")
_RE_NOME_ASSUNTO = re.compile(r"transfer[êe]ncia de (.+?)(?:\s*$|\.)", re.IGNORECASE)
_RE_NOME_CORPO   = re.compile(r"R\$\s*[\d\.]+,\d{2}\s+de\s+(.+?)(?:\s*\.|\s*$|\n)", re.IGNORECASE)

def _decode_h(raw: str) -> str:
    """Decodifica header MIME (=?utf-8?Q?...?=) pra string normal."""
    if not raw:
        return ""
    parts = decode_header(raw)
    out = ""
    for txt, enc in parts:
        if isinstance(txt, bytes):
            try:
                out += txt.decode(enc or "utf-8", errors="replace")
            except Exception:
                out += txt.decode("utf-8", errors="replace")
        else:
            out += txt
    return out

def _extrair_corpo(msg) -> str:
    """Extrai texto do email (prefere text/plain)."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    pass
        # fallback html → strip tags
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
        except Exception:
            pass
    return ""

def _parse_email_nubank(msg) -> dict | None:
    """Extrai {valor, nome, data} de uma mensagem do Nubank. None se não bater."""
    assunto = _decode_h(msg.get("Subject", ""))
    corpo   = _extrair_corpo(msg)

    nome = None
    m = _RE_NOME_ASSUNTO.search(assunto)
    if m:
        nome = m.group(1).strip()
    if not nome:
        m = _RE_NOME_CORPO.search(corpo)
        if m:
            nome = m.group(1).strip()

    valor = None
    m = _RE_VALOR.search(corpo) or _RE_VALOR.search(assunto)
    if m:
        valor = m.group(1)

    if not nome and not valor:
        return None

    data = msg.get("Date", "")
    return {"nome": nome or "?", "valor": valor or "?", "data": data, "assunto": assunto}

# ── Cliente IMAP (rodado em thread) ─────────────────────────────────────────

def _imap_buscar_pix(dias: int = 7, limite: int = 20) -> list[dict]:
    """Conecta no Gmail, baixa emails do Nubank dos últimos N dias e parseia."""
    if not EMAIL_USER or not EMAIL_APP_PASS:
        raise RuntimeError("NUBANK_EMAIL_USER / NUBANK_EMAIL_APP_PASS não configurados.")

    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    try:
        M.login(EMAIL_USER, EMAIL_APP_PASS)
        M.select("INBOX", readonly=True)

        desde = (datetime.now() - timedelta(days=dias)).strftime("%d-%b-%Y")
        # Busca por remetente Nubank desde X dias atrás
        status, ids = M.search(None, f'(FROM "{NUBANK_FROM}" SINCE "{desde}")')
        if status != "OK":
            return []

        ids_list = ids[0].split()
        ids_list = ids_list[-limite:]   # pega os mais recentes

        result = []
        for mid in reversed(ids_list):   # mais recente primeiro
            status, data = M.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            msg = email_lib.message_from_bytes(data[0][1])
            parsed = _parse_email_nubank(msg)
            if parsed:
                result.append(parsed)
        return result
    finally:
        try: M.logout()
        except Exception: pass

# ── API Nubank (pynubank) ────────────────────────────────────────────────────

def _nubank_login():
    """Faz login no Nubank. Reutiliza cert se existir, senão gera um novo.
    Retorna instância do Nubank pronta pra uso.
    """
    try:
        from pynubank import Nubank
    except ImportError:
        raise RuntimeError("pynubank não instalado. Rode: pip install pynubank")

    if not NUBANK_CPF or not NUBANK_SENHA:
        raise RuntimeError("NUBANK_CPF / NUBANK_SENHA não configurados.")

    nu = Nubank()

    if CERT_PATH.exists():
        # login direto com cert salvo
        nu.authenticate_with_cert(NUBANK_CPF, NUBANK_SENHA, str(CERT_PATH))
        return nu

    # Primeira vez: precisa gerar cert (escaneando QR no app Nubank)
    raise RuntimeError(
        f"Cert não encontrado em {CERT_PATH}. "
        "Rode `python -m pynubank.utils.cli` no terminal pra gerar o cert."
    )

def _api_extrato_pix(limite: int = 20) -> list[dict]:
    """Pega últimas movimentações via API e filtra só PIX recebidos."""
    nu = _nubank_login()
    feed = nu.get_account_feed()   # lista de eventos da conta

    pix_in = []
    for ev in feed[:200]:
        title = (ev.get("title") or "").lower()
        # PIX recebido: title costuma ser "Transferência recebida"
        if "recebida" not in title and "recebido" not in title:
            continue
        detail = ev.get("detail", "")
        amount = ev.get("amount") or ev.get("postDate") or "?"
        pix_in.append({
            "nome":  detail or "?",
            "valor": str(amount),
            "data":  ev.get("postDate", ""),
            "title": ev.get("title", ""),
        })
        if len(pix_in) >= limite:
            break
    return pix_in

# ── Cog ──────────────────────────────────────────────────────────────────────

class TestPixCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    grupo = app_commands.Group(name="testpix", description="(DONO) Testes de verificação de PIX Nubank")

    # ── EMAIL ────────────────────────────────────────────────────────────────

    @grupo.command(name="nubank_email_listar", description="Lista últimos PIX recebidos no Gmail (Nubank).")
    @app_commands.describe(dias="Quantos dias buscar (padrão 3)")
    async def cmd_email_listar(self, inter: discord.Interaction, dias: int = 3):
        if not _is_dono(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        try:
            lista = await asyncio.to_thread(_imap_buscar_pix, dias, 15)
            if not lista:
                return await inter.followup.send(
                    embed=_info("Nenhum PIX encontrado", f"Sem emails do Nubank nos últimos {dias} dias."),
                    ephemeral=True,
                )

            linhas = []
            for i, p in enumerate(lista, 1):
                linhas.append(f"`{i:02d}` **R$ {p['valor']}** — {p['nome']}\n   ╸ _{p['data'][:25]}_")
            em = _ok(f"📥 {len(lista)} PIX encontrados (Nubank/Gmail)", "\n".join(linhas[:15]))
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as ex:
            _log.exception("[testpix:email_listar]")
            await inter.followup.send(embed=_err("Erro ao consultar Gmail.", f"`{ex}`"), ephemeral=True)

    @grupo.command(name="nubank_email_buscar", description="Busca um PIX por nome do pagador (Gmail).")
    @app_commands.describe(nome="Nome (ou parte) do pagador", dias="Quantos dias buscar (padrão 3)")
    async def cmd_email_buscar(self, inter: discord.Interaction, nome: str, dias: int = 3):
        if not _is_dono(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        try:
            lista = await asyncio.to_thread(_imap_buscar_pix, dias, 30)
            alvo = nome.lower().strip()
            achados = [p for p in lista if alvo in (p["nome"] or "").lower()]
            if not achados:
                return await inter.followup.send(
                    embed=_err(f"Nenhum PIX de `{nome}` nos últimos {dias} dias."),
                    ephemeral=True,
                )
            linhas = [f"`{i+1:02d}` **R$ {p['valor']}** — {p['nome']}\n   ╸ _{p['data'][:25]}_"
                      for i, p in enumerate(achados)]
            em = _ok(f"✅ {len(achados)} PIX encontrado(s) de `{nome}`", "\n".join(linhas[:10]))
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as ex:
            _log.exception("[testpix:email_buscar]")
            await inter.followup.send(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)

    # ── API ──────────────────────────────────────────────────────────────────

    @grupo.command(name="nubank_api_login", description="Testa o login na API do Nubank (usa cert salvo).")
    async def cmd_api_login(self, inter: discord.Interaction):
        if not _is_dono(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        try:
            await asyncio.to_thread(_nubank_login)
            await inter.followup.send(
                embed=_ok("Login OK!", f"Cert: `{CERT_PATH}` carregado com sucesso."),
                ephemeral=True,
            )
        except Exception as ex:
            await inter.followup.send(embed=_err("Falha no login.", f"`{ex}`"), ephemeral=True)

    @grupo.command(name="nubank_api_extrato", description="Lista PIX recebidos via API do Nubank.")
    @app_commands.describe(limite="Quantos PIX listar (padrão 10)")
    async def cmd_api_extrato(self, inter: discord.Interaction, limite: int = 10):
        if not _is_dono(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        try:
            lista = await asyncio.to_thread(_api_extrato_pix, limite)
            if not lista:
                return await inter.followup.send(
                    embed=_info("Nenhum PIX recebido encontrado no feed."),
                    ephemeral=True,
                )
            linhas = [f"`{i+1:02d}` **{p['title']}** — {p['nome']}\n   ╸ _{p['data'][:19]}_"
                      for i, p in enumerate(lista)]
            em = _ok(f"📥 {len(lista)} PIX (Nubank API)", "\n".join(linhas))
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as ex:
            _log.exception("[testpix:api_extrato]")
            await inter.followup.send(embed=_err("Erro na API.", f"`{ex}`"), ephemeral=True)

    @grupo.command(name="nubank_api_buscar", description="Busca um PIX por nome via API.")
    @app_commands.describe(nome="Nome (ou parte) do pagador")
    async def cmd_api_buscar(self, inter: discord.Interaction, nome: str):
        if not _is_dono(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)

        await inter.response.defer(ephemeral=True)
        try:
            lista = await asyncio.to_thread(_api_extrato_pix, 50)
            alvo = nome.lower().strip()
            achados = [p for p in lista if alvo in (p["nome"] or "").lower()]
            if not achados:
                return await inter.followup.send(
                    embed=_err(f"Nenhum PIX de `{nome}` encontrado via API."),
                    ephemeral=True,
                )
            linhas = [f"`{i+1:02d}` **{p['title']}** — {p['nome']}\n   ╸ _{p['data'][:19]}_"
                      for i, p in enumerate(achados)]
            em = _ok(f"✅ {len(achados)} PIX encontrado(s) de `{nome}` (API)", "\n".join(linhas))
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as ex:
            _log.exception("[testpix:api_buscar]")
            await inter.followup.send(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TestPixCog(bot))
