# utils/api.py — SalasFF Nova API (oauth_key, sshash)

import asyncio
import aiohttp
from typing import Optional, Any
import config
import logging

_log = logging.getLogger("salasff.api")

# Mapeamento modo interno → modo da nova API
# 1=Normal, 2=Infinito, 3=Full Capa
_MODO_MAP = {
    1: 1,  # Normal → sem carregamento
    2: 3,  # Infinito → 1x1 gel inf
    3: 4,  # Full Capa → full capa
}


class SalasFFAPI:
    def __init__(self):
        # Carrega API ativa do botconfig.json (se existir), senão usa config padrão
        try:
            import json as _json, os as _os
            _cfg_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "botconfig.json")
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _bcfg = _json.load(_f)
            _api = str(_bcfg.get("api_ativa", "2"))
            if _api == "1":
                self.base = config.API1_URL.rstrip("/")
                self.key  = config.API1_KEY
            else:
                self.base = config.API2_URL.rstrip("/")
                self.key  = _bcfg.get("api2_key", config.API2_KEY)
        except Exception:
            self.base = config.SALASFF_BASE_URL.rstrip("/")
            self.key  = config.SALASFF_API_KEY
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20, connect=8),
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _post(self, endpoint: str, body: dict = None, tentativas: int = 2) -> dict:
        url = f"{self.base}/{endpoint.lstrip('/')}"
        headers = {
            "Authorization": self.key,
            "Content-Type": "application/json",
        }
        for t in range(1, tentativas + 1):
            try:
                session = await self._get_session()
                async with session.post(url, headers=headers, json=body or {}) as resp:
                    data = await resp.json(content_type=None)
                    _log.debug(f"[API POST] {endpoint} status={resp.status}")
                    return data
            except asyncio.TimeoutError:
                if t == tentativas:
                    return {"sucesso": False, "msg": "API sem resposta."}
                await asyncio.sleep(1)
            except aiohttp.ClientConnectorError:
                if t == tentativas:
                    return {"sucesso": False, "msg": "Sem conexão com a API."}
                await self.close()
                await asyncio.sleep(1.5)
            except Exception as e:
                return {"sucesso": False, "msg": f"Erro: {e}"}
        return {"sucesso": False, "msg": "Falha na API."}

    async def _get(self, endpoint: str, params: dict = None, tentativas: int = 2) -> dict:
        url = f"{self.base}/{endpoint.lstrip('/')}"
        # API 1 usa "key", API 2 usa "oauth_key"
        key_param = "key" if self._is_api1() else "oauth_key"
        p = {key_param: self.key}
        if params:
            p.update(params)
        for t in range(1, tentativas + 1):
            try:
                session = await self._get_session()
                async with session.get(url, params=p) as resp:
                    data = await resp.json(content_type=None)
                    _log.debug(f"[API GET] {endpoint} status={resp.status}")
                    return data
            except asyncio.TimeoutError:
                if t == tentativas:
                    return {"sucesso": False, "msg": "API sem resposta."}
                await asyncio.sleep(1)
            except aiohttp.ClientConnectorError:
                if t == tentativas:
                    return {"sucesso": False, "msg": "Sem conexão com a API."}
                await self.close()
                await asyncio.sleep(1.5)
            except Exception as e:
                return {"sucesso": False, "msg": f"Erro: {e}"}
        return {"sucesso": False, "msg": "Falha na API."}

    # ── Helpers de normalização ────────────────────────────────

    def _normalizar_sala(self, data: dict) -> dict:
        """Converte resposta da nova API para o formato interno do bot."""
        room = data.get("room") or {}

        status_str = data.get("status_atual") or data.get("status", "")
        status_map = {
            "SALA_CRIADA":       3,
            "ESPERANDO_COMECAR": 3,
            "SALA_INICIADA":     4,
            "PARTIDA_INICIADA":  4,
            "CRIANDO":           2,
        }
        status_int = status_map.get(str(status_str).upper(), 3)

        sala = {
            "id":    data.get("roomId") or room.get("id", "—"),
            "senha": data.get("senha")  or room.get("senha", "—"),
            "nome":  data.get("nome")   or room.get("nome", "—"),
            # Novo: link de acesso direto à sala (QR code / deep link Garena).
            # Disponível quando status == 3 (sala criada). Pode vir em vários campos
            # dependendo da versão da API; tentamos todos e ficamos com o primeiro
            # que existir.
            "link":  data.get("link") or room.get("link") or data.get("sala_link") or "",
        }

        sshash = data.get("sshash") or data.get("pedidoid", "")

        return {
            "pedidoid":          sshash,
            "sshash":            sshash,
            "status":            status_int,
            "sala":              sala,
            "success":           data.get("sucesso", True),
            "msg":               data.get("msg", ""),
            "inicio_automatico": "",
        }

    # ── Endpoints ──────────────────────────────────────────────

    def _is_api1(self) -> bool:
        return self.base.rstrip("/") == config.API1_URL.rstrip("/")

    def _falhou(self, data: dict) -> bool:
        """Retorna True se a resposta indica falha definitiva na API."""
        if data.get("pedidoid") or data.get("sshash"):
            return False
        if data.get("status") == "SALA_CRIADA":
            return False
        return True

    async def _criar_api1(self, salaid: str, iniciar: int, senha: str = None) -> dict:
        params: dict[str, Any] = {"salaid": salaid, "iniciar": iniciar}
        if senha:
            params["senha"] = senha
        for tentativa in range(1, 4):
            data = await self._get("criar", params)
            _log.info(f"[criar-api1] t={tentativa} pedidoid={data.get('pedidoid')} status={data.get('status')}")
            if data.get("pedidoid"):
                return data
            if data.get("status") in (2, 3) and not data.get("pedidoid"):
                await asyncio.sleep(1.5); continue
            msg = str(data.get("msg", "")).lower()
            if "bloqueada" in msg or "bloqueado" in msg:
                return {"pedidoid": None, "success": False, "msg": data.get("msg", "Key bloqueada")}
            if any(x in msg for x in ["manutenção", "manutencao"]):
                if tentativa < 3:
                    await asyncio.sleep(1.5); continue
            return data
        return data

    def _key_esgotada(self, data: dict) -> bool:
        """Retorna True se o erro indica que a key está esgotada/inválida (deve rotacionar)."""
        status = str(data.get("status", "")).upper()
        msg = str(data.get("msg", "")).lower()
        if status in ("KEY_INVALIDA", "KEY_EXPIRADA", "KEY_ESGOTADA"):
            return True
        if any(x in msg for x in [
            "nao existe", "não existe",
            "key inválida", "key invalida",
            "sem usos", "usos esgotados",
            "limite atingido", "atingiu o limite",
            "limite de usos", "key atingiu",
            "key bloqueada", "bloqueada",
            "sem saldo", "saldo esgotado",
        ]):
            return True
        return False

    async def _verificar_usos_restantes(self) -> int:
        """Consulta verificar-key e retorna usos_restantes. Retorna -1 em caso de erro."""
        try:
            url = f"{self.base}/api/verificar-key"
            session = await self._get_session()
            async with session.post(
                url,
                headers={"Authorization": self.key, "Content-Type": "application/json"},
                json={},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                data = await r.json(content_type=None)
                restantes = data.get("usos_restantes", -1)
                return int(restantes) if restantes is not None else -1
        except Exception as e:
            _log.debug(f"[verificar-usos] Erro: {e}")
            return -1

    async def _criar_api2(self, modo: int, iniciar: int, senha: str = None) -> dict:
        modo_api = _MODO_MAP.get(modo, 1) if modo else 1
        body: dict[str, Any] = {"modo": modo_api, "auto_start": max(1, min(8, iniciar))}
        if senha:
            body["senha"] = senha

        bloqueios_consecutivos = 0

        for tentativa in range(1, 4):
            data = await self._post("api/v1/create:room", body)
            _log.info(f"[criar-api2] t={tentativa} status={data.get('status')} sshash={data.get('sshash')}")
            if data.get("status") == "SALA_CRIADA" or data.get("sshash"):
                return self._normalizar_sala(data)

            # KEY_BLOQUEADA — pode ser temporária OU key esgotada
            if data.get("status") == "KEY_BLOQUEADA":
                bloqueios_consecutivos += 1
                # Na 2ª ocorrência, verifica se a key está de fato esgotada
                if bloqueios_consecutivos >= 2:
                    usos = await self._verificar_usos_restantes()
                    _log.info(f"[criar-api2] KEY_BLOQUEADA consecutiva — usos_restantes={usos}")
                    if usos == 0:
                        # Key esgotada → rotacionar
                        _log.warning(f"[multi-key] KEY_BLOQUEADA com 0 usos → key esgotada, rotacionando...")
                        try:
                            from cogs.botconfig import avancar_api2_key
                            nova = avancar_api2_key()
                            if nova:
                                self.key = nova
                                return await self._criar_api2(modo, iniciar, senha)
                        except Exception as e:
                            _log.error(f"[multi-key] Erro ao rotacionar: {e}")
                        return {"pedidoid": None, "success": False, "msg": "Todas as keys esgotadas."}
                await asyncio.sleep(1.5)
                continue

            if data.get("status") == "RATE_LIMITED":
                await asyncio.sleep(1.5)
                continue

            # Key esgotada/inválida → rotacionar imediatamente
            if self._key_esgotada(data):
                _log.warning(f"[multi-key] Key esgotada/inválida ({data.get('status')}) → rotacionando...")
                try:
                    from cogs.botconfig import avancar_api2_key
                    nova = avancar_api2_key()
                    if nova:
                        self.key = nova
                        return await self._criar_api2(modo, iniciar, senha)
                except Exception as e:
                    _log.error(f"[multi-key] Erro ao rotacionar key: {e}")
                return {"pedidoid": None, "success": False, "msg": data.get("msg", "Key esgotada")}

            msg = str(data.get("msg", "")).lower()
            if any(x in msg for x in ["manutenção", "manutencao", "aguarde"]):
                if tentativa < 3:
                    await asyncio.sleep(1.5)
                    continue
            return {"pedidoid": None, "success": False, "msg": data.get("msg", "Erro na API")}

        return {"pedidoid": None, "success": False, "msg": "Falha ao criar sala."}

    def _alternar_api(self):
        """Mantido para compatibilidade — não faz mais troca permanente."""
        pass

    async def criar_sala(
        self,
        salaid: str,
        iniciar: int = config.DEFAULT_INICIAR_MINUTOS,
        senha: str = None,
        modo: int = None,
        saldo_origem: str = None,
    ) -> dict:
        try:
            from cogs.botconfig import get_api_principal, get_api_secundario, get_anticai_ativo, get_api2_key
            principal   = get_api_principal()   # "1" ou "2"
            secundario  = get_api_secundario()  # oposto
            anticai     = get_anticai_ativo()
        except Exception:
            principal, secundario, anticai = "2", "1", True

        # Se a sala vem de uma origem específica (venda/ranking/indicacao) e o admin
        # configurou uma API pra essa origem, ela tem prioridade sobre o "principal".
        if saldo_origem:
            try:
                from cogs.botconfig import get_api_por_origem
                api_origem = get_api_por_origem(saldo_origem)
                if api_origem in ("1", "2"):
                    principal  = api_origem
                    secundario = "2" if principal == "1" else "1"
                    _log.info(f"[criar_sala] origem={saldo_origem} → forçando API {principal}")
            except Exception as _ex:
                _log.warning(f"[criar_sala origem] {_ex}")

        # ── Monta base/key para cada API ────────────────────────────────────
        def _base_key(api_num: str):
            if api_num == "1":
                return config.API1_URL.rstrip("/"), config.API1_KEY
            try:
                from cogs.botconfig import get_api2_key
                k = get_api2_key()
            except Exception:
                k = config.API2_KEY
            return config.API2_URL.rstrip("/"), k

        async def _tentar(api_num: str) -> dict:
            """Tenta criar sala na API indicada sem alterar self.base/self.key permanentemente."""
            b, k = _base_key(api_num)
            b_orig, k_orig = self.base, self.key
            self.base, self.key = b, k
            try:
                if api_num == "1":
                    return await self._criar_api1(salaid, iniciar, senha)
                else:
                    return await self._criar_api2(modo, iniciar, senha)
            finally:
                # Sempre restaura para o principal (self.base/key = principal)
                bp, kp = _base_key(principal)
                self.base, self.key = bp, kp

        # ── Garante que self.base/key reflita o principal ────────────────────
        bp, kp = _base_key(principal)
        self.base, self.key = bp, kp

        # ── Tentativa no Principal ───────────────────────────────────────────
        _log.info(f"[criar_sala] Tentando API {principal} (principal)")
        data = await _tentar(principal)

        if not self._falhou(data):
            return data

        # ── Fallback para Secundário (só se anti-caí ativo) ─────────────────
        if anticai:
            _log.warning(f"[anti-caí] API {principal} (principal) falhou → tentando API {secundario} (secundário)")
            data2 = await _tentar(secundario)
            if not self._falhou(data2):
                _log.info(f"[anti-caí] API {secundario} (secundário) funcionou. Próxima sala tentará {principal} (principal) novamente.")
                return data2
            _log.error(f"[anti-caí] Ambas as APIs falharam.")

        return data

    async def info_sala(self, sshash: str) -> dict:
        if self._is_api1():
            return await self._get("info", {"pedidoid": sshash})
        data = await self._post("api/v2/info:room", {"sshash": sshash})
        return self._normalizar_sala(data)

    async def iniciar_partida(self, sshash: str) -> dict:
        if self._is_api1():
            return await self._get("iniciar", {"pedidoid": sshash})
        data = await self._post("api/v2/start:room", {"sshash": sshash})
        ok = data.get("status") == "PARTIDA_INICIADA" or data.get("success") == "ok"
        return {"success": ok, "msg": data.get("msg", ""), "sala": {"id": "—", "senha": "—", "nome": "—"}}

    async def expulsar_jogador(self, sshash: str, accountid: str) -> dict:
        if self._is_api1():
            return await self._get("expulsar", {"pedidoid": sshash, "jogadorid": accountid})
        data = await self._post("api/v2/expulsar-player", {
            "sshash": sshash,
            "accountid": int(accountid) if str(accountid).isdigit() else accountid,
        })
        ok = data.get("status") == "EXPULSO" or data.get("success") == "ok"
        return {"success": ok, "msg": data.get("msg", "")}

    async def aguardar_sala_pronta(
        self,
        sshash: str,
        timeout: int = config.SALA_TIMEOUT_SECONDS,
        interval: float = config.POLLING_INTERVAL_SECONDS,
    ) -> dict:
        elapsed = 0.0
        while elapsed < timeout:
            data = await self.info_sala(sshash)
            if data.get("status", 0) >= 3:
                return data
            if not data.get("success", True):
                return data
            await asyncio.sleep(interval)
            elapsed += interval
        return {"success": False, "msg": "Sala não ficou pronta a tempo.", "status": 2}

    async def status_sistema(self) -> dict:
        if self._is_api1():
            return await self._get("modos")
        return await self._get("api/info-system")

    async def verificar_key(self) -> dict:
        if self._is_api1():
            return await self._get("modos")
        return await self._post("api/verificar-key")

    async def listar_modos(self) -> dict:
        if self._is_api1():
            return await self._get("modos")
        return await self._get("api/status")

    async def listar_salas(self) -> dict:
        if self._is_api1():
            return await self._get("listar")
        return {}

    async def imagem_sala(self, sshash: str) -> bytes | None:
        return None


api = SalasFFAPI()
