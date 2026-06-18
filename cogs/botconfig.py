# cogs/botconfig.py — Painel de Configuração Admin (v3)

import json, os, logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import aiohttp

import config
_ADMIN_GUILDS = [discord.Object(id=gid) for gid in config.OWNER_GUILD_IDS]
from utils.emojis import PE, BOT, STATS, SETTINGS, INFO, DOT, ON, OFF, GIFT, CART, TOP, MOBILE, MONEY, PIX, URL, PRESENTE

_BR = ZoneInfo("America/Sao_Paulo")
_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

def _cfg_path():
    return os.path.join(_BASE, "botconfig.json")

_DEF = {"vendas_ativas":True,"cor_primaria":"2F3136","cor_sucesso":"00FF7F","cor_erro":"FF4444","cargo_cliente_id":None,"canal_log_id":None,"canal_backup_id":None,"canal_compras_pub_id":None,"canal_bonus_pub_id":None,"cargos_por_qtd":{},"preco_por_sala":0.09,"banco_pix":"efi","evento_ativo":None,"salas_gratis":10}

def get_api_ativa() -> str:
    """Retorna '1' ou '2'. Alias de get_api_principal para compatibilidade."""
    return str(carregar_cfg().get("api_principal", carregar_cfg().get("api_ativa", "2")))

def get_api_principal() -> str:
    """Retorna a API principal ('1' ou '2'). É sempre tentada primeiro."""
    cfg = carregar_cfg()
    return str(cfg.get("api_principal", cfg.get("api_ativa", "2")))

def get_api_secundario() -> str:
    """Retorna a API secundária (o oposto da principal)."""
    return "1" if get_api_principal() == "2" else "2"

def set_api_principal(valor: str):
    """Define a API principal e atualiza config em memória."""
    cfg = carregar_cfg()
    cfg["api_principal"] = valor
    cfg["api_ativa"] = valor  # compatibilidade
    salvar_cfg(cfg)
    import config as _config
    from utils.api import api
    if valor == "1":
        api.base = _config.API1_URL.rstrip("/")
        api.key  = _config.API1_KEY
        _config.SALASFF_BASE_URL = _config.API1_URL
        _config.SALASFF_API_KEY  = _config.API1_KEY
    else:
        _key = cfg.get("api2_key", _config.API2_KEY)
        api.base = _config.API2_URL.rstrip("/")
        api.key  = _key
        _config.SALASFF_BASE_URL = _config.API2_URL
        _config.SALASFF_API_KEY  = _key


# ── API por origem do saldo (venda / ranking / indicacao) ──
def get_api_por_origem(origem: str) -> str:
    """Retorna '1', '2' ou '' se não configurada para essa origem.
    Se '', a sala usa a API principal normal.
    Origens válidas: venda, ranking, indicacao.
    """
    cfg = carregar_cfg()
    mp = cfg.get("api_por_origem") or {}
    v = str(mp.get(origem, "")).strip()
    return v if v in ("1", "2") else ""


def set_api_por_origem(origem: str, valor: str):
    """Define API ('1' / '2' / '' para limpar) pra uma origem específica."""
    if origem not in ("venda", "ranking", "indicacao"):
        return
    cfg = carregar_cfg()
    mp = cfg.get("api_por_origem") or {}
    if valor in ("1", "2"):
        mp[origem] = valor
    else:
        mp.pop(origem, None)
    cfg["api_por_origem"] = mp
    salvar_cfg(cfg)

def get_anticai_ativo() -> bool:
    """Retorna se o anti-caí (failover automático entre APIs) está ativo."""
    return bool(carregar_cfg().get("anticai_ativo", True))

def set_anticai_ativo(valor: bool):
    cfg = carregar_cfg()
    cfg["anticai_ativo"] = valor
    salvar_cfg(cfg)

def get_api2_key() -> str:
    """Retorna a key atual da API 2."""
    import config as _config
    return carregar_cfg().get("api2_key", _config.API2_KEY)

def set_api2_key(nova_key: str):
    """Troca a key da API 2 e atualiza em tempo real se ela estiver ativa."""
    import config as _config
    cfg = carregar_cfg()
    cfg["api2_key"] = nova_key
    salvar_cfg(cfg)
    _config.API2_KEY = nova_key
    # Se API 2 estiver ativa, atualiza instância imediatamente
    if str(cfg.get("api_ativa", "2")) == "2":
        _config.SALASFF_API_KEY = nova_key
        from utils.api import api
        api.key = nova_key

# ── Sistema de múltiplas keys API 2 ──────────────────────────────────────────

def get_api2_keys_lista() -> list:
    """Retorna a lista de keys da API 2."""
    return carregar_cfg().get("api2_keys_lista", [])

def set_api2_keys_lista(lista: list):
    """Salva nova lista de keys e ativa a primeira imediatamente."""
    cfg = carregar_cfg()
    cfg["api2_keys_lista"] = lista
    cfg["api2_keys_idx"] = 0
    salvar_cfg(cfg)
    if lista:
        set_api2_key(lista[0])

def get_api2_keys_idx() -> int:
    """Retorna o índice da key ativa na lista."""
    return int(carregar_cfg().get("api2_keys_idx", 0))

def avancar_api2_key() -> str | None:
    """Remove a key atual (esgotada) da lista e avança pra próxima.
    Retorna a nova key ou None se a lista ficar vazia."""
    import logging
    _log_bc = logging.getLogger("salasff.botconfig")
    cfg = carregar_cfg()
    lista = cfg.get("api2_keys_lista", [])
    if not lista:
        return None
    idx_atual = int(cfg.get("api2_keys_idx", 0))
    if idx_atual >= len(lista):
        idx_atual = 0

    # Remove a key esgotada da lista
    key_removida = lista[idx_atual]
    nova_lista = lista[:idx_atual] + lista[idx_atual+1:]
    _log_bc.warning(f"[multi-key] Removendo key esgotada {idx_atual+1}/{len(lista)}: {key_removida[:14]}...")

    if not nova_lista:
        cfg["api2_keys_lista"] = []
        cfg["api2_keys_idx"] = 0
        salvar_cfg(cfg)
        _log_bc.error(f"[multi-key] Lista vazia após remoção!")
        return None

    # Próxima key (ainda no mesmo índice, pois removemos a atual)
    novo_idx = idx_atual % len(nova_lista)
    nova = nova_lista[novo_idx]
    cfg["api2_keys_lista"] = nova_lista
    cfg["api2_keys_idx"] = novo_idx
    cfg["api2_key"] = nova
    salvar_cfg(cfg)
    # Atualiza em memória imediatamente
    import config as _config
    _config.API2_KEY = nova
    if str(cfg.get("api_ativa", "2")) == "2":
        _config.SALASFF_API_KEY = nova
        from utils.api import api
        api.key = nova
    _log_bc.warning(f"[multi-key] → avançou para {nova[:14]}... ({len(nova_lista)} keys restantes)")
    return nova


def limpar_keys_invalidas(invalidas: list) -> tuple[int, str | None]:
    """Remove keys da lista (passe a lista de keys inválidas a remover).
    Retorna (qtd_removidas, key_ativa_atual)."""
    import logging
    _log_bc = logging.getLogger("salasff.botconfig")
    cfg = carregar_cfg()
    lista = list(cfg.get("api2_keys_lista", []))
    if not lista or not invalidas:
        return 0, cfg.get("api2_key")
    inv_set = set(invalidas)
    nova_lista = [k for k in lista if k not in inv_set]
    removidas = len(lista) - len(nova_lista)
    if removidas == 0:
        return 0, cfg.get("api2_key")

    # Reajusta índice e key ativa
    cfg["api2_keys_lista"] = nova_lista
    if not nova_lista:
        cfg["api2_keys_idx"] = 0
        cfg["api2_key"] = ""
        salvar_cfg(cfg)
        _log_bc.warning(f"[limpar-keys] {removidas} removidas — lista vazia!")
        return removidas, None

    cfg["api2_keys_idx"] = 0
    nova_ativa = nova_lista[0]
    cfg["api2_key"] = nova_ativa
    salvar_cfg(cfg)
    import config as _config
    _config.API2_KEY = nova_ativa
    if str(cfg.get("api_ativa", "2")) == "2":
        _config.SALASFF_API_KEY = nova_ativa
        from utils.api import api
        api.key = nova_ativa
    _log_bc.info(f"[limpar-keys] {removidas} removidas — ativa: {nova_ativa[:14]}... ({len(nova_lista)} restantes)")
    return removidas, nova_ativa

def set_api_ativa(valor: str):
    """Alias de set_api_principal para compatibilidade."""
    set_api_principal(valor)

def get_evento_ativo() -> str | None:
    """Retorna o evento ativo: '10+1', '10+2' ou None."""
    return carregar_cfg().get("evento_ativo", None)

def set_evento_ativo(valor: str | None):
    """Define o evento ativo. valor deve ser '10+1', '10+2' ou None."""
    cfg = carregar_cfg()
    cfg["evento_ativo"] = valor
    salvar_cfg(cfg)

def carregar_cfg():
    from utils.database import botconfig_load
    mongo = botconfig_load()
    # Fallback pro arquivo local se MongoDB vazio
    if not mongo:
        try:
            with open(_cfg_path(), "r", encoding="utf-8") as f:
                mongo = json.load(f)
            # Migra pro MongoDB automaticamente
            from utils.database import botconfig_save
            botconfig_save({**_DEF, **mongo})
        except: pass
    return {**_DEF, **mongo}

def salvar_cfg(d):
    from utils.database import botconfig_save
    botconfig_save(d)
    # Mantém arquivo local como backup
    try:
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except: pass

def _emb(t, c):
    em = discord.Embed(title=t, color=c)
    return em


# ═══════════════════════════════════════════
#  Mensagem Automática — cada canal independente
#  Estrutura em botconfig.json:
#    msg_auto: {
#      canais: [
#        {canal_id, mensagem, intervalo_min, ativo, ultima_msg_id},
#        ...
#      ]
#    }
# ═══════════════════════════════════════════
def get_msg_auto() -> dict:
    cfg = carregar_cfg()
    ma = cfg.get("msg_auto", {})

    # Migração do formato antigo (intervalo/ativo global) → novo (por canal)
    if "canal_id" in ma and "canais" not in ma:
        # Formato mais antigo ainda — canal único
        canais = []
        if ma.get("canal_id") and ma.get("mensagem"):
            canais.append({
                "canal_id": ma["canal_id"],
                "mensagem": ma["mensagem"],
                "intervalo_min": int(ma.get("intervalo_min", 30)),
                "ativo": bool(ma.get("ativo", False)),
                "ultima_msg_id": ma.get("ultima_msg_id"),
            })
        ma = {"canais": canais}
        cfg["msg_auto"] = ma
        salvar_cfg(cfg)
    elif "canais" in ma and ("intervalo_min" in ma or "ativo" in ma):
        # Formato multi-canal anterior (intervalo/ativo globais) → migra pra por canal
        intervalo_global = int(ma.get("intervalo_min", 30))
        ativo_global = bool(ma.get("ativo", False))
        for c in ma["canais"]:
            c.setdefault("intervalo_min", intervalo_global)
            c.setdefault("ativo", ativo_global)
        ma.pop("intervalo_min", None)
        ma.pop("ativo", None)
        cfg["msg_auto"] = ma
        salvar_cfg(cfg)

    # Normaliza defaults dos canais
    canais = ma.get("canais", [])
    for c in canais:
        c.setdefault("intervalo_min", 30)
        c.setdefault("ativo", False)
        c.setdefault("ultima_msg_id", None)

    return {"canais": list(canais)}


def get_canal_cfg(canal_id: int) -> dict | None:
    for c in get_msg_auto()["canais"]:
        if c["canal_id"] == canal_id:
            return c
    return None


def add_msg_auto_canal(canal_id: int, mensagem: str):
    """Adiciona canal novo ou atualiza mensagem se já existe. Retorna True se é novo."""
    cfg = carregar_cfg()
    ma = cfg.get("msg_auto", {"canais": []})
    canais = ma.get("canais", [])

    for c in canais:
        if c.get("canal_id") == canal_id:
            c["mensagem"] = mensagem
            cfg["msg_auto"] = ma
            salvar_cfg(cfg)
            return False

    canais.append({
        "canal_id": canal_id,
        "mensagem": mensagem,
        "intervalo_min": 30,
        "ativo": False,
        "ultima_msg_id": None,
    })
    ma["canais"] = canais
    cfg["msg_auto"] = ma
    salvar_cfg(cfg)
    return True


def update_canal_cfg(canal_id: int, **kwargs):
    """Atualiza campos de um canal específico."""
    cfg = carregar_cfg()
    ma = cfg.get("msg_auto", {"canais": []})
    for c in ma.get("canais", []):
        if c.get("canal_id") == canal_id:
            c.update(kwargs)
            break
    cfg["msg_auto"] = ma
    salvar_cfg(cfg)


def remove_msg_auto_canal(canal_id: int) -> bool:
    cfg = carregar_cfg()
    ma = cfg.get("msg_auto", {"canais": []})
    canais = ma.get("canais", [])
    novo = [c for c in canais if c.get("canal_id") != canal_id]
    if len(novo) == len(canais):
        return False
    ma["canais"] = novo
    cfg["msg_auto"] = ma
    salvar_cfg(cfg)
    return True


def _cor(h):
    try: return int(h.lstrip("#"),16)
    except: return 0x2F3136

