# utils/emojis.py — Emojis centralizados com auto-upload pro Application

import discord
import aiohttp
import asyncio
import json
import os
import logging
import base64

_log = logging.getLogger("salasff.emojis")

# ═══════════════════════════════════════════
#  IDs originais (do servidor)
# ═══════════════════════════════════════════

_GUILD_EMOJIS = {
    "bot_StorM":           {"id": 1481526579963363450, "animated": False},
    "estatisticas":        {"id": 1483209542790545592, "animated": False},
    "e_9081_settings":     {"id": 1483209741512609833, "animated": False},
    "e_9396_info":         {"id": 1483210042529415218, "animated": False},
    "jogadores":           {"id": 1483205147709407444, "animated": False},
    "copiar":              {"id": 1483205475486007477, "animated": False},
    "ChatGPTImage6deset":  {"id": 1483205620592017612, "animated": False},
    "upd":                 {"id": 1483784654812090429, "animated": False},
    "za_houst1":           {"id": 1483205696077037821, "animated": False},
    "e_8907_top":          {"id": 1483781590072037386, "animated": False},
    "carteria":            {"id": 1483687546767802481, "animated": False},
    "7375gift":            {"id": 1483688268066328598, "animated": False},
    "MOBILE":              {"id": 1433478699977801820, "animated": False},
    "Dot3":                {"id": 1483687902427873331, "animated": False},
    "black24":             {"id": 1303883819400560690, "animated": False},
    "emoji_70":            {"id": 1483844020466880532, "animated": False},
    "DiscordOn":           {"id": 1483691647597547563, "animated": True},
    "DiscordOff":          {"id": 1483691698373660753, "animated": True},
    "a":                   {"id": 1483210291067093113, "animated": False},
    "roles":               {"id": 1487110056582578377, "animated": False},
    "click":               {"id": 1487110386519113789, "animated": False},
    "awaiting":            {"id": 1487110484095402075, "animated": True},
    "Clock":               {"id": 1487109954769916128, "animated": False},
    "vision":              {"id": 1487110627875885196, "animated": False},
    "rage":                {"id": 1487111108086075442, "animated": False},
    "loading":             {"id": 1487111032752181361, "animated": True},
    "channel": {"id": 1487690008306651167, "animated": False},
    "reloading": {"id": 1487682905349292184, "animated": True},
    "url": {"id": 1487690266767917287, "animated": False},
    "swordbattle": {"id": 1487690180201549976, "animated": False},
    "pix": {"id": 1480993440933347508, "animated": False},
    "othertrash": {"id": 1487690112132317184, "animated": False},
    "otherdollar": {"id": 1487689932624494693, "animated": False},
    "clockcheck": {"id": 1487689813179236472, "animated": False},
    "cloud": {"id": 1487690058973843496, "animated": False},
    "white_calendario": {"id": 1346110785436520562, "animated": False},
    "store_emoji":      {"id": 1465942392543641624, "animated": False},
    "adduser": {"id": 1487682806191624383, "animated": False},
    "a_ban": {"id": 1424618527771463691, "animated": False},
    "Presente": {"id": 1487288588835229846, "animated": False},
    "emoji_99": {"id": 1442445867540938863, "animated": False},
    "e_009": {"id": 1478180562400182273, "animated": False},
    "rr_seta": {"id": 1420986484156010516, "animated": False},
    "verified": {"id": 1481526868061716481, "animated": True},
    "megafone": {"id": 1458584639907299435, "animated": False},
    "emoji_224": {"id": 1357375503379726509, "animated": False},
}

