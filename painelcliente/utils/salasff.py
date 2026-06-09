"""
Integração com a API SalasFF (https://salasff.com).

ATENÇÃO: a key é sigilosa, SEMPRE só no backend.
"""

import os
import aiohttp
from typing import Optional


SALASFF_KEY = os.getenv("SALASFF_KEY", "")
SALASFF_BASE = "https://salasff.com"


# ─── MODOS DISPONÍVEIS ─────────────────────────────────────────────
# Modos fixos (você forneceu). Se quiser puxar dinâmico da API, use listar_modos().
MODOS_FIXOS = [
    {"nome": "X1 GELO INF",            "senha": "99", "salaid": "153348828322603634"},
    {"nome": "FULL CAPA",              "senha": "99", "salaid": "538484516253345546"},
    {"nome": "4X4 SEM CARREGAMENTO",   "senha": "99", "salaid": "190739084104966408"},
    {"nome": "4x4 PADRÃO",             "senha": "99", "salaid": "310644288590470776"},
    {"nome": "4x4 padrão apostado",    "senha": "99", "salaid": "196976212809609773"},
    {"nome": "4x4 FULL OURO",          "senha": "99", "salaid": "414413601880446767"},
    {"nome": "4x4 COM CARREGAMENTO",   "senha": "99", "salaid": "111177793488530956"},
    {"nome": "4X4 3gel",               "senha": "99", "salaid": "143679168576516916"},
]


def get_modo_por_id(salaid: str) -> Optional[dict]:
    for m in MODOS_FIXOS:
        if m["salaid"] == salaid:
            return m
    return None


# ─── REQUEST HELPER ────────────────────────────────────────────────
async def _get(endpoint: str, params: dict) -> Optional[dict]:
    if not SALASFF_KEY:
        print("⚠️ SALASFF_KEY não configurada no .env")
        return {"success": False, "error": "SALASFF_KEY não configurada no .env"}
    params["key"] = SALASFF_KEY
    url = f"{SALASFF_BASE}{endpoint}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                # Log da resposta crua
                text = await resp.text()
                print(f"[salasff] GET {endpoint} → {resp.status}: {text[:300]}")
                try:
                    import json
                    return json.loads(text)
                except Exception:
                    return {
                        "success": False,
                        "error": f"resposta não-JSON ({resp.status})",
                        "raw": text[:200],
                    }
    except aiohttp.ClientError as e:
        msg = f"erro de conexão: {e}"
        print(f"⚠️ SalasFF {endpoint}: {msg}")
        return {"success": False, "error": msg}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"⚠️ SalasFF {endpoint}: {msg}")
        return {"success": False, "error": msg}


# ─── ENDPOINTS ─────────────────────────────────────────────────────
async def listar_modos() -> Optional[dict]:
    """GET /modos — também retorna quantidade de salas no campo `salas`."""
    return await _get("/modos", {})


async def saldo_global() -> Optional[int]:
    """Consulta o saldo total da key (de salas)."""
    data = await listar_modos()
    if data and data.get("success"):
        return data.get("salas")
    return None


async def criar_sala(salaid: str, iniciar: int = 4, senha: Optional[str] = None) -> Optional[dict]:
    """GET /criar — retorna pedidoid e dados iniciais da sala."""
    params = {"salaid": salaid, "iniciar": iniciar}
    if senha:
        params["senha"] = senha
    return await _get("/criar", params)


async def info_sala(pedidoid: str) -> Optional[dict]:
    """GET /info?pedidoid=..."""
    # /info não exige key segundo a doc
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{SALASFF_BASE}/info",
                params={"pedidoid": pedidoid},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                return await resp.json(content_type=None)
    except Exception as e:
        print(f"⚠️ Erro /info: {e}")
        return None


async def iniciar_sala(pedidoid: str) -> Optional[dict]:
    return await _get("/iniciar", {"pedidoid": pedidoid})


async def expulsar_jogador(pedidoid: str, jogadorid: str) -> Optional[dict]:
    return await _get("/expulsar", {"pedidoid": pedidoid, "jogadorid": jogadorid})


async def resgatar_keys(serials: list[str]) -> Optional[dict]:
    """Resgata códigos SALA... pra adicionar saldo na key."""
    return await _get("/resgatar", {"serials": ",".join(serials)})


def contar_jogadores(info: dict) -> int:
    """Conta total de jogadores nas equipes."""
    try:
        equipes = info.get("sala", {}).get("equipes", [])
        return sum(len(e.get("jogadores", [])) for e in equipes)
    except Exception:
        return 0