def _painel_emb(cfg, guild):
    c = _cor(cfg["cor_primaria"])
    em = discord.Embed(title=f"{SETTINGS}  SalasFF — Painel de Configuração", color=c)
    status = f"{ON} Ativo" if cfg["vendas_ativas"] else f"{OFF} Desativado"
    em.add_field(name=f"{CART} Vendas", value=status, inline=True)
    em.add_field(name=f"{MONEY} Preço/sala", value=f"R$ {cfg.get('preco_por_sala',0.09):.4f}", inline=True)
    cores = f"{DOT} Primária: `#{cfg['cor_primaria']}`\n{DOT} Sucesso: `#{cfg['cor_sucesso']}`\n{DOT} Erro: `#{cfg['cor_erro']}`"
    em.add_field(name=f"{STATS} Cores", value=cores, inline=False)
    log_ch = f"<#{cfg['canal_log_id']}>" if cfg.get("canal_log_id") else f"{OFF} Não definido"
    em.add_field(name=f"{INFO} Canais", value=f"{DOT} Log: {log_ch}", inline=False)
    # Logs públicos
    aval_ch = f"<#{cfg['canal_avaliacao_pub_id']}>" if cfg.get("canal_avaliacao_pub_id") else f"{OFF} Não definido"
    sug_ch = f"<#{cfg['canal_sugestao_pub_id']}>" if cfg.get("canal_sugestao_pub_id") else f"{OFF} Não definido"
    gratis_ch = f"<#{cfg['canal_gratis_log_id']}>" if cfg.get("canal_gratis_log_id") else f"{OFF} Não definido"
    compras_ch = f"<#{cfg['canal_compras_pub_id']}>" if cfg.get("canal_compras_pub_id") else f"{OFF} Não definido"
    bonus_ch = f"<#{cfg['canal_bonus_pub_id']}>" if cfg.get("canal_bonus_pub_id") else f"{OFF} Não definido"
    em.add_field(name=f"{STATS} Logs Públicos", value=f"{DOT} Compras: {compras_ch}\n{DOT} Bônus: {bonus_ch}\n{DOT} Avaliações: {aval_ch}\n{DOT} Sugestões: {sug_ch}\n{DOT} Log Grátis: {gratis_ch}", inline=False)
    # OAuth2
    oauth_secret = ON + " Configurado" if cfg.get("oauth2_client_secret") else f"{OFF} Não definido"
    oauth_redir = f"`{cfg.get('gratis_oauth2_redirect', '')[:35]}…`" if cfg.get("gratis_oauth2_redirect") else f"{OFF} Não definido"
    em.add_field(name=f"{URL} OAuth2", value=f"{DOT} Secret: {oauth_secret}\n{DOT} Redirect: {oauth_redir}", inline=False)
    # Salas Teste
    salas_teste = cfg.get("salas_gratis", 10)
    em.add_field(name=f"{PRESENTE} Salas Teste", value=f"{DOT} Quantidade: **{salas_teste} salas**", inline=False)
    # Cargos Cliente Globais (1 por server dono)
    cargos_global = cfg.get("cargos_cliente_global") or {}
    legacy = cfg.get("cargo_cliente_id")
    guild_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)
    cargo_lines = []
    for gid in guild_ids[:2]:
        cid = cargos_global.get(str(gid))
        if not cid and legacy and gid == guild_ids[0]:
            cid = legacy  # fallback retroativo
        gname = f"Server `{gid}`"
        cargo_txt = f"<@&{cid}>" if cid else f"{OFF} *Sem cargo*"
        cargo_lines.append(f"{DOT} **{gname}** → {cargo_txt}")
    em.add_field(
        name=f"{GIFT} Cargos Cliente Globais",
        value="\n".join(cargo_lines) if cargo_lines else f"{OFF} *Nenhum cargo*",
        inline=False,
    )
    banco = cfg.get("banco_pix", "efi")
    banco_nome = "Efí Bank" if banco == "efi" else "MisticPay"
    banco_emoji = ON if banco == "efi" else ON
    em.add_field(name=f"{PIX} Banco PIX", value=f"{banco_emoji} **{banco_nome}**", inline=True)
    cq = cfg.get("cargos_por_qtd",{})
    if cq:
        lines = [f"{DOT} **{q} salas** → <@&{cid}>" for q,cid in sorted(cq.items(), key=lambda x:int(x[0]))]
        em.add_field(name=f"{TOP} Cargos por Qtd", value="\n".join(lines), inline=False)
    else:
        em.add_field(name=f"{TOP} Cargos por Qtd", value=f"{OFF} Nenhum", inline=False)
    return em

# ═══════════════════════════════════════════
#  Modais
# ═══════════════════════════════════════════

class ModalCores(discord.ui.Modal, title="🎨 Configurar Cores"):
    primaria = discord.ui.TextInput(label="Primária (hex)", placeholder="5865F2", min_length=6, max_length=6)
    sucesso  = discord.ui.TextInput(label="Sucesso (hex)", placeholder="00FF7F", min_length=6, max_length=6)
    erro     = discord.ui.TextInput(label="Erro (hex)", placeholder="FF4444", min_length=6, max_length=6)
    def __init__(self, cfg):
        super().__init__()
        self.primaria.default=cfg["cor_primaria"]; self.sucesso.default=cfg["cor_sucesso"]; self.erro.default=cfg["cor_erro"]
    async def on_submit(self, i):
        cfg = carregar_cfg()
        cfg["cor_primaria"]=self.primaria.value.strip().lstrip("#").upper()
        cfg["cor_sucesso"]=self.sucesso.value.strip().lstrip("#").upper()
        cfg["cor_erro"]=self.erro.value.strip().lstrip("#").upper()
        salvar_cfg(cfg)
        em = _emb(f"{ON}  Cores Atualizadas!", _cor(cfg["cor_sucesso"]))
        em.add_field(name="Primária", value=f"`#{cfg['cor_primaria']}`", inline=True)
        em.add_field(name="Sucesso", value=f"`#{cfg['cor_sucesso']}`", inline=True)
        em.add_field(name="Erro", value=f"`#{cfg['cor_erro']}`", inline=True)
        await i.response.send_message(embed=em, ephemeral=True)

class CanalLogSelectView(discord.ui.View):
    def __init__(self): super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de Log...",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1,
    )
    async def sel(self, inter, s):
        cfg = carregar_cfg()
        cfg["canal_log_id"] = s.values[0].id
        salvar_cfg(cfg)
        config.LOG_CHANNEL_ID = cfg["canal_log_id"]
        em = _emb(f"{ON}  Canal de Log Atualizado!", config.COR_SUCESSO)
        em.description = f"{DOT} Log: <#{cfg['canal_log_id']}>"
        await inter.response.send_message(embed=em, ephemeral=True)

class LogsPubSelectView(discord.ui.View):
    def __init__(self): super().__init__(timeout=120)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="📦 Canal de Compras", channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=0)
    async def s_compras(self, inter, s):
        cfg = carregar_cfg()
        cfg["canal_compras_pub_id"] = s.values[0].id if s.values else None
        salvar_cfg(cfg)
        await inter.response.send_message(embed=_emb(f"{ON}  Compras → <#{s.values[0].id}>" if s.values else f"{OFF}  Compras removido", config.COR_SUCESSO), ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="🎁 Canal de Bônus", channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=1)
    async def s_bonus(self, inter, s):
        cfg = carregar_cfg()
        cfg["canal_bonus_pub_id"] = s.values[0].id if s.values else None
        salvar_cfg(cfg)
        await inter.response.send_message(embed=_emb(f"{ON}  Bônus → <#{s.values[0].id}>" if s.values else f"{OFF}  Bônus removido", config.COR_SUCESSO), ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="⭐ Canal de Avaliações", channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=2)
    async def s_aval(self, inter, s):
        cfg = carregar_cfg()
        cfg["canal_avaliacao_pub_id"] = s.values[0].id if s.values else None
        salvar_cfg(cfg)
        await inter.response.send_message(embed=_emb(f"{ON}  Avaliações → <#{s.values[0].id}>" if s.values else f"{OFF}  Avaliações removido", config.COR_SUCESSO), ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="💡 Canal de Sugestões", channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=3)
    async def s_sug(self, inter, s):
        cfg = carregar_cfg()
        cfg["canal_sugestao_pub_id"] = s.values[0].id if s.values else None
        salvar_cfg(cfg)
        await inter.response.send_message(embed=_emb(f"{ON}  Sugestões → <#{s.values[0].id}>" if s.values else f"{OFF}  Sugestões removido", config.COR_SUCESSO), ephemeral=True)

    @discord.ui.select(cls=discord.ui.ChannelSelect, placeholder="🎮 Canal Log Salas Grátis", channel_types=[discord.ChannelType.text], min_values=0, max_values=1, row=4)
    async def s_gratis(self, inter, s):
        cfg = carregar_cfg()
        cfg["canal_gratis_log_id"] = s.values[0].id if s.values else None
        salvar_cfg(cfg)
        await inter.response.send_message(embed=_emb(f"{ON}  Grátis → <#{s.values[0].id}>" if s.values else f"{OFF}  Grátis removido", config.COR_SUCESSO), ephemeral=True)

class ModalOAuth2(discord.ui.Modal, title="🔗 Configurar OAuth2"):
    secret = discord.ui.TextInput(label="Client Secret (Discord Dev Portal)", placeholder="Cole o Client Secret aqui", required=False, max_length=100)
    uri    = discord.ui.TextInput(label="Redirect URI", placeholder="https://fapps.discloud.app/oauth/callback", required=False, max_length=200)
    def __init__(self, cfg):
        super().__init__()
        self.secret.default = cfg.get("oauth2_client_secret", "")
        self.uri.default = cfg.get("gratis_oauth2_redirect", "")
    async def on_submit(self, i):
        cfg = carregar_cfg()
        if self.secret.value.strip():
            cfg["oauth2_client_secret"] = self.secret.value.strip()
        if self.uri.value.strip():
            cfg["gratis_oauth2_redirect"] = self.uri.value.strip()
        salvar_cfg(cfg)
        em = _emb(f"{ON}  OAuth2 Atualizado!", config.COR_SUCESSO)
        em.add_field(name="🔑 Secret", value="✅ Configurado" if cfg.get("oauth2_client_secret") else f"{OFF} Não definido", inline=True)
        redir = cfg.get("gratis_oauth2_redirect", "")
        em.add_field(name="🔗 URI", value=f"`{redir}`" if redir else f"{OFF} Não definido", inline=True)
        await i.response.send_message(embed=em, ephemeral=True)

class ModalSalasTeste(discord.ui.Modal, title="🎮 Salas Teste Grátis"):
    qtd = discord.ui.TextInput(label="Quantidade de salas teste", placeholder="5", min_length=1, max_length=5)
    def __init__(self, cfg):
        super().__init__()
        self.qtd.default = str(cfg.get("salas_gratis", 10))
    async def on_submit(self, i):
        cfg = carregar_cfg()
        try:
            valor = int(self.qtd.value.strip())
            if valor < 1 or valor > 99999:
                raise ValueError
        except ValueError:
            return await i.response.send_message(embed=_emb("❌ Valor inválido! Use um número de 1 a 99999.", config.COR_ERRO), ephemeral=True)
        cfg["salas_gratis"] = valor
        salvar_cfg(cfg)
        em = _emb(f"{ON}  Salas Teste Atualizado!", config.COR_SUCESSO)
        em.description = f"{PRESENTE} Cada novo usuário receberá **{valor} sala(s) teste** ao resgatar."
        await i.response.send_message(embed=em, ephemeral=True)