# Mapeamento chave amigável → nome do emoji
_KEY_MAP = {
    # emojis sem fundo (novos)
    "roles":       "roles",        # pessoas/cargo
    "click":       "click",        # clique/interação
    "vision":      "vision",       # olho/ver
    "reloading":   "reloading",    # atualizar (animado)
    "url":         "url",          # link/url
    "swordbattle": "swordbattle",  # batalha/modo
    "pix":         "pix",          # pagamento
    "othertrash":  "othertrash",   # lixo/remover
    "otherdollar": "otherdollar",  # dinheiro
    "clockcheck":  "clockcheck",   # relógio/confirmado
    "cloud":       "cloud",        # nuvem
    "calendario":  "white_calendario",  # data
    "store":       "store_emoji",       # perfil/loja
    "adduser":     "adduser",      # adicionar usuário
    "ban":         "a_ban",        # banir
    "presente":    "Presente",     # presente/bonus
    "sala_id":     "emoji_99",     # ID da sala
    "sala_senha":  "e_009",        # senha da sala
    "channel":     "channel",      # canal/lista
    "seta":        "rr_seta",      # seta/indicador
    "verified":    "verified",     # verified animado
    "megafone":    "megafone",     # megafone
    "sep224":      "emoji_224",    # separador
    # emojis que ficam como estão (sem substituto melhor)
    "bot":     "bot_StorM",
    "on":      "DiscordOn",
    "off":     "DiscordOff",
    "awaiting":"awaiting",
    "rage":    "rage",
    "loading": "loading",
    # aliases apontando para sem-fundo
    "stats":    "channel",       # era estatisticas (fundo) → channel
    "settings": "clockcheck",    # era settings (fundo) → clockcheck
    "info":     "url",           # era info (fundo) → url
    "refresh":  "reloading",     # era upd (fundo) → reloading
    "carteira": "otherdollar",   # era carteria (fundo) → otherdollar
    "gift":     "pix",           # era gift (fundo) → pix
    "mobile":   "adduser",       # era MOBILE (fundo) → adduser
    "dot":      "roles",         # era Dot3 (fundo) → roles
    "emoji70":  "otherdollar",   # era emoji_70 (fundo) → otherdollar
    "copiar":   "url",           # era copiar (fundo) → url
    "top":      "swordbattle",   # era top (fundo) → swordbattle
    "clock":    "clockcheck",    # era Clock (fundo) → clockcheck
    "play":     "swordbattle",   # era za_houst1 (fundo) → swordbattle
    "jogadores":"roles",         # era jogadores (fundo) → roles
    "enviar":   "channel",       # era ChatGPTImage6deset → channel
    "black":    "channel",       # era black24 → channel
    "fullcapa": "swordbattle",   # era a → swordbattle
    "money":    "otherdollar",   # alias extra
}

# ═══════════════════════════════════════════
#  Cache de Application Emojis
# ═══════════════════════════════════════════

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_PATH = os.path.join(_BASE_DIR, "app_emojis_cache.json")

def _load_cache() -> dict:
    try:
        with open(_CACHE_PATH, "r") as f:
            return json.load(f)
    except: return {}

def _save_cache(data: dict):
    with open(_CACHE_PATH, "w") as f:
        json.dump(data, f, indent=2)

# ═══════════════════════════════════════════
#  Inicializa PE com IDs do servidor (fallback)
# ═══════════════════════════════════════════

def _build_pe() -> dict:
    """Constrói dict PE usando cache de app emojis se existir, senão usa guild IDs."""
    cache = _load_cache()
    pe = {}
    for key, emoji_name in _KEY_MAP.items():
        guild = _GUILD_EMOJIS[emoji_name]
        animated = guild["animated"]
        # Usa app emoji se tiver no cache
        if emoji_name in cache:
            eid = int(cache[emoji_name])
        else:
            eid = guild["id"]
        pe[key] = discord.PartialEmoji(name=emoji_name, id=eid, animated=animated)
    return pe


PE = _build_pe()


def e(key: str) -> str:
    """Retorna string formatada do emoji para uso em textos."""
    em = PE[key]
    pre = "<a:" if em.animated else "<:"
    return f"{pre}{em.name}:{em.id}>"


