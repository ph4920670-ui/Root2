# utils/pix_site.py — Cliente do site Fmed (PIX via Nubank/MacroDroid)
#
# Faz polling em GET {SITE_URL}/pix/pending a cada SITE_POLL_INTERVAL segundos.
# Pra cada PIX novo, dispara o evento Discord 'on_pix_recebido' (cogs reagem).
# Após o cog confirmar o processamento, dá ACK em POST {SITE_URL}/pix/ack.
#
# Configuração (botconfig.json):
# {
#   "pix_site_url":      "https://fmediador.discloud.app",
#   "pix_site_token":    "tk_qualquer_coisa_aleatoria_e_grande",
#   "pix_site_interval": 3,            # segundos entre polls (padrão 3)
#   "pix_site_enabled":  true          # liga/desliga (padrão true se url+token existem)
# }
#
# Ou via env vars: PIX_SITE_URL, PIX_SITE_TOKEN, PIX_SITE_INTERVAL.

import os
import json
import asyncio
import logging
from typing import Optional

import aiohttp

_log = logging.getLogger("salasff.pix_site")

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BOTCONFIG = os.path.join(_BASE_DIR, "botconfig.json")


def _load_cfg() -> dict:
    try:
        with open(_BOTCONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_site_config() -> dict:
    cfg = _load_cfg()
    env_url      = os.environ.get("PIX_SITE_URL")
    env_token    = os.environ.get("PIX_SITE_TOKEN")
    env_interval = os.environ.get("PIX_SITE_INTERVAL")

    url      = env_url      or cfg.get("pix_site_url", "")
    token    = env_token    or cfg.get("pix_site_token", "")  # opcional
    interval = env_interval or cfg.get("pix_site_interval", 3)

    # enabled: basta ter URL. Token é opcional (site pode rodar sem auth).
    if env_url:
        enabled = True
    else:
        enabled = cfg.get("pix_site_enabled", bool(url))

    try:
        interval = max(1, int(interval))
    except Exception:
        interval = 3
    return {
        "url":      url.rstrip("/") if url else "",
        "token":    token,
        "interval": interval,
        "enabled":  bool(enabled and url),
    }


class PixSiteClient:
    """Cliente HTTP do endpoint /pix do site Fmed."""

    def __init__(self, bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        # IDs já processados nesta sessão (evita reprocessar caso ack falhe e PIX volte)
        self._vistos: set[str] = set()
        # Limita memória do _vistos (mantém só últimos 500)
        self._vistos_lista: list[str] = []

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10, connect=5)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    def _registrar_visto(self, pix_id: str):
        if pix_id in self._vistos:
            return
        self._vistos.add(pix_id)
        self._vistos_lista.append(pix_id)
        # Limita a 500 IDs
        if len(self._vistos_lista) > 500:
            antigo = self._vistos_lista.pop(0)
            self._vistos.discard(antigo)

    async def fetch_pendentes(self, guild_id: int | str | None = None) -> list[dict]:
        """Busca PIX pendentes. Se guild_id passado, filtra no servidor."""
        cfg = get_site_config()
        if not cfg["enabled"]:
            return []
        url = f"{cfg['url']}/pix/pending"
        if guild_id is not None:
            url += f"?guild_id={guild_id}"
        headers = {"X-Auth": cfg["token"]} if cfg["token"] else {}
        try:
            session = await self._get_session()
            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    _log.warning("[pix_site] token inválido (401) — verifique pix_site_token")
                    return []
                if resp.status != 200:
                    _log.warning(f"[pix_site] HTTP {resp.status} em /pix/pending")
                    return []
                data = await resp.json(content_type=None)
                return data.get("pix") or []
        except asyncio.TimeoutError:
            _log.warning("[pix_site] timeout em /pix/pending")
            return []
        except Exception as e:
            _log.warning(f"[pix_site] erro em /pix/pending: {e}")
            return []

    async def ack(self, ids: list[str]) -> bool:
        if not ids:
            return True
        cfg = get_site_config()
        if not cfg["enabled"]:
            return False
        url = f"{cfg['url']}/pix/ack"
        try:
            session = await self._get_session()
            body = {"ids": ids}
            headers = {"Content-Type": "application/json"}
            if cfg["token"]:
                headers["X-Auth"] = cfg["token"]
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status == 200:
                    return True
                _log.warning(f"[pix_site] ack HTTP {resp.status}")
                return False
        except Exception as e:
            _log.warning(f"[pix_site] erro em /pix/ack: {e}")
            return False

    async def tick(self):
        """Chamado pelo loop periódico — busca PIX e dispara evento."""
        pendentes = await self.fetch_pendentes()
        if not pendentes:
            return

        novos: list[dict] = []
        for pix in pendentes:
            pid = str(pix.get("id") or "")
            if not pid or pid in self._vistos:
                continue
            self._registrar_visto(pid)
            novos.append(pix)

        if not novos:
            # Todos já vistos nesta sessão — provavelmente ack falhou antes.
            # Tenta dar ack de novo pra limpar o site.
            ids = [str(p.get("id")) for p in pendentes if p.get("id")]
            await self.ack(ids)
            return

        _log.info(f"[pix_site] {len(novos)} PIX novo(s) recebido(s)")

        # Dispara evento pro bot — qualquer cog pode escutar com:
        #   @commands.Cog.listener()
        #   async def on_pix_recebido(self, pix): ...
        for pix in novos:
            try:
                self.bot.dispatch("pix_recebido", pix)
            except Exception as e:
                _log.error(f"[pix_site] erro ao despachar evento: {e}")

        # ACK imediato (cogs processam de forma assíncrona; se falharem,
        # o PIX já foi salvo internamente pelo cog ou perdido — comportamento
        # idêntico ao do MisticPay/EFI, que também não tem retry após confirmação)
        ids = [str(p["id"]) for p in novos if p.get("id")]
        await self.ack(ids)


# ── Singleton + interface pública ───────────────────────────────────

_client: Optional[PixSiteClient] = None


def get_client(bot) -> PixSiteClient:
    global _client
    if _client is None:
        _client = PixSiteClient(bot)
    return _client


async def start_polling(bot):
    """Liga o cliente. Chamado uma vez no setup_hook do bot."""
    cfg = get_site_config()
    if not cfg["enabled"]:
        _log.info("[pix_site] desligado (sem URL/token configurados)")
        return
    _log.info(
        f"[pix_site] polling ativo em {cfg['url']} a cada {cfg['interval']}s"
    )
    get_client(bot)  # instancia


async def stop_polling():
    """Fecha sessão HTTP. Chamado no shutdown."""
    global _client
    if _client:
        await _client.close()
        _client = None