class ConvitesCanalSelectView(discord.ui.View):
    """View pra setar canal de aprovação de convites (global, no botconfig)."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Selecione o canal de aprovação...",
        min_values=1, max_values=1,
    )
    async def select_canal(self, inter: discord.Interaction, sel: discord.ui.ChannelSelect):
        from utils.database import convites_canal_aprovacao_set
        ch = sel.values[0]
        await asyncio.to_thread(convites_canal_aprovacao_set, int(ch.id))
        em = _emb(f"{ON}  Canal de aprovação definido!", config.COR_SUCESSO)
        em.description = f"{DOT} Pedidos de convites cairão em {ch.mention}"
        await inter.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="Limpar Canal", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_limpar(self, inter: discord.Interaction, btn):
        from utils.database import convites_canal_aprovacao_set
        await asyncio.to_thread(convites_canal_aprovacao_set, None)
        em = _emb(f"{ON}  Canal removido!", config.COR_SUCESSO)
        em.description = "Sistema de aprovação desativado."
        await inter.response.send_message(embed=em, ephemeral=True)


class CargoSaldoGlobalView(discord.ui.View):
    """Botões pra escolher qual server dono configurar cargo de saldo."""
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
        guild_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)

        for idx, gid in enumerate(guild_ids[:2], start=1):
            guild = bot.get_guild(int(gid))
            gname = guild.name if guild else f"Server {idx}"
            label = gname if len(gname) <= 30 else gname[:27] + "..."
            btn = discord.ui.Button(
                label=label, emoji="💰",
                style=discord.ButtonStyle.primary,
                custom_id=f"cargosaldo:{gid}",
            )
            btn.callback = self._make_callback(int(gid))
            self.add_item(btn)

        btn_limpar = discord.ui.Button(
            label="Limpar Todos", emoji="🗑️",
            style=discord.ButtonStyle.danger, row=1,
        )
        btn_limpar.callback = self._limpar_callback
        self.add_item(btn_limpar)

    def _make_callback(self, gid: int):
        async def _cb(inter: discord.Interaction):
            guild = self.bot.get_guild(int(gid))
            if not guild:
                em = _emb(f"❌ Bot não está nesse servidor (`{gid}`).", config.COR_ERRO)
                return await inter.response.send_message(embed=em, ephemeral=True)
            em = _emb(f"💰  Cargo Saldo — {guild.name}", config.COR_INFO)
            em.description = (
                f"Selecione o cargo do **{guild.name}** que será dado a quem tem saldo > 0.\n\n"
                f"{DOT} Ao salvar, o bot aplicará o cargo aos clientes com saldo agora (em background).\n"
                f"{DOT} Quando alguém zerar saldo, o cargo é removido automaticamente."
            )
            await inter.response.send_message(
                embed=em, view=CargoSaldoRoleSelectView(self.bot, gid), ephemeral=True
            )
        return _cb

    async def _limpar_callback(self, inter: discord.Interaction):
        cfg = carregar_cfg()
        cfg["cargos_cliente_saldo"] = {}
        salvar_cfg(cfg)
        em = _emb(f"{ON}  Cargos cliente saldo removidos.", config.COR_SUCESSO)
        await inter.response.send_message(embed=em, ephemeral=True)


class CargoSaldoRoleSelectView(discord.ui.View):
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = int(guild_id)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Selecione o cargo de saldo...",
        min_values=1, max_values=1,
    )
    async def select_role(self, inter: discord.Interaction, sel: discord.ui.RoleSelect):
        role = sel.values[0]
        if role.guild.id != self.guild_id:
            em = _emb("❌ Cargo não pertence ao servidor escolhido.", config.COR_ERRO)
            em.description = (
                f"Rode `/botconfig` dentro do servidor `{self.guild_id}` ou clique em **Digitar ID**."
            )
            return await inter.response.send_message(embed=em, ephemeral=True)

        await self._aplicar(inter, int(role.id))

    @discord.ui.button(label="Digitar ID", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_digitar(self, inter: discord.Interaction, btn):
        await inter.response.send_modal(CargoSaldoIDModal(self.guild_id))

    @discord.ui.button(label="Limpar este servidor", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_limpar(self, inter: discord.Interaction, btn):
        cfg = carregar_cfg()
        cs = cfg.get("cargos_cliente_saldo") or {}
        cs.pop(str(self.guild_id), None)
        cfg["cargos_cliente_saldo"] = cs
        salvar_cfg(cfg)
        guild = self.bot.get_guild(self.guild_id)
        em = _emb(f"{ON}  Cargo removido!", config.COR_SUCESSO)
        em.description = f"{DOT} **{guild.name if guild else self.guild_id}** sem cargo de saldo."
        await inter.response.send_message(embed=em, ephemeral=True)

    async def _aplicar(self, inter: discord.Interaction, cargo_id: int):
        cfg = carregar_cfg()
        cs = cfg.get("cargos_cliente_saldo") or {}
        cs[str(self.guild_id)] = int(cargo_id)
        cfg["cargos_cliente_saldo"] = cs
        salvar_cfg(cfg)

        guild = self.bot.get_guild(self.guild_id)
        em = _emb(f"{ON}  Cargo de saldo configurado!", config.COR_SUCESSO)
        em.description = (
            f"{DOT} **{guild.name if guild else self.guild_id}** → <@&{cargo_id}>\n\n"
            f"⏳ Aplicando o cargo aos clientes com saldo no momento... "
            f"(roda em background, pode demorar uns minutos)"
        )
        await inter.response.send_message(embed=em, ephemeral=True)

        # Sweep em background
        asyncio.create_task(_sweep_cargo_saldo(self.bot, self.guild_id, cargo_id))


class CargoSaldoIDModal(discord.ui.Modal, title="✏️ Digitar ID do Cargo Saldo"):
    cargo_id_input = discord.ui.TextInput(
        label="ID do Cargo",
        placeholder="Ex: 1234567890123456789",
        required=True, min_length=15, max_length=20,
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = int(guild_id)

    async def on_submit(self, inter: discord.Interaction):
        try:
            cid = int(self.cargo_id_input.value.strip())
        except ValueError:
            em = _emb("❌ ID inválido!", config.COR_ERRO)
            return await inter.response.send_message(embed=em, ephemeral=True)

        cfg = carregar_cfg()
        cs = cfg.get("cargos_cliente_saldo") or {}
        cs[str(self.guild_id)] = cid
        cfg["cargos_cliente_saldo"] = cs
        salvar_cfg(cfg)

        em = _emb(f"{ON}  Cargo de saldo configurado!", config.COR_SUCESSO)
        em.description = (
            f"{DOT} Servidor `{self.guild_id}` → <@&{cid}>\n\n"
            f"⏳ Aplicando aos clientes com saldo... (background)"
        )
        await inter.response.send_message(embed=em, ephemeral=True)
        asyncio.create_task(_sweep_cargo_saldo(inter.client, self.guild_id, cid))


async def _sweep_cargo_saldo(bot, guild_id: int, cargo_id: int):
    """Sincroniza cargo de saldo:
    - Adiciona em quem tem saldo > 0
    - Remove de quem tem o cargo mas não tem saldo
    """
    import logging as _lg
    _lcs = _lg.getLogger("salasff.cargosaldo")

    guild = bot.get_guild(int(guild_id))
    if not guild:
        _lcs.warning(f"[sweep] guild {guild_id} não encontrada")
        return
    cargo = guild.get_role(int(cargo_id))
    if not cargo:
        _lcs.warning(f"[sweep] cargo {cargo_id} não existe em {guild_id}")
        return
    if not guild.me.guild_permissions.manage_roles:
        _lcs.warning(f"[sweep] sem permissão Manage Roles em {guild_id}")
        return
    if guild.me.top_role <= cargo:
        _lcs.warning(f"[sweep] hierarquia: cargo bot abaixo de {cargo.name} em {guild_id}")
        return

    from utils.database import usuarios_com_saldo
    user_ids_com_saldo = set(await asyncio.to_thread(usuarios_com_saldo))
    _lcs.info(
        f"[sweep] guild={guild_id} cargo={cargo.name} "
        f"clientes_com_saldo={len(user_ids_com_saldo)}"
    )

    aplicados = 0
    removidos = 0
    erros = 0

    # 1) Adiciona em quem tem saldo
    for uid in user_ids_com_saldo:
        try:
            member = guild.get_member(int(uid))
            if not member:
                continue
            if cargo in member.roles:
                continue
            await member.add_roles(cargo, reason="Sweep: usuário com saldo > 0")
            aplicados += 1
            await asyncio.sleep(0.4)
        except discord.Forbidden:
            erros += 1
        except Exception as ex:
            _lcs.warning(f"[sweep+] erro user {uid}: {ex}")
            erros += 1

    # 2) Remove de quem tem o cargo mas NÃO tem saldo
    # Itera os membros que TÊM o cargo agora
    membros_com_cargo = list(cargo.members)
    _lcs.info(f"[sweep] revisão: {len(membros_com_cargo)} membros têm o cargo")

    for member in membros_com_cargo:
        try:
            if str(member.id) in user_ids_com_saldo:
                continue  # tem saldo — mantém
            await member.remove_roles(cargo, reason="Sweep: saldo zerado/sem keys")
            removidos += 1
            await asyncio.sleep(0.4)
        except discord.Forbidden:
            erros += 1
        except Exception as ex:
            _lcs.warning(f"[sweep-] erro user {member.id}: {ex}")
            erros += 1

    _lcs.info(
        f"[sweep] guild={guild_id} fim: "
        f"aplicados={aplicados} removidos={removidos} erros={erros}"
    )


async def aplicar_cargo_saldo(bot, user_id: int):
    """Dá o cargo de saldo nos servers donos onde o user é membro.
    Chamado quando o user RECEBE saldo (compra, restauração)."""
    import logging as _lg
    _lcs = _lg.getLogger("salasff.cargosaldo")
    cfg = carregar_cfg()
    cs = cfg.get("cargos_cliente_saldo") or {}
    if not cs:
        return
    for gid_str, cargo_id in cs.items():
        try:
            guild = bot.get_guild(int(gid_str))
            if not guild:
                continue
            member = guild.get_member(int(user_id))
            if not member:
                try:
                    member = await guild.fetch_member(int(user_id))
                except Exception:
                    continue
            cargo = guild.get_role(int(cargo_id))
            if not cargo or cargo in member.roles:
                continue
            if guild.me.top_role <= cargo:
                continue
            await member.add_roles(cargo, reason="Saldo > 0")
            _lcs.info(f"[saldo+] cargo dado a {user_id} em {gid_str}")
        except Exception as ex:
            _lcs.warning(f"[saldo+] erro {user_id} em {gid_str}: {ex}")


async def remover_cargo_saldo_se_zerou(bot, user_id: int):
    """Remove o cargo de saldo se o saldo do user chegou a 0.
    Chamado depois que ele consumir saldo."""
    import logging as _lg
    _lcs = _lg.getLogger("salasff.cargosaldo")
    cfg = carregar_cfg()
    cs = cfg.get("cargos_cliente_saldo") or {}
    if not cs:
        return
    from utils.database import saldo_total_usuario
    saldo = await asyncio.to_thread(saldo_total_usuario, str(user_id))
    if saldo > 0:
        return
    for gid_str, cargo_id in cs.items():
        try:
            guild = bot.get_guild(int(gid_str))
            if not guild:
                continue
            member = guild.get_member(int(user_id))
            if not member:
                continue
            cargo = guild.get_role(int(cargo_id))
            if not cargo or cargo not in member.roles:
                continue
            if guild.me.top_role <= cargo:
                continue
            await member.remove_roles(cargo, reason="Saldo zerou")
            _lcs.info(f"[saldo-] cargo removido de {user_id} em {gid_str}")
        except Exception as ex:
            _lcs.warning(f"[saldo-] erro {user_id} em {gid_str}: {ex}")


class CargoClienteGlobalView(discord.ui.View):
    """Botões pra escolher qual server dono configurar."""
    def __init__(self, bot):
        super().__init__(timeout=300)
        self.bot = bot
        guild_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)

        for idx, gid in enumerate(guild_ids[:2], start=1):
            guild = bot.get_guild(int(gid))
            gname = guild.name if guild else f"Server {idx}"
            label = gname if len(gname) <= 30 else gname[:27] + "..."
            btn = discord.ui.Button(
                label=label,
                emoji=PE.get("gift"),
                style=discord.ButtonStyle.primary,
                custom_id=f"cargoglobal:{gid}",
            )
            btn.callback = self._make_callback(int(gid))
            self.add_item(btn)

        # Botão limpar tudo
        btn_limpar = discord.ui.Button(
            label="Limpar Todos",
            emoji="🗑️",
            style=discord.ButtonStyle.danger,
            row=1,
        )
        btn_limpar.callback = self._limpar_callback
        self.add_item(btn_limpar)

    def _make_callback(self, gid: int):
        async def _cb(inter: discord.Interaction):
            guild = self.bot.get_guild(int(gid))
            if not guild:
                em = _emb(f"❌ Bot não está nesse servidor (`{gid}`).", config.COR_ERRO)
                return await inter.response.send_message(embed=em, ephemeral=True)
            em = _emb(f"{GIFT}  Cargo Cliente — {guild.name}", config.COR_INFO)
            em.description = (
                f"Selecione abaixo qual cargo do **{guild.name}** dar a quem comprar.\n\n"
                f"{DOT} Esse cargo será dado a todo comprador membro desse servidor.\n"
                f"{DOT} Para remover o cargo configurado, clique em **Limpar este servidor**."
            )
            await inter.response.send_message(
                embed=em, view=CargoClienteRoleSelectView(self.bot, gid), ephemeral=True
            )
        return _cb

    async def _limpar_callback(self, inter: discord.Interaction):
        cfg = carregar_cfg()
        cfg["cargos_cliente_global"] = {}
        cfg.pop("cargo_cliente_id", None)
        salvar_cfg(cfg)
        em = _emb(f"{ON}  Todos os cargos cliente globais foram removidos.", config.COR_SUCESSO)
        await inter.response.send_message(embed=em, ephemeral=True)


class CargoClienteRoleSelectView(discord.ui.View):
    """RoleSelect com cargos da guild escolhida."""
    def __init__(self, bot, guild_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.guild_id = int(guild_id)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Selecione o cargo cliente...",
        min_values=1, max_values=1,
    )
    async def select_role(self, inter: discord.Interaction, sel: discord.ui.RoleSelect):
        role = sel.values[0]
        # O RoleSelect só lista cargos da guild atual da interação.
        # Se o user rodou /botconfig em outra guild que não a self.guild_id, o cargo não vai bater.
        if role.guild.id != self.guild_id:
            em = _emb(
                "❌ Esse cargo não pertence ao servidor escolhido.",
                config.COR_ERRO,
            )
            em.description = (
                f"Você precisa rodar `/botconfig` **dentro** do servidor "
                f"`{self.guild_id}` pra configurar o cargo dele, ou digitar o ID manualmente "
                f"clicando em **Digitar ID**."
            )
            return await inter.response.send_message(embed=em, ephemeral=True)

        cfg = carregar_cfg()
        cargos_global = cfg.get("cargos_cliente_global") or {}
        cargos_global[str(self.guild_id)] = int(role.id)
        cfg["cargos_cliente_global"] = cargos_global
        # Limpa legacy
        cfg.pop("cargo_cliente_id", None)
        salvar_cfg(cfg)

        guild = self.bot.get_guild(self.guild_id)
        em = _emb(f"{ON}  Cargo cliente atualizado!", config.COR_SUCESSO)
        em.description = f"{DOT} **{guild.name if guild else self.guild_id}** → {role.mention}"
        await inter.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="Digitar ID", emoji="✏️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_digitar(self, inter: discord.Interaction, btn):
        await inter.response.send_modal(CargoClienteIDModal(self.guild_id))

    @discord.ui.button(label="Limpar este servidor", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_limpar(self, inter: discord.Interaction, btn):
        cfg = carregar_cfg()
        cargos_global = cfg.get("cargos_cliente_global") or {}
        cargos_global.pop(str(self.guild_id), None)
        cfg["cargos_cliente_global"] = cargos_global
        salvar_cfg(cfg)
        guild = self.bot.get_guild(self.guild_id)
        em = _emb(f"{ON}  Cargo removido!", config.COR_SUCESSO)
        em.description = f"{DOT} **{guild.name if guild else self.guild_id}** sem cargo cliente."
        await inter.response.send_message(embed=em, ephemeral=True)


class CargoClienteIDModal(discord.ui.Modal, title="✏️ Digitar ID do Cargo"):
    cargo_id_input = discord.ui.TextInput(
        label="ID do Cargo",
        placeholder="Ex: 1234567890123456789",
        required=True, min_length=15, max_length=20,
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = int(guild_id)

    async def on_submit(self, inter: discord.Interaction):
        try:
            cid = int(self.cargo_id_input.value.strip())
        except ValueError:
            em = _emb("❌ ID inválido!", config.COR_ERRO)
            return await inter.response.send_message(embed=em, ephemeral=True)

        cfg = carregar_cfg()
        cargos_global = cfg.get("cargos_cliente_global") or {}
        cargos_global[str(self.guild_id)] = cid
        cfg["cargos_cliente_global"] = cargos_global
        cfg.pop("cargo_cliente_id", None)
        salvar_cfg(cfg)

        em = _emb(f"{ON}  Cargo cliente atualizado!", config.COR_SUCESSO)
        em.description = f"{DOT} Servidor `{self.guild_id}` → <@&{cid}>"
        await inter.response.send_message(embed=em, ephemeral=True)


class ModalCargo(discord.ui.Modal, title="👤 Cargo de Cliente Global"):
    """Configura cargos cliente globais — 1 cargo pra cada server dono.
    Quem comprar (em qualquer server) recebe esses cargos automaticamente
    nos servers donde for membro."""
    cargo_id_1 = discord.ui.TextInput(
        label="Cargo no Server Dono 1",
        placeholder="ID do cargo (vazio = remover)",
        required=False, max_length=20,
    )
    cargo_id_2 = discord.ui.TextInput(
        label="Cargo no Server Dono 2",
        placeholder="ID do cargo (vazio = remover)",
        required=False, max_length=20,
    )

    def __init__(self, cfg):
        super().__init__()
        cargos_global = cfg.get("cargos_cliente_global") or {}

        # Compat retroativa: se ainda tem cargo_cliente_id antigo e nada na nova estrutura,
        # mostra o legacy no slot do server 1
        legacy = cfg.get("cargo_cliente_id")
        guild_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)
        gid1 = guild_ids[0] if len(guild_ids) >= 1 else None
        gid2 = guild_ids[1] if len(guild_ids) >= 2 else None

        v1 = cargos_global.get(str(gid1)) if gid1 else None
        v2 = cargos_global.get(str(gid2)) if gid2 else None
        if not v1 and legacy:
            v1 = legacy

        self.cargo_id_1.default = str(v1) if v1 else ""
        self.cargo_id_2.default = str(v2) if v2 else ""
        self._gid1 = gid1
        self._gid2 = gid2

    async def on_submit(self, i):
        cfg = carregar_cfg()
        guild_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)
        gid1 = guild_ids[0] if len(guild_ids) >= 1 else None
        gid2 = guild_ids[1] if len(guild_ids) >= 2 else None

        # Parse IDs
        def _parse(v):
            v = (v or "").strip()
            if not v:
                return None
            try:
                return int(v)
            except ValueError:
                return "INVALIDO"

        cid1 = _parse(self.cargo_id_1.value)
        cid2 = _parse(self.cargo_id_2.value)
        if cid1 == "INVALIDO" or cid2 == "INVALIDO":
            return await i.response.send_message(
                embed=_emb("❌ ID inválido!", config.COR_ERRO), ephemeral=True
            )

        cargos_global = cfg.get("cargos_cliente_global") or {}
        if gid1:
            if cid1: cargos_global[str(gid1)] = cid1
            else: cargos_global.pop(str(gid1), None)
        if gid2:
            if cid2: cargos_global[str(gid2)] = cid2
            else: cargos_global.pop(str(gid2), None)
        cfg["cargos_cliente_global"] = cargos_global

        # Limpa o legacy pra evitar inconsistência
        cfg.pop("cargo_cliente_id", None)

        salvar_cfg(cfg)

        em = _emb(f"{ON}  Cargos Cliente Atualizados!", config.COR_SUCESSO)
        linhas = []
        for gid, cid_v in [(gid1, cid1), (gid2, cid2)]:
            if not gid:
                continue
            guild = i.client.get_guild(int(gid))
            gname = guild.name if guild else f"Guild {gid}"
            if cid_v:
                linhas.append(f"{DOT} **{gname}** → <@&{cid_v}>")
            else:
                linhas.append(f"{DOT} **{gname}** → {OFF} *Sem cargo*")
        em.description = "\n".join(linhas) if linhas else "*Nenhum cargo definido.*"
        await i.response.send_message(embed=em, ephemeral=True)

class ModalCargoQtd(discord.ui.Modal, title="🏆 Cargo por Quantidade"):
    qtd = discord.ui.TextInput(label="Mínimo de salas", placeholder="50", min_length=1, max_length=10)
    cargo_id = discord.ui.TextInput(label="ID do Cargo (vazio = remover)", required=False, max_length=20)
    async def on_submit(self, i):
        cfg = carregar_cfg()
        try: q = int(self.qtd.value.strip())
        except: return await i.response.send_message(embed=_emb("❌ Quantidade inválida!", config.COR_ERRO), ephemeral=True)
        cargos = cfg.get("cargos_por_qtd",{})
        cid = self.cargo_id.value.strip()
        if cid:
            try: cargos[str(q)] = int(cid); acao = f"Cargo <@&{cid}> → **{q} salas**"
            except: return await i.response.send_message(embed=_emb("❌ ID inválido!", config.COR_ERRO), ephemeral=True)
        else:
            cargos.pop(str(q), None); acao = f"Cargo removido de **{q} salas**"
        cfg["cargos_por_qtd"] = cargos; salvar_cfg(cfg)
        em = _emb(f"{ON}  Cargo por Qtd Atualizado!", config.COR_SUCESSO); em.description = acao
        await i.response.send_message(embed=em, ephemeral=True)

class ModalPreco(discord.ui.Modal, title="💰 Preço por Sala"):
    preco = discord.ui.TextInput(label="Preço (R$)", placeholder="0.09", min_length=1, max_length=10)
    def __init__(self, cfg):
        super().__init__()
        self.preco.default = str(cfg.get("preco_por_sala",0.09))
    async def on_submit(self, i):
        try:
            v = float(self.preco.value.strip().replace(",","."))
            if v<=0: raise ValueError
        except: return await i.response.send_message(embed=_emb("❌ Preço inválido!", config.COR_ERRO), ephemeral=True)
        cfg = carregar_cfg(); cfg["preco_por_sala"] = round(v,4); salvar_cfg(cfg)
        em = _emb(f"{ON}  Preço Atualizado!", config.COR_SUCESSO)
        em.add_field(name=f"{MONEY} Preço/sala", value=f"**R$ {v:.4f}**", inline=True)
        em.add_field(name=f"{DOT} 100 salas", value=f"**R$ {v*100:.2f}**", inline=True)
        await i.response.send_message(embed=em, ephemeral=True)


class ModalBonus(discord.ui.Modal, title="🎁 Configurar Sistema de Bônus"):
    ratio = discord.ui.TextInput(
        label="Salas por ciclo (ex: 10 = a cada 10 compradas)",
        placeholder="10",
        min_length=1, max_length=5,
    )
    por_ciclo = discord.ui.TextInput(
        label="Bônus por ciclo (salas grátis)",
        placeholder="2",
        min_length=1, max_length=3,
    )
    def __init__(self, cfg):
        super().__init__()
        self.ratio.default    = str(cfg.get("bonus_ratio",     10))
        self.por_ciclo.default = str(cfg.get("bonus_por_ciclo", 2))

    async def on_submit(self, i):
        try:
            r  = int(self.ratio.value.strip())
            pc = int(self.por_ciclo.value.strip())
            if r < 1 or pc < 1: raise ValueError
        except ValueError:
            return await i.response.send_message(
                embed=_emb("❌ Valores inválidos! Use números inteiros positivos.", config.COR_ERRO),
                ephemeral=True,
            )
        cfg = carregar_cfg()
        cfg["bonus_ratio"]     = r
        cfg["bonus_por_ciclo"] = pc
        salvar_cfg(cfg)
        em = _emb(f"{ON}  Bônus Atualizado!", config.COR_SUCESSO)
        em.description = (
            f"{PRESENTE} A cada **{r} salas** compradas → **+{pc} sala(s) grátis**\n\n"
            f"{DOT} 10 salas → {(10//r)*pc} bônus\n"
            f"{DOT} 50 salas → {(50//r)*pc} bônus\n"
            f"{DOT} 100 salas → {(100//r)*pc} bônus"
        )
        await i.response.send_message(embed=em, ephemeral=True)



# ═══════════════════════════════════════════════════════════════════════
#  Conversão automática de :nome_emoji: → <:nome:id> / <a:nome:id>
#  Busca em todos os servidores onde o bot está. Ignora :nome: que já
#  estiverem dentro de uma sintaxe <...:...:...> válida.
# ═══════════════════════════════════════════════════════════════════════
import re as _re

_EMOJI_TAG_RE = _re.compile(r":([A-Za-z0-9_]{2,32}):")

def _converter_emojis_custom(texto: str, bot) -> tuple[str, list[str], list[str]]:
    """Substitui ocorrências de :nome: no texto pelo <:nome:id> correspondente,
    buscando em todos os emojis dos servidores onde o bot está.

    Retorna (texto_convertido, lista_convertidos, lista_nao_encontrados).
    - Preserva :nome: que já estão dentro de <...:nome:id> (Discord já formatou).
    - Case-insensitive no match (Discord normaliza, mas usa nome original do server).
    - Se o bot não estiver disponível, retorna o texto original sem alterar.
    """
    if not texto or not bot:
        return texto, [], []

    # Mapa nome→emoji (case-insensitive). Se houver duplicatas, fica a primeira.
    mapa = {}
    try:
        for emj in bot.emojis:
            chave = emj.name.lower()
            if chave not in mapa:
                mapa[chave] = emj
    except Exception:
        return texto, [], []

    # Protege blocos já formatados <:nome:id> e <a:nome:id> com placeholders.
    placeholders = {}
    def _proteger(m):
        token = f"\x00PROT{len(placeholders)}\x00"
        placeholders[token] = m.group(0)
        return token
    texto_p = _re.sub(r"<a?:[A-Za-z0-9_]+:\d+>", _proteger, texto)

    convertidos, nao_encontrados = [], []

    def _sub(m):
        nome = m.group(1)
        emj = mapa.get(nome.lower())
        if emj is None:
            nao_encontrados.append(nome)
            return m.group(0)  # mantém :nome: original
        convertidos.append(nome)
        prefixo = "a" if emj.animated else ""
        return f"<{prefixo}:{emj.name}:{emj.id}>"

    texto_p = _EMOJI_TAG_RE.sub(_sub, texto_p)

    # Restaura os blocos protegidos
    for token, original in placeholders.items():
        texto_p = texto_p.replace(token, original)

    # Deduplica preservando ordem
    convertidos = list(dict.fromkeys(convertidos))
    nao_encontrados = list(dict.fromkeys(nao_encontrados))
    return texto_p, convertidos, nao_encontrados


class ModalTituloPanel(discord.ui.Modal, title="✏️ Alterar Título do Painel"):
    titulo = discord.ui.TextInput(label="Título", placeholder="F Applications - Compre Aqui", min_length=1, max_length=60)
    def __init__(self, cfg):
        super().__init__()
        self.titulo.default = cfg.get("titulo_painel_compra", "F Applications - Compre Aqui")
    async def on_submit(self, i):
        novo, _conv, _nf = _converter_emojis_custom(self.titulo.value.strip(), i.client)
        cfg = carregar_cfg(); cfg["titulo_painel_compra"] = novo; salvar_cfg(cfg)
        em = _emb(f"{ON}  Título Atualizado!", config.COR_SUCESSO)
        em.add_field(name=f"{INFO} Novo título", value=f"`{novo}`", inline=False)
        if _nf:
            em.add_field(
                name=f"{OFF} Emojis não encontrados",
                value=", ".join(f"`:{n}:`" for n in _nf[:10]),
                inline=False,
            )
        await i.response.send_message(embed=em, ephemeral=True)


class ModalSubtituloPanel(discord.ui.Modal, title="✏️ Editar Embed Secundária"):
    titulo_sec = discord.ui.TextInput(
        label="Título (topo da embed secundária)",
        placeholder="Ex: 💡 Como Funciona",
        min_length=0, max_length=80, required=False,
    )
    descricao_sec = discord.ui.TextInput(
        label="Descrição (corpo da embed secundária)",
        style=discord.TextStyle.paragraph,
        placeholder="Ex: Pague via PIX e receba suas salas na hora...",
        min_length=0, max_length=1500, required=False,
    )
    rodape_sec = discord.ui.TextInput(
        label="Rodapé (opcional)",
        placeholder="Ex: F Applications • Atendimento 24h",
        min_length=0, max_length=100, required=False,
    )

    def __init__(self, cfg):
        super().__init__()
        self.titulo_sec.default    = cfg.get("subtitulo_painel_compra", "")
        self.descricao_sec.default = cfg.get("subdesc_painel_compra", "")
        self.rodape_sec.default    = cfg.get("subrodape_painel_compra", "")

    async def on_submit(self, i):
        # Converte :nome: → <:nome:id> nos 3 campos, usando emojis de todos
        # os servidores onde o bot está. Ignora os que já estão formatados.
        bot = i.client
        titulo_conv, c1, nf1 = _converter_emojis_custom(self.titulo_sec.value.strip(), bot)
        desc_conv,   c2, nf2 = _converter_emojis_custom(self.descricao_sec.value.strip(), bot)
        rodape_conv, c3, nf3 = _converter_emojis_custom(self.rodape_sec.value.strip(), bot)

        cfg = carregar_cfg()
        cfg["subtitulo_painel_compra"] = titulo_conv
        cfg["subdesc_painel_compra"]   = desc_conv
        cfg["subrodape_painel_compra"] = rodape_conv
        salvar_cfg(cfg)

        em = _emb(f"{ON}  Embed Secundária Atualizada!", config.COR_SUCESSO)
        partes = []
        if cfg["subtitulo_painel_compra"]: partes.append(f"**Título:** {cfg['subtitulo_painel_compra']}")
        if cfg["subdesc_painel_compra"]:   partes.append(f"**Descrição:** {cfg['subdesc_painel_compra'][:200]}")
        if cfg["subrodape_painel_compra"]: partes.append(f"**Rodapé:** {cfg['subrodape_painel_compra']}")
        em.description = "\n\n".join(partes) if partes else "_Embed secundária vazia — nada será postado abaixo._"

        # Relatório dos emojis processados
        convertidos = list(dict.fromkeys(c1 + c2 + c3))
        nao_encontrados = list(dict.fromkeys(nf1 + nf2 + nf3))
        if convertidos:
            em.add_field(
                name=f"{ON} Emojis convertidos",
                value=", ".join(f"`:{n}:`" for n in convertidos[:15]),
                inline=False,
            )
        if nao_encontrados:
            em.add_field(
                name=f"{OFF} Não encontrados",
                value=(
                    ", ".join(f"`:{n}:`" for n in nao_encontrados[:15])
                    + "\n-# Esses emojis não existem em nenhum servidor onde o bot está."
                ),
                inline=False,
            )
        await i.response.send_message(embed=em, ephemeral=True)

# ═══════════════════════════════════════════
#  Views
# ═══════════════════════════════════════════

class BotConfigView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="Ativar/Desativar Vendas", emoji=PE["on"], style=discord.ButtonStyle.success, row=0, custom_id="cfg:vendas")
    async def tv(self, i, b):
        cfg = carregar_cfg(); cfg["vendas_ativas"] = not cfg["vendas_ativas"]; salvar_cfg(cfg)
        st = "ativadas" if cfg["vendas_ativas"] else "desativadas"
        em = _emb(f"{ON if cfg['vendas_ativas'] else OFF}  Vendas {st}!", config.COR_SUCESSO if cfg["vendas_ativas"] else config.COR_ERRO)
        await i.response.send_message(embed=em, ephemeral=True)
        await i.message.edit(embed=_painel_emb(cfg, i.guild), view=self)

    @discord.ui.button(label="Preço", emoji=PE["emoji70"], style=discord.ButtonStyle.primary, row=0, custom_id="cfg:preco")
    async def cp(self, i, b): await i.response.send_modal(ModalPreco(carregar_cfg()))

    @discord.ui.button(label="Cores", emoji=PE["stats"], style=discord.ButtonStyle.primary, row=0, custom_id="cfg:cores")
    async def cc(self, i, b): await i.response.send_modal(ModalCores(carregar_cfg()))

    @discord.ui.button(label="Canal de Log", emoji=PE["info"], style=discord.ButtonStyle.secondary, row=1, custom_id="cfg:canais")
    async def cn(self, i, b):
        cfg = carregar_cfg()
        log_ch = f"<#{cfg['canal_log_id']}>" if cfg.get("canal_log_id") else f"{OFF} Não definido"
        em = _emb(f"{INFO}  Canal de Log", config.COR_INFO)
        em.description = f"Atual: {log_ch}\n\nSelecione o canal abaixo:"
        await i.response.send_message(embed=em, view=CanalLogSelectView(), ephemeral=True)

    @discord.ui.button(label="Aprovação Convites", emoji="🎉", style=discord.ButtonStyle.secondary, row=2, custom_id="cfg:conv_canal")
    async def conv(self, i, b):
        from utils.database import convites_canal_aprovacao_get
        ch_id = await asyncio.to_thread(convites_canal_aprovacao_get)
        ch = i.client.get_channel(int(ch_id)) if ch_id else None
        atual = f"<#{ch_id}>" if ch else f"{OFF} *Não definido*"

        em = _emb("🎉  Canal de Aprovação de Convites", config.COR_INFO)
        em.description = (
            f"Canal onde caem os pedidos de aprovação dos convites do `/painelglobal`.\n\n"
            f"{DOT} **Atual:** {atual}\n\n"
            f"{INFO}  Selecione um canal abaixo ou clique em **Limpar**."
        )
        await i.response.send_message(embed=em, view=ConvitesCanalSelectView(), ephemeral=True)

    @discord.ui.button(label="Cargo Cliente Saldo", emoji="💰", style=discord.ButtonStyle.secondary, row=1, custom_id="cfg:cargo_saldo")
    async def cgs(self, i, b):
        cfg = carregar_cfg()
        cargos_saldo = cfg.get("cargos_cliente_saldo") or {}
        guild_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)

        em = _emb("💰  Cargo Cliente Saldo", config.COR_INFO)
        em.description = (
            "Define **1 cargo por servidor dono** que será dado **automaticamente "
            "a todos os usuários com saldo (salas restantes) > 0**.\n\n"
            f"{DOT} Quando alguém comprar/receber saldo → ganha o cargo\n"
            f"{DOT} Quando o saldo zerar (gastar todas as salas) → perde o cargo\n\n"
            "Selecione o servidor abaixo para definir/alterar o cargo dele."
        )
        for idx, gid in enumerate(guild_ids[:2], start=1):
            cid = cargos_saldo.get(str(gid))
            guild = i.client.get_guild(int(gid))
            gname = guild.name if guild else f"Server `{gid}`"
            cargo_txt = f"<@&{cid}>" if cid else f"{OFF} *Sem cargo*"
            em.add_field(name=f"{DOT} {gname}", value=cargo_txt, inline=False)

        await i.response.send_message(
            embed=em, view=CargoSaldoGlobalView(i.client), ephemeral=True
        )

    @discord.ui.button(label="Cargo Cliente", emoji=PE["gift"], style=discord.ButtonStyle.secondary, row=1, custom_id="cfg:cargo")
    async def cg(self, i, b):
        cfg = carregar_cfg()
        cargos_global = cfg.get("cargos_cliente_global") or {}
        legacy = cfg.get("cargo_cliente_id")
        guild_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)

        em = _emb(f"{GIFT}  Cargos Cliente Globais", config.COR_INFO)
        em.description = (
            "Define **1 cargo por servidor dono**. Quem comprar (em qualquer lugar) "
            "recebe esses cargos automaticamente nos servidores onde for membro.\n\n"
            "Selecione o servidor abaixo para definir/alterar o cargo dele."
        )
        for idx, gid in enumerate(guild_ids[:2], start=1):
            cid = cargos_global.get(str(gid))
            if not cid and legacy and idx == 1:
                cid = legacy
            guild = i.client.get_guild(int(gid))
            gname = guild.name if guild else f"Server `{gid}`"
            cargo_txt = f"<@&{cid}>" if cid else f"{OFF} *Sem cargo*"
            em.add_field(name=f"{DOT} {gname}", value=cargo_txt, inline=False)

        await i.response.send_message(
            embed=em, view=CargoClienteGlobalView(i.client), ephemeral=True
        )

    @discord.ui.button(label="Salas Teste", emoji=PE["presente"], style=discord.ButtonStyle.secondary, row=1, custom_id="cfg:salas_teste")
    async def st(self, i, b): await i.response.send_modal(ModalSalasTeste(carregar_cfg()))

    @discord.ui.button(label="Bônus", emoji=PE["presente"], style=discord.ButtonStyle.primary, row=2, custom_id="cfg:bonus")
    async def cb(self, i, b): await i.response.send_modal(ModalBonus(carregar_cfg()))

    @discord.ui.button(label="Cargos por Qtd", emoji=PE["top"], style=discord.ButtonStyle.primary, row=2, custom_id="cfg:cargos_qtd")
    async def cq(self, i, b):
        em = _emb("🏆  Cargos por Quantidade — escolha o servidor", config.COR_INFO)
        em.description = "Depois de escolher, você pode adicionar/remover cargos por quantidade de salas compradas naquele servidor."
        await i.response.send_message(embed=em, view=EscolherGuildCargosView(i.client), ephemeral=True)

    @discord.ui.button(label="Banco PIX", emoji=PE["pix"], style=discord.ButtonStyle.primary, row=2, custom_id="cfg:banco_pix")
    async def bp(self, i, b):
        cfg = carregar_cfg()
        banco = cfg.get("banco_pix", "efi")
        banco_nome = "Efí Bank" if banco == "efi" else "MisticPay"
        em = _emb(f"{PIX}  Banco PIX Ativo", config.COR_INFO)
        em.description = (
            f"{DOT} Banco atual: **{banco_nome}**\n\n"
            f"Selecione abaixo qual banco usar para cobranças PIX:"
        )
        await i.response.send_message(embed=em, view=BancoPixSelectView(), ephemeral=True)

    @discord.ui.button(label="Logs Públicos", emoji=PE["stats"], style=discord.ButtonStyle.primary, row=3, custom_id="cfg:logs_pub")
    async def lp(self, i, b):
        cfg = carregar_cfg()
        em = _emb(f"{STATS}  Logs Públicos", config.COR_INFO)
        em.description = "Selecione o canal de cada log abaixo:"
        await i.response.send_message(embed=em, view=LogsPubSelectView(), ephemeral=True)

    @discord.ui.button(label="OAuth2", emoji=PE["url"], style=discord.ButtonStyle.danger, row=3, custom_id="cfg:oauth2")
    async def oa(self, i, b): await i.response.send_modal(ModalOAuth2(carregar_cfg()))

    @discord.ui.button(label="Ranking Semanal", emoji="🏆", style=discord.ButtonStyle.primary, row=3, custom_id="cfg:ranking")
    async def rk(self, i, b):
        from cogs.ranking import abrir_config_ranking
        await abrir_config_ranking(i)


    @discord.ui.button(label="Msg Automática", emoji="📨", style=discord.ButtonStyle.primary, row=4, custom_id="cfg:msg_auto")
    async def ma(self, i, b):
        em = discord.Embed(title="📨  Mensagens Automáticas", color=config.COR_INFO)
        em.description = (
            "Cada canal tem sua própria mensagem, tempo e liga/desliga.\n\n"
            "▸ **Adicionar Canal** — adiciona um canal novo (pode ser de outro servidor).\n"
            "▸ **Configurar Canal** — escolha um canal e defina mensagem, tempo e ligar.\n"
            "▸ **Status Geral** — visão geral de todos os canais.\n\n"
            "Cada canal roda no seu próprio ritmo, independente dos outros."
        )
        await i.response.send_message(embed=em, view=MsgAutoView(), ephemeral=True)

    @discord.ui.button(label="Atualizar", emoji=PE["refresh"], style=discord.ButtonStyle.secondary, row=4, custom_id="cfg:refresh")
    async def rf(self, i, b): await i.response.edit_message(embed=_painel_emb(carregar_cfg(), i.guild), view=self)


class ModalAddCanal(discord.ui.Modal, title="📨 Adicionar Canal"):
    """Modal pra adicionar canal novo (com mensagem inicial)."""
    def __init__(self):
        super().__init__()
        self.canal = discord.ui.TextInput(
            label="ID do Canal",
            placeholder="Ex: 1234567890123456789 (pode ser de outro servidor)",
            required=True,
            max_length=20,
        )
        self.mensagem = discord.ui.TextInput(
            label="Conteúdo da Mensagem",
            style=discord.TextStyle.paragraph,
            placeholder="Texto que será enviado no loop...",
            required=True,
            max_length=2000,
        )
        self.add_item(self.canal)
        self.add_item(self.mensagem)

    async def on_submit(self, inter: discord.Interaction):
        try:
            canal_id = int(self.canal.value.strip())
        except Exception:
            return await inter.response.send_message(
                embed=_emb("❌ ID do canal inválido.", config.COR_ERRO), ephemeral=True
            )

        canal = inter.client.get_channel(canal_id)
        if not canal:
            return await inter.response.send_message(
                embed=_emb("❌ Canal não encontrado. Confira se o bot está no servidor dele.", config.COR_ERRO),
                ephemeral=True,
            )

        novo = add_msg_auto_canal(canal_id, self.mensagem.value)
        preview = self.mensagem.value[:150] + ("..." if len(self.mensagem.value) > 150 else "")

        em = discord.Embed(
            title=f"{ON}  Canal {'adicionado' if novo else 'atualizado'}!",
            color=config.COR_SUCESSO
        )
        nome_guild = canal.guild.name if hasattr(canal, "guild") and canal.guild else "?"
        em.description = (
            f"> **Canal:** {canal.mention} *(em {nome_guild})*\n"
            f"> **Preview:**\n```{preview}```\n\n"
            f"Use **Configurar Canal** no painel para definir tempo e ligar."
        )
        await inter.response.send_message(embed=em, ephemeral=True)


class ModalEditarMensagem(discord.ui.Modal, title="✏️ Editar Mensagem"):
    """Modal pra editar só a mensagem de um canal já configurado."""
    def __init__(self, canal_id: int, mensagem_atual: str):
        super().__init__()
        self.canal_id = canal_id
        self.mensagem = discord.ui.TextInput(
            label="Conteúdo da Mensagem",
            style=discord.TextStyle.paragraph,
            default=mensagem_atual,
            required=True,
            max_length=2000,
        )
        self.add_item(self.mensagem)

    async def on_submit(self, inter: discord.Interaction):
        update_canal_cfg(self.canal_id, mensagem=self.mensagem.value)
        preview = self.mensagem.value[:150] + ("..." if len(self.mensagem.value) > 150 else "")
        em = discord.Embed(title=f"{ON}  Mensagem atualizada!", color=config.COR_SUCESSO)
        em.description = f"> **Preview:**\n```{preview}```"
        await inter.response.send_message(embed=em, ephemeral=True)


class _EscolherCanalSelect(discord.ui.Select):
    """Select pra escolher qual canal configurar."""
    def __init__(self, canais: list, client):
        opcoes = []
        for c in canais[:25]:
            canal = client.get_channel(c["canal_id"])
            nome = f"#{canal.name}" if canal else f"Canal {c['canal_id']}"
            guild_nome = (canal.guild.name if canal and hasattr(canal, "guild") else "?")
            status = "🟢" if c.get("ativo") else "🔴"
            label = f"{status} {nome}"[:80]
            desc = f"em {guild_nome} • {c.get('intervalo_min', 30)} min"[:90]
            opcoes.append(discord.SelectOption(label=label, value=str(c["canal_id"]), description=desc))

        if not opcoes:
            opcoes.append(discord.SelectOption(label="— nenhum canal —", value="0"))

        super().__init__(placeholder="Escolha o canal para configurar...", min_values=1, max_values=1, options=opcoes)

    async def callback(self, inter: discord.Interaction):
        canal_id = int(self.values[0])
        if canal_id == 0:
            return await inter.response.edit_message(
                embed=_emb("Nenhum canal configurado.", config.COR_AVISO), view=None
            )

        c = get_canal_cfg(canal_id)
        if not c:
            return await inter.response.edit_message(
                embed=_emb("❌ Canal não encontrado na config.", config.COR_ERRO), view=None
            )

        em = _canal_painel_emb(c, inter.client)
        await inter.response.edit_message(embed=em, view=CanalPainelView(canal_id))


class _EscolherCanalView(discord.ui.View):
    def __init__(self, canais: list, client):
        super().__init__(timeout=180)
        self.add_item(_EscolherCanalSelect(canais, client))


def _canal_painel_emb(c: dict, client) -> discord.Embed:
    """Embed do sub-painel de um canal específico."""
    canal = client.get_channel(c["canal_id"])
    nome = canal.mention if canal else f"`{c['canal_id']}`"
    guild_nome = canal.guild.name if canal and hasattr(canal, "guild") and canal.guild else "?"
    status = f"{ON} **LIGADO**" if c.get("ativo") else f"{OFF} **DESLIGADO**"
    tipo = c.get("tipo", "texto")
    tipo_txt = "🎯 **Promo V2** (visual estilizado)" if tipo == "promo" else "📝 **Texto simples**"
    mencionar = "✅ Sim" if c.get("mencionar_everyone", True) else "❌ Não"
    preview = (c.get("mensagem") or "")
    preview = (preview[:200] + "...") if len(preview) > 200 else preview

    em = discord.Embed(title=f"📨  Configurar Canal", color=config.COR_INFO)
    em.description = (
        f"> **Canal:** {nome} *(em {guild_nome})*\n"
        f"> **Status:** {status}\n"
        f"> **Intervalo:** **{c.get('intervalo_min', 30)} min**\n"
        f"> **Tipo:** {tipo_txt}\n"
        f"> **@everyone:** {mencionar}\n\n"
        f"**Mensagem/Texto do corpo:**\n```{preview or '(vazia)'}```"
    )
    return em


class CanalPainelView(discord.ui.View):
    """Sub-painel pra configurar UM canal específico."""
    def __init__(self, canal_id: int):
        super().__init__(timeout=300)
        self.canal_id = canal_id

    @discord.ui.button(label="Editar Mensagem", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit_msg(self, i, b):
        c = get_canal_cfg(self.canal_id)
        if not c:
            return await i.response.send_message(embed=_emb("❌ Canal removido.", config.COR_ERRO), ephemeral=True)
        await i.response.send_modal(ModalEditarMensagem(self.canal_id, c.get("mensagem", "")))

    @discord.ui.button(label="15 min", style=discord.ButtonStyle.secondary, row=1)
    async def t15(self, i, b):
        await self._set_tempo(i, 15)

    @discord.ui.button(label="30 min", style=discord.ButtonStyle.secondary, row=1)
    async def t30(self, i, b):
        await self._set_tempo(i, 30)

    @discord.ui.button(label="60 min", style=discord.ButtonStyle.secondary, row=1)
    async def t60(self, i, b):
        await self._set_tempo(i, 60)

    async def _set_tempo(self, i: discord.Interaction, minutos: int):
        update_canal_cfg(self.canal_id, intervalo_min=minutos)
        # Reinicia o loop desse canal (se estava ativo)
        c = get_canal_cfg(self.canal_id)
        cog = i.client.get_cog("BotConfigCog")
        if cog and c and c.get("ativo"):
            cog._restart_canal(self.canal_id)

        em = _canal_painel_emb(c, i.client) if c else _emb("Canal removido.", config.COR_ERRO)
        await i.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="Ligar/Desligar", emoji="🔁", style=discord.ButtonStyle.success, row=2)
    async def toggle(self, i, b):
        c = get_canal_cfg(self.canal_id)
        if not c:
            return await i.response.send_message(embed=_emb("❌ Canal removido.", config.COR_ERRO), ephemeral=True)
        if not c.get("mensagem"):
            return await i.response.send_message(
                embed=_emb("❌ Configure a mensagem antes de ligar.", config.COR_ERRO), ephemeral=True
            )

        novo = not c.get("ativo", False)
        update_canal_cfg(self.canal_id, ativo=novo)

        cog = i.client.get_cog("BotConfigCog")
        if cog:
            if novo:
                cog._start_canal(self.canal_id)
            else:
                cog._stop_canal(self.canal_id)

        c = get_canal_cfg(self.canal_id)
        em = _canal_painel_emb(c, i.client)
        await i.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="Promo V2 / Texto", emoji="🎯", style=discord.ButtonStyle.primary, row=2)
    async def toggle_tipo(self, i, b):
        c = get_canal_cfg(self.canal_id)
        if not c:
            return await i.response.send_message(embed=_emb("❌ Canal removido.", config.COR_ERRO), ephemeral=True)
        novo_tipo = "texto" if c.get("tipo") == "promo" else "promo"
        update_canal_cfg(self.canal_id, tipo=novo_tipo)
        c = get_canal_cfg(self.canal_id)
        em = _canal_painel_emb(c, i.client)
        await i.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="@everyone On/Off", emoji="📢", style=discord.ButtonStyle.secondary, row=2)
    async def toggle_everyone(self, i, b):
        c = get_canal_cfg(self.canal_id)
        if not c:
            return await i.response.send_message(embed=_emb("❌ Canal removido.", config.COR_ERRO), ephemeral=True)
        novo = not c.get("mencionar_everyone", True)
        update_canal_cfg(self.canal_id, mencionar_everyone=novo)
        c = get_canal_cfg(self.canal_id)
        em = _canal_painel_emb(c, i.client)
        await i.response.edit_message(embed=em, view=self)

    @discord.ui.button(label="Remover Canal", emoji="🗑️", style=discord.ButtonStyle.danger, row=3)
    async def rem(self, i, b):
        cog = i.client.get_cog("BotConfigCog")
        if cog:
            cog._stop_canal(self.canal_id)
        remove_msg_auto_canal(self.canal_id)
        em = discord.Embed(title=f"{ON}  Canal removido!", color=config.COR_SUCESSO)
        em.description = "> Não receberá mais mensagens automáticas."
        await i.response.edit_message(em, view=None)


class MsgAutoView(discord.ui.View):
    """Painel principal — Adicionar Canal / Configurar Canal / Status."""
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Adicionar Canal", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add(self, i, b):
        await i.response.send_modal(ModalAddCanal())

    @discord.ui.button(label="Configurar Canal", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0)
    async def cfg(self, i, b):
        ma = get_msg_auto()
        if not ma["canais"]:
            return await i.response.send_message(
                embed=_emb("Nenhum canal configurado. Use **Adicionar Canal** primeiro.", config.COR_AVISO),
                ephemeral=True
            )
        await i.response.send_message(
            embed=_emb("⚙️  Escolha o canal para configurar:", config.COR_INFO),
            view=_EscolherCanalView(ma["canais"], i.client),
            ephemeral=True,
        )

    @discord.ui.button(label="Status Geral", emoji="📊", style=discord.ButtonStyle.secondary, row=1)
    async def status(self, i, b):
        ma = get_msg_auto()
        em = discord.Embed(title="📨  Mensagens Automáticas — Status Geral", color=config.COR_INFO)
        em.add_field(name="Total de canais", value=f"**{len(ma['canais'])}**", inline=True)
        ativos = sum(1 for c in ma["canais"] if c.get("ativo"))
        em.add_field(name="Canais ligados", value=f"**{ativos}**", inline=True)

        if ma["canais"]:
            linhas = []
            for idx, c in enumerate(ma["canais"][:15], 1):
                canal = i.client.get_channel(c["canal_id"])
                if canal:
                    guild_nome = canal.guild.name if hasattr(canal, "guild") and canal.guild else "?"
                    status = "🟢" if c.get("ativo") else "🔴"
                    linhas.append(f"`{idx}.` {status} {canal.mention} *({guild_nome})* • {c.get('intervalo_min', 30)} min")
                else:
                    linhas.append(f"`{idx}.` ❌ Canal `{c['canal_id']}` não encontrado")
            if len(ma["canais"]) > 15:
                linhas.append(f"\n*... e mais {len(ma['canais']) - 15} canais*")
            em.add_field(name="Canais", value="\n".join(linhas), inline=False)
        else:
            em.add_field(name="Canais", value="*Nenhum canal — use **Adicionar Canal***", inline=False)

        await i.response.send_message(embed=em, ephemeral=True)


        await i.response.send_message(embed=em, ephemeral=True)


# ═══════════════════════════════════════════
#  Cargos por Servidor — configuração por guild
# ═══════════════════════════════════════════
class ModalCargoClienteGuild(discord.ui.Modal, title="👤 Cargo Cliente (servidor)"):
    cargo_id = discord.ui.TextInput(
        label="ID do Cargo (vazio = remover)",
        placeholder="Ex: 1234567890",
        required=False,
        max_length=20,
    )

    def __init__(self, guild_id: int, atual):
        super().__init__()
        self.guild_id = guild_id
        self.cargo_id.default = str(atual) if atual else ""

    async def on_submit(self, i: discord.Interaction):
        from utils.database import guild_set_cargo_cliente
        raw = self.cargo_id.value.strip()
        if raw:
            try:
                cid = int(raw)
            except ValueError:
                return await i.response.send_message(
                    embed=_emb("❌ ID inválido!", config.COR_ERRO), ephemeral=True
                )
            guild_set_cargo_cliente(str(self.guild_id), cid)
            desc = f"Cargo definido: <@&{cid}>"
        else:
            guild_set_cargo_cliente(str(self.guild_id), None)
            desc = "Cargo removido."
        em = _emb(f"{ON}  Cargo Cliente atualizado!", config.COR_SUCESSO)
        em.description = f"*Servidor:* `{self.guild_id}`\n{desc}"
        await i.response.send_message(embed=em, ephemeral=True)


class ModalCargoQtdGuild(discord.ui.Modal, title="🏆 Cargo por Qtd (servidor)"):
    qtd = discord.ui.TextInput(label="Mínimo de salas", placeholder="50", min_length=1, max_length=10)
    cargo_id = discord.ui.TextInput(
        label="ID do Cargo (vazio = remover)",
        required=False,
        max_length=20,
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, i: discord.Interaction):
        from utils.database import guild_get_cargos_por_qtd, guild_set_cargos_por_qtd
        try:
            q = int(self.qtd.value.strip())
        except:
            return await i.response.send_message(
                embed=_emb("❌ Quantidade inválida!", config.COR_ERRO), ephemeral=True
            )
        cargos = dict(guild_get_cargos_por_qtd(str(self.guild_id)))
        cid_raw = self.cargo_id.value.strip()
        if cid_raw:
            try:
                cargos[str(q)] = int(cid_raw)
                acao = f"Cargo <@&{cid_raw}> → **{q} salas**"
            except:
                return await i.response.send_message(
                    embed=_emb("❌ ID inválido!", config.COR_ERRO), ephemeral=True
                )
        else:
            cargos.pop(str(q), None)
            acao = f"Cargo removido de **{q} salas**"
        guild_set_cargos_por_qtd(str(self.guild_id), cargos)
        em = _emb(f"{ON}  Cargo por Qtd atualizado!", config.COR_SUCESSO)
        em.description = f"*Servidor:* `{self.guild_id}`\n{acao}"
        await i.response.send_message(embed=em, ephemeral=True)


def _cargos_guild_emb(guild, bot) -> discord.Embed:
    """Embed do sub-painel de cargos de um servidor."""
    from utils.database import guild_get_cargo_cliente, guild_get_cargos_por_qtd
    gid = str(guild.id)
    cargo_base = guild_get_cargo_cliente(gid)
    cargos_qtd = guild_get_cargos_por_qtd(gid)

    em = discord.Embed(title=f"👥  Cargos — {guild.name}", color=config.COR_INFO)
    em.set_thumbnail(url=guild.icon.url if guild.icon else None)

    base_txt = f"<@&{cargo_base}>" if cargo_base else f"{OFF} *Não definido*"
    em.add_field(name=f"{GIFT} Cargo Cliente (todo comprador)", value=base_txt, inline=False)

    if cargos_qtd:
        linhas = []
        for qs in sorted(cargos_qtd.keys(), key=lambda x: int(x)):
            linhas.append(f"`{qs}+` salas → <@&{cargos_qtd[qs]}>")
        em.add_field(name=f"{TOP} Cargos por Qtd", value="\n".join(linhas), inline=False)
    else:
        em.add_field(name=f"{TOP} Cargos por Qtd", value=f"{OFF} Nenhum", inline=False)

    em.set_footer(text=f"Servidor ID: {guild.id}")
    return em


class CargosGuildPainelView(discord.ui.View):
    """Sub-painel de cargos de uma guild específica."""
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="Definir Cargo Cliente", emoji=PE["gift"], style=discord.ButtonStyle.primary, row=0)
    async def btn_cargo_base(self, i, b):
        from utils.database import guild_get_cargo_cliente
        atual = guild_get_cargo_cliente(str(self.guild_id))
        await i.response.send_modal(ModalCargoClienteGuild(self.guild_id, atual))

    @discord.ui.button(label="Cargos por Qtd", emoji=PE["top"], style=discord.ButtonStyle.primary, row=0)
    async def btn_cargos_qtd(self, i, b):
        await i.response.send_modal(ModalCargoQtdGuild(self.guild_id))

    @discord.ui.button(label="Atualizar", emoji=PE["refresh"], style=discord.ButtonStyle.secondary, row=1)
    async def btn_refresh(self, i, b):
        guild = i.client.get_guild(self.guild_id)
        if not guild:
            return await i.response.send_message(
                embed=_emb("❌ Servidor não encontrado.", config.COR_ERRO), ephemeral=True
            )
        await i.response.edit_message(embed=_cargos_guild_emb(guild, i.client), view=self)


class _EscolherGuildCargosSelect(discord.ui.Select):
    """Select com todas as guilds onde o bot está."""
    def __init__(self, client):
        guilds = sorted(client.guilds, key=lambda g: g.name.lower())[:25]
        opcoes = []
        for g in guilds:
            opcoes.append(discord.SelectOption(
                label=g.name[:80],
                value=str(g.id),
                description=f"ID: {g.id} • {g.member_count or 0} membros"[:90],
            ))
        if not opcoes:
            opcoes.append(discord.SelectOption(label="— nenhum servidor —", value="0"))
        super().__init__(
            placeholder="Escolha o servidor para configurar cargos...",
            min_values=1, max_values=1, options=opcoes
        )

    async def callback(self, inter: discord.Interaction):
        gid = int(self.values[0])
        if gid == 0:
            return await inter.response.edit_message(
                embed=_emb("Nenhum servidor.", config.COR_AVISO), view=None
            )
        guild = inter.client.get_guild(gid)
        if not guild:
            return await inter.response.edit_message(
                embed=_emb("❌ Servidor não encontrado.", config.COR_ERRO), view=None
            )
        await inter.response.edit_message(
            embed=_cargos_guild_emb(guild, inter.client),
            view=CargosGuildPainelView(gid),
        )


class EscolherGuildCargosView(discord.ui.View):
    def __init__(self, client):
        super().__init__(timeout=180)
        self.add_item(_EscolherGuildCargosSelect(client))


class BancoPixSelectView(discord.ui.View):
    """Select para escolher Efí ou MisticPay."""
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        placeholder="Selecione o banco PIX...",
        options=[
            discord.SelectOption(
                label="Efí Bank",
                description="API PIX da Efí (Gerencianet) com certificado",
                emoji=PE["pix"],
                value="efi",
            ),
            discord.SelectOption(
                label="MisticPay",
                description="API PIX da MisticPay (CI/CS)",
                emoji=PE["pix"],
                value="mistic",
            ),
        ],
        row=0,
    )
    async def select_banco(self, inter, select):
        banco = select.values[0]
        from utils.pix import set_banco_ativo
        set_banco_ativo(banco)

        # Atualiza botconfig.json também
        cfg = carregar_cfg()
        cfg["banco_pix"] = banco
        salvar_cfg(cfg)

        banco_nome = "Efí Bank" if banco == "efi" else "MisticPay"

        em = _emb(f"{ON}  Banco PIX Alterado!", config.COR_SUCESSO)
        em.description = (
            f"{PIX} Banco ativo: **{banco_nome}**\n\n"
            f"{DOT} Todas as novas cobranças PIX serão geradas via **{banco_nome}**.\n"
            f"{DOT} Cobranças pendentes anteriores continuam no banco original."
        )
        await inter.response.edit_message(embed=em, view=None)


class PainelCompraConfigView(discord.ui.View):
    """View vazia — só mostra o embed do perfil."""
    def __init__(self): super().__init__(timeout=None)


# ═══════════════════════════════════════════
#  Funções públicas
# ═══════════════════════════════════════════

def vendas_ativas(): return carregar_cfg().get("vendas_ativas", True)

async def dar_cargo_comprador(bot, guild_id, user_id, quantia):
    """Dá os cargos da guild onde a compra foi feita.

    Lógica:
    1. Cargo cliente da guild local (definido em /dev), se houver
    2. Cargo cliente GLOBAL daquele server dono (cargos_cliente_global no botconfig),
       se essa guild for um server dono
    3. Cargo por quantidade (guild local) ou fallback global
    """
    import logging as _lg
    _lcc = _lg.getLogger("salasff.cargocliente")

    from utils.database import guild_get_cargo_cliente, guild_get_cargos_por_qtd
    cfg_global = carregar_cfg()

    guild = bot.get_guild(guild_id)
    if not guild:
        _lcc.info(f"[cargo] guild {guild_id} não encontrada — pulando")
        return

    try:
        member = guild.get_member(user_id)
        if not member:
            member = await guild.fetch_member(user_id)
    except Exception as ex:
        _lcc.info(f"[cargo] user {user_id} não está em guild {guild_id}: {ex}")
        return

    gid_str = str(guild.id)

    # ── Decide qual cargo base usar ──
    # Prioridade: 1) cargo_cliente da própria guild (via /dev)
    #             2) cargos_cliente_global do botconfig pra essa guild (se for dono)
    #             3) legacy cargo_cliente_id (fallback antigo, só pra primeira guild dona)
    cargo_base_id = guild_get_cargo_cliente(gid_str)

    if cargo_base_id is None:
        cargos_global = cfg_global.get("cargos_cliente_global") or {}
        cargo_base_id = cargos_global.get(gid_str)

    if cargo_base_id is None:
        # Legacy fallback (só pra guild dona principal)
        owner_ids = list(config.OWNER_GUILD_IDS) if hasattr(config, "OWNER_GUILD_IDS") else list(config.GUILD_IDS)
        if owner_ids and guild.id == owner_ids[0]:
            cargo_base_id = cfg_global.get("cargo_cliente_id")

    if cargo_base_id:
        try:
            cargo = guild.get_role(int(cargo_base_id))
            if not cargo:
                _lcc.warning(f"[cargo] cargo {cargo_base_id} não existe na guild {gid_str}")
            elif cargo in member.roles:
                _lcc.info(f"[cargo] user {user_id} já tem cargo {cargo.name} em {gid_str}")
            else:
                if guild.me.top_role <= cargo:
                    _lcc.warning(
                        f"[cargo] hierarquia: cargo do bot ({guild.me.top_role.name}) "
                        f"está abaixo de {cargo.name} em {gid_str} — não pode atribuir"
                    )
                elif not guild.me.guild_permissions.manage_roles:
                    _lcc.warning(f"[cargo] bot sem permissão Manage Roles em {gid_str}")
                else:
                    await member.add_roles(cargo, reason="Compra SalasFF")
                    _lcc.info(f"[cargo] ✅ cargo base '{cargo.name}' dado a {user_id} em {gid_str}")
        except discord.Forbidden as ex:
            _lcc.warning(f"[cargo] Forbidden ao dar cargo base em {gid_str}: {ex}")
        except Exception as ex:
            _lcc.warning(f"[cargo] erro ao dar cargo base em {gid_str}: {ex}")

    # ── Cargo por quantidade ──
    cq = guild_get_cargos_por_qtd(gid_str)
    if not cq:
        cq = cfg_global.get("cargos_por_qtd", {}) or {}

    melhor = None
    for qs, cid in cq.items():
        try:
            if quantia >= int(qs) and (melhor is None or int(qs) > int(melhor[0])):
                melhor = (qs, cid)
        except:
            pass
    if melhor:
        try:
            cargo = guild.get_role(int(melhor[1]))
            if not cargo:
                _lcc.warning(f"[cargo] cargo por qtd {melhor[1]} não existe em {gid_str}")
            elif cargo in member.roles:
                _lcc.info(f"[cargo] user {user_id} já tem cargo qtd {cargo.name} em {gid_str}")
            else:
                if guild.me.top_role <= cargo:
                    _lcc.warning(
                        f"[cargo qtd] hierarquia: cargo do bot abaixo de {cargo.name} em {gid_str}"
                    )
                else:
                    await member.add_roles(cargo, reason=f"Compra SalasFF — {quantia} salas")
                    _lcc.info(f"[cargo] ✅ cargo qtd '{cargo.name}' dado a {user_id} em {gid_str}")
        except Exception as ex:
            _lcc.warning(f"[cargo qtd] erro em {gid_str}: {ex}")


# ═══════════════════════════════════════════
#  COG
# ═══════════════════════════════════════════

_log = logging.getLogger("salasff.msgauto")

def get_promo_ativa() -> dict | None:
    return carregar_cfg().get("mega_promo")

def set_promo_ativa(ate_hora: str, preco_centavos: int, preco_original_centavos: int):
    cfg = carregar_cfg()
    cfg["mega_promo"] = {
        "ate_hora": ate_hora,
        "preco_centavos": preco_centavos,
        "preco_original_centavos": preco_original_centavos,
    }
    salvar_cfg(cfg)

def clear_promo_ativa():
    cfg = carregar_cfg()
    cfg.pop("mega_promo", None)
    salvar_cfg(cfg)


# ── Promo V2 ──────────────────────────────────────────────────────────────────

def _em(key: str) -> str:
    from utils.emojis import e as _em_str
    try:
        return _em_str(key)
    except Exception:
        return ""


def _build_promo_v2_payload(mensagem: str, mencionar: bool = True) -> dict:
    """Monta payload Components V2 da mensagem promo (normal ou mega promoção)."""
    from utils.pix import get_preco_por_sala
    preco = get_preco_por_sala()
    cfg   = carregar_cfg()
    canal_id_compras = cfg.get("canal_compras_pub_id")
    canal_txt  = f"<#{canal_id_compras}>" if canal_id_compras else "**#compras**"
    promo_ativa = cfg.get("mega_promo")
    mention = "@everyone" if mencionar else ""

    if promo_ativa:
        ate_hora  = promo_ativa.get("ate_hora", "?")
        preco_cts = promo_ativa.get("preco_centavos", round(preco * 100))
        inner = [
            {
                "id": 1, "type": 10,
                "content": f"## {_em('rage')} MEGA PROMOÇÃO — SALAS A {preco_cts} CENTAVOS!",
            },
            {
                "id": 2, "type": 10,
                "content": (
                    (f"{mensagem}\n\n" if mensagem and mensagem.strip() else "")
                    + f"{_em('awaiting')} **Promoção válida somente até às {ate_hora} BRT!**\n"
                    "-# Após encerrar, o preço volta ao normal. Não perca!"
                ),
            },
            {"id": 3, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 4, "type": 10,
                "content": (
                    f"{_em('otherdollar')} **PREÇO ESPECIAL:** R$ {preco_cts/100:.2f}/sala\n"
                    f"{_em('clockcheck')} **Encerra às:** {ate_hora} BRT\n"
                    f"{_em('channel')} **Compre aqui:** {canal_txt}"
                ),
            },
        ]
        accent = 0xED4245  # vermelho — urgência
    else:
        inner = [
            {
                "id": 1, "type": 10,
                "content": f"## {_em('swordbattle')} COMPRE SALAS AGORA",
            },
            {
                "id": 2, "type": 10,
                "content": mensagem,
            },
            {"id": 3, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 4, "type": 10,
                "content": (
                    f"{_em('otherdollar')} **Preço:** R$ {preco:.2f}/sala\n"
                    f"{_em('channel')} **Compre aqui:** {canal_txt}"
                ),
            },
        ]
        accent = 0xFFD700

    if mention:
        inner.append({"id": 5, "type": 10, "content": f"-# {mention}"})

    payload = {
        "flags": 32768,
        "components": [{"id": 0, "type": 17, "accent_color": accent, "components": inner}],
    }
    if mention:
        payload["allowed_mentions"] = {"parse": ["everyone"]}
    return payload


async def _post_promo_v2(canal_id: int, payload: dict) -> discord.Message | None:
    """Envia payload V2 via REST (necessário para flag IS_COMPONENTS_V2)."""
    import json as _json
    import config as _cfg
    url = f"https://discord.com/api/v10/channels/{canal_id}/messages"
    headers = {
        "Authorization": f"Bot {_cfg.DISCORD_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status in (200, 201):
                    data = await r.json()
                    return data.get("id")
                _log.warning(f"[promo v2] {r.status} {(await r.text())[:200]}")
    except Exception as ex:
        _log.error(f"[promo v2] {ex}")
    return None


class BotConfigCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._canal_tasks: dict[int, asyncio.Task] = {}  # canal_id → task
        self._skip_send_once: set[int] = set()           # canais que devem pular o 1º envio
        self._aa_task: asyncio.Task | None = None        # loop exclusivo do +aa

    async def cog_load(self):
        """Inicia loops dos canais que estavam ativos antes do restart."""
        ma = get_msg_auto()
        ativos = [c["canal_id"] for c in ma["canais"] if c.get("ativo")]
        if ativos:
            _log.info(f"[msg_auto] restaurando {len(ativos)} canal(is) ativo(s)")
            for cid in ativos:
                self._start_canal(cid)
        # Restaura loop do +aa se promo estava ativa com canal_id
        promo = get_promo_ativa()
        if promo and promo.get("canal_id"):
            _log.info(f"[+aa] restaurando loop no canal {promo['canal_id']}")
            self._start_aa_loop(int(promo["canal_id"]))
        self._check_promo_expiry.start()

    async def cog_unload(self):
        for cid in list(self._canal_tasks.keys()):
            self._stop_canal(cid)
        self._stop_aa_loop()
        self._check_promo_expiry.cancel()

    @tasks.loop(minutes=1)
    async def _check_promo_expiry(self):
        """Verifica a cada minuto se a mega promoção expirou."""
        try:
            promo = await asyncio.to_thread(get_promo_ativa)
            if not promo:
                return
            agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
            ate_hora = promo.get("ate_hora", "")
            if not ate_hora:
                return
            h, m = [int(x) for x in ate_hora.split(":")]
            limite = agora.replace(hour=h, minute=m, second=0, microsecond=0)
            if agora < limite:
                return

            # Expirou — reverte preço apenas se era mega promo (preco_centavos != None)
            preco_centavos_promo = promo.get("preco_centavos")
            preco_original_cts   = promo.get("preco_original_centavos", 9)
            aa_canal_id          = promo.get("canal_id")
            cfg = await asyncio.to_thread(carregar_cfg)
            if preco_centavos_promo is not None:
                cfg["preco_por_sala"] = round(preco_original_cts / 100, 4)
            cfg.pop("mega_promo", None)
            await asyncio.to_thread(salvar_cfg, cfg)
            from utils.database import botconfig_save
            await asyncio.to_thread(botconfig_save, cfg)
            self._stop_aa_loop()
            _log.info(f"[promo] Promoção encerrada às {ate_hora}. preco_centavos={preco_centavos_promo}")

            # Monta payload de encerramento
            if preco_centavos_promo is not None:
                corpo_fim = (
                    f"A mega promoção das **{ate_hora}** chegou ao fim.\n"
                    f"{_em('otherdollar')} Preço voltou ao normal: **R$ {preco_original_cts/100:.2f}/sala**\n\n"
                    "-# Fique de olho nas próximas promoções!"
                )
            else:
                corpo_fim = (
                    f"A promoção das **{ate_hora}** chegou ao fim.\n\n"
                    "-# Fique de olho nas próximas promoções!"
                )
            fim_inner = [
                {"id": 1, "type": 10, "content": f"## {_em('clockcheck')} Promoção Encerrada!"},
                {"id": 2, "type": 10, "content": corpo_fim},
                {"id": 3, "type": 10, "content": "-# @everyone"},
            ]
            payload_fim = {
                "flags": 32768,
                "components": [{"id": 0, "type": 17, "accent_color": 0x95A5A6, "components": fim_inner}],
                "allowed_mentions": {"parse": ["everyone"]},
            }

            # Posta no canal do +aa (se existir)
            if aa_canal_id:
                try:
                    await _post_promo_v2(int(aa_canal_id), payload_fim)
                except Exception as _ex:
                    _log.warning(f"[promo:fim] erro canal +aa {aa_canal_id}: {_ex}")

            # Posta em canais msg_auto tipo promo (se existirem)
            ma = await asyncio.to_thread(get_msg_auto)
            for c in ma.get("canais", []):
                if c.get("ativo") and c.get("tipo") == "promo":
                    try:
                        await _post_promo_v2(int(c["canal_id"]), payload_fim)
                    except Exception as _ex:
                        _log.warning(f"[promo:fim] erro em {c['canal_id']}: {_ex}")
        except Exception as ex:
            _log.error(f"[promo:expiry] {ex}")

    @_check_promo_expiry.before_loop
    async def _before_promo(self):
        await self.bot.wait_until_ready()

    # ── Controle por canal ────────────────────────────────────────
    def _start_canal(self, canal_id: int):
        """Inicia a task de um canal específico."""
        existing = self._canal_tasks.get(canal_id)
        if existing and not existing.done():
            return
        self._canal_tasks[canal_id] = self.bot.loop.create_task(self._canal_loop(canal_id))

    def _stop_canal(self, canal_id: int):
        task = self._canal_tasks.get(canal_id)
        if task and not task.done():
            task.cancel()
        self._canal_tasks.pop(canal_id, None)

    # ── Loop exclusivo do +aa (independente do msg_auto) ─────────────
    def _start_aa_loop(self, canal_id: int):
        self._stop_aa_loop()
        self._aa_task = self.bot.loop.create_task(self._aa_promo_loop(canal_id))

    def _stop_aa_loop(self):
        if self._aa_task and not self._aa_task.done():
            self._aa_task.cancel()
        self._aa_task = None

    async def _aa_promo_loop(self, canal_id: int):
        """Envia promo V2 a cada 30 min no canal do +aa até a hora configurada."""
        await self.bot.wait_until_ready()
        ultima_msg_id = None
        try:
            while True:
                await asyncio.sleep(30 * 60)

                promo = await asyncio.to_thread(get_promo_ativa)
                if not promo or int(promo.get("canal_id", 0)) != canal_id:
                    return

                # Verifica expiração antes de enviar
                agora = datetime.now(_BR)
                ate_hora = promo.get("ate_hora", "")
                if ate_hora:
                    hh, mm = [int(x) for x in ate_hora.split(":")]
                    limite = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
                    if agora >= limite:
                        return

                canal = self.bot.get_channel(canal_id)
                if not canal:
                    _log.warning(f"[aa_loop] canal {canal_id} não encontrado")
                    return

                # Deleta mensagem anterior
                if ultima_msg_id:
                    try:
                        msg = await canal.fetch_message(int(ultima_msg_id))
                        await msg.delete()
                    except Exception:
                        pass

                # Build payload inline — evita bug de content vazio
                _promo_cts  = promo.get("preco_centavos")
                _ate_str    = promo.get("ate_hora", "?")
                _cfg_loop   = await asyncio.to_thread(carregar_cfg)
                if _promo_cts is not None:
                    _inner = [
                        {"id": 1, "type": 10, "content": f"## {_em('rage')} MEGA PROMOÇÃO — SALAS A {_promo_cts} CENTAVOS!"},
                        {"id": 2, "type": 10, "content": (
                            f"{_em('awaiting')} **Promoção válida somente até às {_ate_str} BRT!**\n"
                            "-# Após encerrar, o preço volta ao normal. Não perca!"
                        )},
                        {"id": 3, "type": 14, "divider": True, "spacing": 1},
                        {"id": 4, "type": 10, "content": (
                            f"{_em('otherdollar')} **PREÇO ESPECIAL:** R$ {_promo_cts/100:.2f}/sala\n"
                            f"{_em('clockcheck')} **Encerra às:** {_ate_str} BRT"
                        )},
                        {"id": 5, "type": 10, "content": "-# @everyone"},
                    ]
                    _accent = 0xED4245
                else:
                    _preco_r = _cfg_loop.get("preco_por_sala", 0.09)
                    _inner = [
                        {"id": 1, "type": 10, "content": f"## {_em('swordbattle')} COMPRE SALAS AGORA"},
                        {"id": 2, "type": 10, "content": (
                            f"{_em('awaiting')} **Promoção válida somente até às {_ate_str} BRT!**\n"
                            "-# Não perca!"
                        )},
                        {"id": 3, "type": 14, "divider": True, "spacing": 1},
                        {"id": 4, "type": 10, "content": (
                            f"{_em('otherdollar')} **Preço:** R$ {_preco_r:.2f}/sala\n"
                            f"{_em('clockcheck')} **Encerra às:** {_ate_str} BRT"
                        )},
                        {"id": 5, "type": 10, "content": "-# @everyone"},
                    ]
                    _accent = 0xFFD700
                payload = {
                    "flags": 32768,
                    "components": [{"id": 0, "type": 17, "accent_color": _accent, "components": _inner}],
                    "allowed_mentions": {"parse": ["everyone"]},
                }
                nova_id = await _post_promo_v2(canal_id, payload)
                if nova_id:
                    ultima_msg_id = nova_id
                    _log.info(f"[aa_loop] promo enviada em #{canal.name}")

        except asyncio.CancelledError:
            _log.info(f"[aa_loop:{canal_id}] cancelado")
            raise
        except Exception as ex:
            _log.error(f"[aa_loop:{canal_id}] erro: {ex}", exc_info=True)

    def _restart_canal(self, canal_id: int, skip_first_send: bool = False):
        self._stop_canal(canal_id)
        if skip_first_send:
            self._skip_send_once.add(canal_id)
        self._start_canal(canal_id)

    async def _canal_loop(self, canal_id: int):
        """Loop independente de um canal: envia → espera intervalo → deleta → envia ..."""
        try:
            await self.bot.wait_until_ready()

            while True:
                c = get_canal_cfg(canal_id)
                if not c or not c.get("ativo"):
                    _log.info(f"[msg_auto:{canal_id}] desligado — saindo")
                    return

                canal = self.bot.get_channel(canal_id)
                if not canal:
                    _log.warning(f"[msg_auto:{canal_id}] canal não encontrado — desativando")
                    update_canal_cfg(canal_id, ativo=False)
                    return

                # Se +aa acabou de enviar a promo, pula este ciclo (só dorme)
                if canal_id in self._skip_send_once:
                    self._skip_send_once.discard(canal_id)
                    c = get_canal_cfg(canal_id)
                    if not c:
                        return
                    intervalo_seg = int(c.get("intervalo_min", 30)) * 60
                    await asyncio.sleep(max(60, intervalo_seg))
                    continue

                # 1) Deleta mensagem anterior
                ultima_id = c.get("ultima_msg_id")
                if ultima_id:
                    try:
                        ultima = await canal.fetch_message(int(ultima_id))
                        await ultima.delete()
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        _log.warning(f"[msg_auto:{canal_id}] sem permissão pra deletar")
                    except Exception as ex:
                        _log.warning(f"[msg_auto:{canal_id}] erro deletando: {ex}")

                # 2) Envia mensagem nova
                try:
                    if c.get("tipo") == "promo":
                        mencionar = c.get("mencionar_everyone", True)
                        payload   = await asyncio.to_thread(
                            _build_promo_v2_payload, c.get("mensagem", ""), mencionar
                        )
                        nova_id = await _post_promo_v2(canal_id, payload)
                        if nova_id:
                            update_canal_cfg(canal_id, ultima_msg_id=nova_id)
                            _log.info(f"[msg_auto:{canal_id}] promo V2 enviada em #{canal.name}")
                        else:
                            _log.warning(f"[msg_auto:{canal_id}] falha ao enviar promo V2")
                    else:
                        nova = await canal.send(c["mensagem"])
                        update_canal_cfg(canal_id, ultima_msg_id=nova.id)
                        _log.info(f"[msg_auto:{canal_id}] texto enviado em #{canal.name}")
                except discord.Forbidden:
                    _log.warning(f"[msg_auto:{canal_id}] sem permissão pra enviar — desativando")
                    update_canal_cfg(canal_id, ativo=False)
                    return
                except Exception as ex:
                    _log.warning(f"[msg_auto:{canal_id}] erro enviando: {ex}")

                # 3) Aguarda intervalo (recarrega pra pegar mudanças)
                c = get_canal_cfg(canal_id)
                if not c:
                    return
                intervalo_seg = int(c.get("intervalo_min", 30)) * 60
                await asyncio.sleep(max(60, intervalo_seg))

        except asyncio.CancelledError:
            _log.info(f"[msg_auto:{canal_id}] task cancelada")
            raise
        except Exception as ex:
            _log.error(f"[msg_auto:{canal_id}] erro fatal: {ex}", exc_info=True)

    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="botconfig", description="[ADMIN] Configuração do bot.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_botconfig(self, i):
        if i.user.id not in config.ADMIN_IDS:
            return await i.response.send_message(embed=_emb("❌ Sem permissão.", config.COR_ERRO), ephemeral=True)
        await i.response.send_message(embed=_painel_emb(carregar_cfg(), i.guild), view=BotConfigView(), ephemeral=True)


class _CategoriaSelectView(discord.ui.View):
    def __init__(self): super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione a categoria...",
        channel_types=[discord.ChannelType.category],
        min_values=1, max_values=1,
    )
    async def sel(self, inter, s):
        cfg = carregar_cfg(); cfg["gratis_categoria_id"] = s.values[0].id; salvar_cfg(cfg)
        em = discord.Embed(title=f"{ON}  Categoria definida!", color=config.COR_SUCESSO)
        em.description = f"> Categoria: {s.values[0].mention}"
        await inter.response.edit_message(embed=em, view=None)


class _TicketSelectView(discord.ui.View):
    def __init__(self): super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de ticket...",
        channel_types=[discord.ChannelType.text],
        min_values=1, max_values=1,
    )
    async def sel(self, inter, s):
        cfg = carregar_cfg(); cfg["gratis_ticket_canal_id"] = s.values[0].id; salvar_cfg(cfg)
        em = discord.Embed(title=f"{ON}  Canal de ticket definido!", color=config.COR_SUCESSO)
        em.description = f"> Canal: {s.values[0].mention}"
        await inter.response.edit_message(embed=em, view=None)

    @app_commands.guilds(*_ADMIN_GUILDS)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.command(name="resetgratis", description="[ADMIN] Reseta lista de resgates do Painel Grátis.")
    @app_commands.guilds(*[discord.Object(id=g) for g in config.OWNER_GUILD_IDS])
    async def cmd_resetgratis(self, i):
        if i.user.id not in config.ADMIN_IDS:
            return await i.response.send_message(embed=_emb("❌ Sem permissão.", config.COR_ERRO), ephemeral=True)

        await i.response.defer(ephemeral=True)

        total_cfg   = 0
        total_mongo = 0

        # 1 — Limpar botconfig.json
        try:
            cfg = carregar_cfg()
            total_cfg = len(cfg.get("gratis_resgatados", []))
            cfg["gratis_resgatados"] = []
            salvar_cfg(cfg)
        except Exception:
            pass

        # 2 — Limpar collection resgates_gratis no MongoDB
        try:
            from utils.database import _get_db
            db = _get_db()
            if db is not None:
                result = db["resgates_gratis"].delete_many({})
                total_mongo = result.deleted_count
        except Exception:
            pass

        total = max(total_cfg, total_mongo)

        em = discord.Embed(title=f"{ON}  Resgates Resetados!", color=config.COR_SUCESSO)
        em.description = (
            f"> **{total}** registro(s) removidos.\n"
            f"> Todos podem resgatar o Painel Grátis novamente."
        )
        await i.followup.send(embed=em, ephemeral=True)


async def setup(bot): await bot.add_cog(BotConfigCog(bot))