# Atalhos
BOT      = e("bot")
STATS    = e("stats")
SETTINGS = e("settings")
INFO     = e("info")
DOT      = e("dot")
ON       = e("on")
OFF      = e("off")
GIFT     = e("gift")
CART     = e("carteira")
TOP      = e("top")
MOBILE   = e("mobile")
PLAY     = e("play")
REFRESH  = e("refresh")
MONEY    = e("emoji70")
COPIAR   = e("copiar")
ROLES    = e("roles")
CLICK    = e("click")
AWAITING = e("awaiting")
CLOCK    = e("clock")
VISION   = e("vision")
RAGE     = e("rage")
LOADING  = e("loading")
CHANNEL  = e("channel")
RELOADING= e("reloading")
URL      = e("url")
SWORD    = e("swordbattle")
PIX      = e("pix")
TRASH    = e("othertrash")
DOLLAR   = e("otherdollar")
CLOCKCHECK=e("clockcheck")
CLOUD    = e("cloud")
ADDUSER  = e("adduser")
BAN      = e("ban")
PRESENTE = e("presente")
SALA_ID  = e("sala_id")
SALA_SENHA = e("sala_senha")


# ═══════════════════════════════════════════
#  Auto-upload pro Application (roda 1x)
# ═══════════════════════════════════════════

async def sync_app_emojis(bot=None):
    """
    Sobe todos os emojis pro Application do bot.
    Funciona em qualquer lugar (DM, User Install, outros servers).
    Roda 1x — depois usa cache.
    """
    global PE, BOT, STATS, SETTINGS, INFO, DOT, ON, OFF, GIFT, CART, TOP, MOBILE, PLAY, REFRESH, MONEY, COPIAR, ROLES, CLICK, AWAITING, CLOCK, VISION, RAGE, LOADING, CHANNEL, RELOADING, URL, SWORD, PIX, TRASH, DOLLAR, CLOCKCHECK, CLOUD, ADDUSER, BAN, PRESENTE, SALA_ID, SALA_SENHA

    import config as _cfg
    token = _cfg.DISCORD_TOKEN

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    cache = _load_cache()

    # Checa se já tem todos no cache
    missing = [name for name in _GUILD_EMOJIS if name not in cache]
    if not missing:
        _log.info(f"[emojis] Todos {len(cache)} emojis no Application (cache)")
        # Reconstrói PE e atalhos com IDs do cache
        PE.update(_build_pe())
        _rebuild_shortcuts()
        return

    _log.info(f"[emojis] {len(missing)} emojis faltando. Subindo...")

    async with aiohttp.ClientSession() as session:
        # Descobre app_id
        try:
            async with session.get("https://discord.com/api/v10/applications/@me", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    app_id = data["id"]
                else:
                    _log.error(f"[emojis] Erro ao buscar app_id: {resp.status}")
                    return
        except Exception as ex:
            _log.error(f"[emojis] Erro app_id: {ex}")
            return

    _log.info(f"[emojis] {len(missing)} emojis faltando no Application. Subindo...")

    async with aiohttp.ClientSession() as session:
        # Lista emojis existentes no Application
        try:
            async with session.get(
                f"https://discord.com/api/v10/applications/{app_id}/emojis",
                headers=headers,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    existing = {em["name"]: str(em["id"]) for em in data.get("items", [])}
                    # Atualiza cache com os que já existem
                    for name, eid in existing.items():
                        if name in _GUILD_EMOJIS:
                            cache[name] = eid
                else:
                    _log.warning(f"[emojis] Erro ao listar app emojis: {resp.status}")
                    existing = {}
        except Exception as ex:
            _log.error(f"[emojis] Erro listando: {ex}")
            return

        # Sobe os que faltam
        uploaded = 0
        for emoji_name in missing:
            if emoji_name in cache:
                continue  # Já achamos na listagem

            info = _GUILD_EMOJIS[emoji_name]
            ext = "gif" if info["animated"] else "png"
            cdn_url = f"https://cdn.discordapp.com/emojis/{info['id']}.{ext}"

            try:
                # Baixa imagem do CDN
                async with session.get(cdn_url) as img_resp:
                    if img_resp.status != 200:
                        _log.warning(f"[emojis] CDN {emoji_name}: status {img_resp.status}")
                        continue
                    img_bytes = await img_resp.read()

                # Converte pra base64
                mime = "image/gif" if info["animated"] else "image/png"
                b64 = base64.b64encode(img_bytes).decode()
                data_uri = f"data:{mime};base64,{b64}"

                # Sobe pro Application
                async with session.post(
                    f"https://discord.com/api/v10/applications/{app_id}/emojis",
                    headers=headers,
                    json={"name": emoji_name, "image": data_uri},
                ) as up_resp:
                    if up_resp.status in (200, 201):
                        result = await up_resp.json()
                        cache[emoji_name] = str(result["id"])
                        uploaded += 1
                        _log.info(f"[emojis] ✅ {emoji_name} → {result['id']}")
                    else:
                        body = await up_resp.text()
                        _log.warning(f"[emojis] ❌ {emoji_name}: {up_resp.status} {body[:100]}")

                # Rate limit: espera um pouco entre uploads
                await asyncio.sleep(1)

            except Exception as ex:
                _log.error(f"[emojis] Erro subindo {emoji_name}: {ex}")

    # Salva cache e reconstrói PE
    _save_cache(cache)
    PE.update(_build_pe())
    _rebuild_shortcuts()

    _log.info(f"[emojis] Sync completo! {uploaded} novos, {len(cache)} total no Application")


def _rebuild_shortcuts():
    """Atualiza as variáveis globais de atalho com os IDs atuais do PE."""
    global BOT, STATS, SETTINGS, INFO, DOT, ON, OFF, GIFT, CART, TOP, MOBILE, PLAY, REFRESH, MONEY, COPIAR, ROLES, CLICK, AWAITING, CLOCK, VISION, RAGE, LOADING, CHANNEL, RELOADING, URL, SWORD, PIX, TRASH, DOLLAR, CLOCKCHECK, CLOUD, ADDUSER, BAN, PRESENTE, SALA_ID, SALA_SENHA
    BOT      = e("bot")
    STATS    = e("stats")
    SETTINGS = e("settings")
    INFO     = e("info")
    DOT      = e("dot")
    ON       = e("on")
    OFF      = e("off")
    GIFT     = e("gift")
    CART     = e("carteira")
    TOP      = e("top")
    MOBILE   = e("mobile")
    PLAY     = e("play")
    REFRESH  = e("refresh")
    MONEY    = e("emoji70")
    COPIAR   = e("copiar")
    ROLES    = e("roles")
    CLICK    = e("click")
    AWAITING = e("awaiting")
    CLOCK    = e("clock")
    VISION   = e("vision")
    RAGE     = e("rage")
    LOADING   = e("loading")
    CHANNEL   = e("channel")
    RELOADING = e("reloading")
    URL       = e("url")
    SWORD     = e("swordbattle")
    PIX       = e("pix")
    TRASH     = e("othertrash")
    DOLLAR    = e("otherdollar")
    CLOCKCHECK= e("clockcheck")
    CLOUD     = e("cloud")
    ADDUSER   = e("adduser")
    BAN       = e("ban")
    PRESENTE  = e("presente")
    SALA_ID   = e("sala_id")
    SALA_SENHA= e("sala_senha")
CHANNEL  = e("channel")
RELOADING= e("reloading")
URL      = e("url")
SWORD    = e("swordbattle")
PIX      = e("pix")
TRASH    = e("othertrash")
DOLLAR   = e("otherdollar")
CLOCKCHECK=e("clockcheck")
CLOUD    = e("cloud")
ADDUSER  = e("adduser")
BAN      = e("ban")
PRESENTE = e("presente")
SALA_ID  = e("sala_id")
SALA_SENHA = e("sala_senha")
