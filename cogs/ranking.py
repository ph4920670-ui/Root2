# cogs/ranking.py — Sistema de Ranking Diário (Top Criadores de Sala)
#
# Fluxo:
#   • Todo dia às 23:59 BRT → distribui prêmios ao top 3 e posta imagem no canal
#   • /painelglobal → "🏆 Postar Ranking"  posta painel público com botão Atualizar
#   • /painelglobal → "📊 Ver Ranking"     mostra top 10 ephemeral para o admin
#   • /botconfig    → "🏆 Ranking Diário"  configura canal / ativa-desativa / prêmios

import asyncio
import io
import logging
import os
import aiohttp
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands, tasks

import config
from utils.emojis import ON, OFF, DOT, INFO, TOP, GIFT, STATS, PE, CART, PRESENTE, e as _em_str

_log = logging.getLogger("salasff.ranking")
_BR  = ZoneInfo("America/Sao_Paulo")

# ── Constantes padrão ────────────────────────────────────────────────────────
PREMIOS_SALAS_DEFAULT = [100, 80, 50]
PREMIOS_REAIS_DEFAULT = [7.0, 5.0, 4.0]

# ── Helpers internos ─────────────────────────────────────────────────────────

def _emb(titulo="", cor=0x5865F2, desc=""):
    return discord.Embed(title=titulo, color=cor, description=desc)

def _ok(t, d=""):   return _emb(f"✅  {t}", config.COR_SUCESSO, d)
def _err(t, d=""):  return _emb(f"❌  {t}", config.COR_ERRO, d)
def _info(t, d=""): return _emb(f"{INFO}  {t}", config.COR_INFO, d)

def _is_admin(uid: int) -> bool:
    return not config.ADMIN_IDS or uid in config.ADMIN_IDS

def _hoje_str() -> str:
    return datetime.now(_BR).strftime("%d/%m/%Y")

def _proximo_reset_str() -> str:
    agora = datetime.now(_BR)
    if agora.hour < 23 or (agora.hour == 23 and agora.minute < 59):
        return agora.replace(hour=23, minute=59, second=0, microsecond=0).strftime("%d/%m às 23:59")
    return (agora + timedelta(days=1)).replace(hour=23, minute=59, second=0, microsecond=0).strftime("%d/%m às 23:59")


# ── Emojis do bot para posições/prêmios ───────────────────────────────────────
# Usa exclusivamente emojis do Application (PE / _em_str), sem unicode.
_POS_KEYS = ["verified", "top", "swordbattle"]  # 1º (dourado animado), 2º, 3º

def _pos_emoji(idx: int) -> str:
    """String do emoji do bot para a posição (0-based)."""
    if idx < len(_POS_KEYS):
        return _em_str(_POS_KEYS[idx])
    return _em_str("dot")

def _emj_pos(idx: int):
    """Dict do emoji do bot (Components V2) para a posição."""
    key = _POS_KEYS[idx] if idx < len(_POS_KEYS) else "dot"
    return _emj(key)

# ── Geração de Imagem Top 3 ──────────────────────────────────────────────────

def _gerar_imagem_top3(top3: list, premios_salas: list, premios_reais: list) -> bytes | None:
    """Gera PNG 900x400 com o pódio do dia."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    W, H = 900, 400
    BG = (13, 17, 23)
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    font_dir = "/usr/share/fonts/truetype/dejavu"

    def _f(size, bold=True):
        fname = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        try:
            return ImageFont.truetype(os.path.join(font_dir, fname), size)
        except Exception:
            return ImageFont.load_default()

    agora = datetime.now(_BR)

    # Barra dourada no topo
    draw.rectangle([0, 0, W, 6], fill=(255, 215, 0))

    f30 = _f(30)
    f17 = _f(17, bold=False)
    f20 = _f(20)
    f16 = _f(16, bold=False)
    f15 = _f(15)
    f14 = _f(14)
    f18 = _f(18)

    # Título
    title = "TOP CRIADORES DO DIA"
    tb = draw.textbbox((0, 0), title, font=f30)
    draw.text(((W - (tb[2] - tb[0])) // 2, 14), title, font=f30, fill=(255, 215, 0))

    # Data
    date_txt = agora.strftime("%d/%m/%Y")
    db2 = draw.textbbox((0, 0), date_txt, font=f17)
    draw.text(((W - (db2[2] - db2[0])) // 2, 52), date_txt, font=f17, fill=(136, 153, 166))

    # Linha divisória
    draw.rectangle([50, 86, W - 50, 88], fill=(40, 40, 50))

    # Cards
    col_colors = [(255, 215, 0), (192, 192, 192), (205, 127, 50)]
    pos_labels = ["1 LUGAR", "2 LUGAR", "3 LUGAR"]
    card_margin = 18
    card_gap = 10
    card_w = (W - 2 * card_margin - 2 * card_gap) // 3
    card_y_start = 96
    card_h = H - card_y_start - card_margin

    for i in range(3):
        cx = card_margin + i * (card_w + card_gap)
        cy = card_y_start
        c = col_colors[i]

        u = top3[i] if i < len(top3) else None
        nome = ((u.get("user_nome") or "---")[:18]) if u else "---"
        total = u.get("total", 0) if u else 0
        s_salas = premios_salas[i] if i < len(premios_salas) else 0
        s_reais = premios_reais[i] if i < len(premios_reais) else 0.0

        # Fundo do card
        draw.rounded_rectangle(
            [cx, cy, cx + card_w, cy + card_h],
            radius=10, fill=(20, 26, 34), outline=c, width=2,
        )

        # Badge de posição
        badge_h = 42
        draw.rounded_rectangle([cx, cy, cx + card_w, cy + badge_h], radius=10, fill=c)
        draw.rectangle([cx, cy + badge_h // 2, cx + card_w, cy + badge_h], fill=c)

        f_lbl = _f(18)
        lb = draw.textbbox((0, 0), pos_labels[i], font=f_lbl)
        lw, lh = lb[2] - lb[0], lb[3] - lb[1]
        draw.text(
            (cx + (card_w - lw) // 2, cy + (badge_h - lh) // 2 - 1),
            pos_labels[i], font=f_lbl, fill=(0, 0, 0),
        )

        iy = cy + badge_h + 10

        # Nome
        nb = draw.textbbox((0, 0), nome, font=f18)
        nw, nh = nb[2] - nb[0], nb[3] - nb[1]
        draw.text((cx + (card_w - nw) // 2, iy), nome, font=f18, fill=(255, 255, 255))
        iy += nh + 8

        # Salas criadas hoje
        sc = f"{total} sala{'s' if total != 1 else ''} hoje"
        sb = draw.textbbox((0, 0), sc, font=f15)
        sw, sh = sb[2] - sb[0], sb[3] - sb[1]
        draw.text((cx + (card_w - sw) // 2, iy), sc, font=f15, fill=(136, 153, 166))
        iy += sh + 12

        # Divisória interna
        draw.rectangle([cx + 16, iy, cx + card_w - 16, iy + 1], fill=(40, 40, 50))
        iy += 10

        # Label "PREMIO"
        lbl = "PREMIO"
        lb2 = draw.textbbox((0, 0), lbl, font=f14)
        lw2 = lb2[2] - lb2[0]
        draw.text((cx + (card_w - lw2) // 2, iy), lbl, font=f14, fill=(90, 90, 100))
        iy += 22

        # Salas
        ps = f"+{s_salas} salas"
        psb = draw.textbbox((0, 0), ps, font=f20)
        psw, psh = psb[2] - psb[0], psb[3] - psb[1]
        draw.text((cx + (card_w - psw) // 2, iy), ps, font=f20, fill=c)
        iy += psh + 6

        # R$
        pr = f"+ R$ {s_reais:.0f}"
        prb = draw.textbbox((0, 0), pr, font=f16)
        prw = prb[2] - prb[0]
        draw.text((cx + (card_w - prw) // 2, iy), pr, font=f16, fill=(87, 242, 135))

    # Barra dourada no rodapé
    draw.rectangle([0, H - 6, W, H], fill=(255, 215, 0))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


# ── Embeds ────────────────────────────────────────────────────────────────────

def _build_ranking_embed(top10: list, premios: dict = None) -> discord.Embed:
    if premios is None:
        premios = {"salas": list(PREMIOS_SALAS_DEFAULT), "reais": list(PREMIOS_REAIS_DEFAULT)}
    premios_salas = premios["salas"]

    em = discord.Embed(title="Ranking Diário — Top 5", color=0xFFD700)

    cabecalho = (
        f"{_em_str('calendario')}  **Hoje: {_hoje_str()}**\n"
        "Quem criar mais salas hoje entra no top!\n"
        f"{_em_str('presente')} **Top 3 recebe prêmios todo dia às 23:59 BRT.**\n\n"
    )

    if not top10:
        em.description = cabecalho + f"{_em_str('off')} *Nenhuma sala criada hoje ainda. Seja o primeiro!*"
    else:
        linhas = []
        for idx, u in enumerate(top10[:5]):
            badge = _pos_emoji(idx) if idx < 3 else _em_str("dot")
            nome  = (u.get("user_nome") or "Desconhecido")[:24]
            total = u.get("total", 0)
            s = "s" if total != 1 else ""
            if idx < 3:
                premio_txt = f"  ╸ **+{premios_salas[idx]} salas**"
            else:
                premio_txt = ""
            linhas.append(f"{badge} **{idx+1}º** **{nome}** — {total} sala{s}{premio_txt}")
        em.description = cabecalho + "\n".join(linhas)

    prizes = "\n".join(
        f"{_pos_emoji(i)} **{i+1}º lugar** → +{premios_salas[i]} salas"
        for i in range(3)
    )
    em.add_field(name=f"{GIFT}  Prêmios (todo dia às 23:59)", value=prizes, inline=False)
    em.set_footer(text=f"Próximo reset: {_proximo_reset_str()}  •  Contagem desde meia-noite BRT")
    return em


def _build_ranking_v2_payload(top10: list = None) -> dict:
    """Painel público inicial em Components V2 — cabeçalho + botões."""
    components = [{
        "id": 1,
        "type": 17,
        "accent_color": 0xFFD700,
        "components": [
            {
                "id": 2, "type": 10,
                "content": f"## {_em_str('top')}  Ranking Diário — Top Criadores de Sala",
            },
            {
                "id": 3, "type": 10,
                "content": (
                    f"{_em_str('calendario')}  **Hoje:** {_hoje_str()}\n"
                    "-# Quem criar mais salas hoje entra no top — **Top 3** recebe prêmios todo dia às 23:59 BRT.\n"
                    "-# Clique em **Ranking** para ver a tabela completa, ou **Meu Perfil** para ver sua posição."
                ),
            },
            {
                "id": 4, "type": 1,
                "components": [
                    {
                        "type": 2, "style": 1,
                        "label": "Ranking",
                        "emoji": _emj("top"),
                        "custom_id": "ranking:atualizar",
                    },
                    {
                        "type": 2, "style": 2,
                        "label": "Meu Perfil",
                        "emoji": _emj("info"),
                        "custom_id": "ranking:perfil",
                    },
                ],
            },
        ],
    }]
    return {"flags": 32768, "components": components}


def _build_ranking_tabela_v2_payload(top10: list, premios: dict = None) -> dict:
    """Container V2 ephemeral com a tabela top 5 + prêmios + rodapé."""
    if premios is None:
        premios = {"salas": list(PREMIOS_SALAS_DEFAULT), "reais": list(PREMIOS_REAIS_DEFAULT)}
    premios_salas = premios["salas"]

    if not top10:
        linhas_top = (
            f"{_em_str('off')}  *Nenhuma sala criada hoje ainda.*\n"
            "-# Seja o primeiro a entrar no ranking!"
        )
    else:
        linhas = []
        for idx, u in enumerate(top10[:5]):
            nome  = (u.get("user_nome") or "Desconhecido")[:24]
            total = u.get("total", 0)
            s = "s" if total != 1 else ""
            badge = _pos_emoji(idx) if idx < 3 else _em_str("dot")
            if idx < 3:
                premio_txt = f" ╸ **+{premios_salas[idx]} salas**"
            else:
                premio_txt = ""
            linhas.append(f"{badge} **{idx+1}º** **{nome}** ╸ {total} sala{s}{premio_txt}")
        linhas_top = "\n".join(linhas)

    prizes_txt = "\n".join(
        f"{_pos_emoji(i)} **{i+1}º lugar** ╸ +{premios_salas[i]} salas"
        for i in range(3)
    )

    components = [{
        "id": 1,
        "type": 17,
        "accent_color": 0xFFD700,
        "components": [
            {"id": 2, "type": 10, "content": f"## {_em_str('top')}  Ranking Diário — Top 5"},
            {"id": 3, "type": 10, "content": f"{_em_str('calendario')}  **Hoje:** {_hoje_str()}"},
            {"id": 4, "type": 14, "divider": True, "spacing": 2},
            {"id": 5, "type": 10, "content": linhas_top},
            {"id": 6, "type": 14, "divider": True, "spacing": 2},
            {
                "id": 7, "type": 10,
                "content": f"### {_em_str('presente')}  Prêmios — todo dia às 23:59 BRT\n{prizes_txt}",
            },
            {"id": 8, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 9, "type": 10,
                "content": (
                    f"-# {_em_str('clockcheck')} Próximo reset: **{_proximo_reset_str()}**  •  "
                    "Contagem desde meia-noite BRT"
                ),
            },
        ],
    }]
    return {"flags": 64 | 32768, "components": components}


def _build_config_embed() -> discord.Embed:
    from utils.database import ranking_canal_anuncio_get, ranking_ativo_get, ranking_premios_get
    canal_id = ranking_canal_anuncio_get()
    ativo    = ranking_ativo_get()
    premios  = ranking_premios_get()
    premios_salas = premios["salas"]
    premios_reais = premios["reais"]

    canal_txt = f"<#{canal_id}>" if canal_id else f"{OFF} *Não definido*"
    status    = f"{ON} **Ativo**" if ativo else f"{OFF} **Desativado**"

    prizes = "\n".join(
        f"{_pos_emoji(i)} {i+1}º lugar → +{premios_salas[i]} salas"
        for i in range(3)
    )

    em = discord.Embed(title=f"{TOP}  Ranking Diário — Configuração", color=0xFFD700)
    em.description = (
        f"{DOT} **Status:** {status}\n"
        f"{DOT} **Canal de anúncio:** {canal_txt}\n"
        f"{DOT} **Reset automático:** todo dia às 23:59 BRT\n"
        f"{DOT} **Próximo reset:** {_proximo_reset_str()}\n\n"
        f"**Prêmios (configuráveis via botão abaixo):**\n{prizes}"
    )
    em.set_footer(text="Prêmios em salas são creditados automaticamente. R$ são pagos manualmente pelo admin.")
    return em


# ── Helpers Components V2 ────────────────────────────────────────────────────

async def _respond_v2_initial(inter_id: int, inter_token: str, payload: dict) -> bool:
    url = f"https://discord.com/api/v10/interactions/{inter_id}/{inter_token}/callback"
    body = {"type": 4, "data": payload}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
                ok = r.status in (200, 204)
                if not ok:
                    _log.warning(f"[ranking v2 respond] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[ranking v2 respond] {ex}")
        return False


def _emj(key: str):
    em = PE.get(key)
    if not em:
        return None
    return {"id": str(em.id), "name": em.name, "animated": em.animated}


# ── Anúncio dos vencedores em Components V2 (com imagem do pódio) ──────────────

def _build_anuncio_v2_payload(top3: list, premios_salas: list, premios_reais: list,
                              dia_str: str, ephemeral: bool = False,
                              com_imagem: bool = True, filename: str = "ranking_top3.png") -> dict:
    """Container V2 do anúncio diário: título, imagem do pódio e lista de vencedores.
    Usa exclusivamente emojis do bot."""
    if top3:
        linhas = []
        for idx, u in enumerate(top3[:3]):
            badge   = _pos_emoji(idx)
            uid     = u.get("user_id", "?")
            nome    = (u.get("user_nome") or "Desconhecido")[:24]
            total   = u.get("total", 0)
            s       = "s" if total != 1 else ""
            s_salas = premios_salas[idx] if idx < len(premios_salas) else 0
            s_reais = premios_reais[idx] if idx < len(premios_reais) else 0.0
            linhas.append(
                f"{badge} **{idx+1}º** <@{uid}> ╸ {total} sala{s} criada{s}\n"
                f"-# {_em_str('presente')} +{s_salas} salas"
            )
        corpo = "\n".join(linhas)
    else:
        corpo = f"{_em_str('off')}  *Nenhum participante hoje.*"

    inner = [
        {"id": 2, "type": 10, "content": f"## {_em_str('verified')}  Ranking Diário — Vencedores!"},
        {"id": 3, "type": 10, "content": f"{_em_str('calendario')}  **Dia:** {dia_str}"},
    ]
    if com_imagem:
        inner.append({
            "id": 4, "type": 12,  # Media Gallery
            "items": [{"media": {"url": f"attachment://{filename}"}}],
        })
    inner += [
        {"id": 5, "type": 14, "divider": True, "spacing": 2},
        {"id": 6, "type": 10, "content": corpo},
        {"id": 7, "type": 14, "divider": True, "spacing": 1},
        {
            "id": 8, "type": 10,
            "content": (
                f"-# {_em_str('clockcheck')} As salas já foram creditadas automaticamente.  "
                f"Próximo reset: **{_proximo_reset_str()}**"
            ),
        },
    ]

    components = [{"id": 1, "type": 17, "accent_color": 0xFFD700, "components": inner}]

    flags = 32768  # IS_COMPONENTS_V2
    if ephemeral:
        flags |= 64

    payload = {"flags": flags, "components": components}
    if com_imagem:
        payload["attachments"] = [{"id": 0, "filename": filename}]
    return payload


async def _post_v2_canal(channel_id: int, payload: dict, img_bytes: bytes = None,
                         filename: str = "ranking_top3.png") -> bool:
    """Posta um payload V2 num canal (via bot token). Se img_bytes, usa multipart."""
    import json as _json
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {config.DISCORD_TOKEN}"}
    try:
        async with aiohttp.ClientSession() as s:
            if img_bytes:
                form = aiohttp.FormData()
                form.add_field("payload_json", _json.dumps(payload), content_type="application/json")
                form.add_field("files[0]", img_bytes, filename=filename, content_type="image/png")
                req = s.post(url, data=form, headers=headers, timeout=aiohttp.ClientTimeout(total=20))
            else:
                h = {**headers, "Content-Type": "application/json"}
                req = s.post(url, json=payload, headers=h, timeout=aiohttp.ClientTimeout(total=20))
            async with req as r:
                ok = r.status in (200, 201)
                if not ok:
                    _log.warning(f"[ranking v2 canal] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[ranking v2 canal] {ex}")
        return False


async def _followup_v2(app_id: int, token: str, payload: dict, img_bytes: bytes = None,
                       filename: str = "ranking_previa.png") -> bool:
    """Envia um followup de interaction com payload V2 (via webhook). Suporta imagem (multipart)."""
    import json as _json
    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}"
    try:
        async with aiohttp.ClientSession() as s:
            if img_bytes:
                form = aiohttp.FormData()
                form.add_field("payload_json", _json.dumps(payload), content_type="application/json")
                form.add_field("files[0]", img_bytes, filename=filename, content_type="image/png")
                req = s.post(url, data=form, timeout=aiohttp.ClientTimeout(total=20))
            else:
                req = s.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=20))
            async with req as r:
                ok = r.status in (200, 201)
                if not ok:
                    _log.warning(f"[ranking v2 followup] {r.status} {(await r.text())[:200]}")
                return ok
    except Exception as ex:
        _log.error(f"[ranking v2 followup] {ex}")
        return False


def _build_perfil_v2_payload(user, posicao, salas_hoje, saldo, total_participantes) -> dict:
    nome = user.display_name

    if posicao is None:
        pos_txt = (
            f"{_em_str('off')} **Você ainda não criou salas hoje**\n"
            "-# Crie sua primeira sala pra entrar no ranking!"
        )
    else:
        if posicao == 1:
            medalha = _em_str("verified")
        elif posicao == 2:
            medalha = _em_str("top")
        elif posicao == 3:
            medalha = _em_str("swordbattle")
        else:
            medalha = _em_str("dot")
        s = "s" if salas_hoje != 1 else ""
        pos_txt = (
            f"{medalha} **{posicao}º lugar** de {total_participantes} participantes\n"
            f"-# Você criou **{salas_hoje} sala{s}** hoje"
        )

    components = [{
        "id": 1,
        "type": 17,
        "accent_color": 0xFFD700,
        "components": [
            {"id": 2, "type": 10, "content": f"## {_em_str('top')}  Perfil de {nome}"},
            {"id": 3, "type": 14, "divider": True, "spacing": 1},
            {"id": 4, "type": 10, "content": pos_txt},
            {"id": 5, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 6, "type": 10,
                "content": (
                    f"{_em_str('carteira')}  **Saldo Atual**\n"
                    f"-# Você possui **{saldo} sala(s)** disponíveis"
                ),
            },
            {"id": 7, "type": 14, "divider": True, "spacing": 1},
            {
                "id": 8, "type": 10,
                "content": (
                    f"{_em_str('gift')}  **Próximo Reset**\n"
                    f"-# Todo dia às 23:59 BRT — próximo: **{_proximo_reset_str()}**"
                ),
            },
        ],
    }]
    return {"flags": 64 | 32768, "components": components}


# ── Modal de Configuração de Prêmios ─────────────────────────────────────────

class _ModalPremiosRanking(discord.ui.Modal, title="Configurar Prêmios do Ranking"):
    salas_input = discord.ui.TextInput(
        label="Salas — Top1, Top2, Top3 (vírgula)",
        placeholder="100,80,50",
        required=True,
        max_length=30,
    )
    reais_input = discord.ui.TextInput(
        label="R$ — Top1, Top2, Top3 (vírgula)",
        placeholder="7,5,4",
        required=True,
        max_length=30,
    )

    async def on_submit(self, inter: discord.Interaction):
        try:
            def _parse_ints(s):
                return [int(x.strip()) for x in s.split(",") if x.strip().isdigit()]

            def _parse_floats(s):
                result = []
                for x in s.split(","):
                    x = x.strip().replace(",", ".")
                    try:
                        result.append(float(x))
                    except ValueError:
                        pass
                return result

            salas_list = _parse_ints(self.salas_input.value)
            reais_list = _parse_floats(self.reais_input.value)

            if len(salas_list) < 3 or len(reais_list) < 3:
                return await inter.response.send_message(
                    embed=_err(
                        "Formato inválido.",
                        "Use 3 valores separados por vírgula.\n"
                        "Salas: `100,80,50`\nReais: `7,5,4`",
                    ),
                    ephemeral=True,
                )

            from utils.database import ranking_premios_set
            await asyncio.to_thread(ranking_premios_set, salas_list[:3], reais_list[:3])
            await inter.response.send_message(
                embed=_ok(
                    "Prêmios atualizados!",
                    f"{_pos_emoji(0)} Top1: **{salas_list[0]} salas** + **R${reais_list[0]:.0f}**\n"
                    f"{_pos_emoji(1)} Top2: **{salas_list[1]} salas** + **R${reais_list[1]:.0f}**\n"
                    f"{_pos_emoji(2)} Top3: **{salas_list[2]} salas** + **R${reais_list[2]:.0f}**",
                ),
                ephemeral=True,
            )
        except Exception as ex:
            _log.error(f"[ModalPremios] {ex}")
            await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)


# ── Views ─────────────────────────────────────────────────────────────────────

class RankingPublicoView(discord.ui.View):
    """View persistente postada no canal — botões Ranking e Meu Perfil."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ranking",
        emoji=PE["top"],
        style=discord.ButtonStyle.primary,
        custom_id="ranking:atualizar",
    )
    async def btn_ranking(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            await inter.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass
        try:
            from utils.database import top_criadores_hoje, ranking_premios_get
            top10   = await asyncio.to_thread(top_criadores_hoje, 10)
            premios = await asyncio.to_thread(ranking_premios_get)
            payload = _build_ranking_tabela_v2_payload(top10, premios)
            ok = await _followup_v2(inter.application_id, inter.token, payload)
            if not ok:
                # Fallback: embed normal com os dados do ranking
                try:
                    em = _build_ranking_embed(top10, premios)
                    await inter.followup.send(embed=em, ephemeral=True)
                except Exception:
                    await inter.followup.send(embed=_err("Erro ao carregar ranking."), ephemeral=True)
        except Exception as ex:
            _log.warning(f"[ranking:atualizar] {ex}")
            try:
                await inter.followup.send(
                    embed=_err("Erro ao carregar ranking.", f"`{ex}`"), ephemeral=True
                )
            except Exception:
                pass

    @discord.ui.button(
        label="Meu Perfil",
        emoji=PE["info"],
        style=discord.ButtonStyle.secondary,
        custom_id="ranking:perfil",
    )
    async def btn_perfil(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            await inter.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass
        try:
            from utils.database import top_criadores_hoje, saldo_total_usuario
            uid = str(inter.user.id)

            todos = await asyncio.to_thread(top_criadores_hoje, 0)
            saldo = await asyncio.to_thread(saldo_total_usuario, uid)

            posicao = None
            salas_hoje = 0
            for idx, u in enumerate(todos, start=1):
                if str(u.get("user_id")) == uid:
                    posicao = idx
                    salas_hoje = u.get("total", 0)
                    break

            payload = _build_perfil_v2_payload(inter.user, posicao, salas_hoje, saldo, len(todos))
            ok = await _followup_v2(inter.application_id, inter.token, payload)
            if not ok:
                # Fallback: embed normal com dados do perfil
                try:
                    em = discord.Embed(title=f"{TOP}  Perfil de {inter.user.display_name}", color=0xFFD700)
                    if posicao:
                        badge = _pos_emoji(posicao - 1) if posicao <= 3 else _em_str("dot")
                        s = "s" if salas_hoje != 1 else ""
                        em.description = (
                            f"{badge} **{posicao}º lugar** de {len(todos)} participantes\n"
                            f"Você criou **{salas_hoje} sala{s}** hoje\n\n"
                            f"Saldo: **{saldo} sala(s)**"
                        )
                    else:
                        em.description = f"Você ainda não criou salas hoje.\n\nSaldo: **{saldo} sala(s)**"
                    await inter.followup.send(embed=em, ephemeral=True)
                except Exception:
                    await inter.followup.send(embed=_err("Erro ao carregar perfil."), ephemeral=True)
        except Exception as ex:
            _log.warning(f"[ranking:perfil] {ex}")
            try:
                await inter.followup.send(
                    embed=_err("Erro ao carregar perfil.", f"`{ex}`"), ephemeral=True
                )
            except Exception:
                pass


class RankingConfigView(discord.ui.View):
    """Sub-painel de configuração (aberto via /botconfig → Ranking Diário)."""

    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Ativar / Desativar", emoji="🔁", style=discord.ButtonStyle.success, row=0)
    async def btn_toggle(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            from utils.database import ranking_ativo_get, ranking_ativo_set
            novo = not await asyncio.to_thread(ranking_ativo_get)
            await asyncio.to_thread(ranking_ativo_set, novo)
            st = "ativado ✅" if novo else "desativado ❌"
            em_cfg = await asyncio.to_thread(_build_config_embed)
            await inter.response.edit_message(embed=em_cfg, view=self)
            await inter.followup.send(embed=_ok(f"Ranking diário {st}!"), ephemeral=True)
        except Exception as ex:
            _log.error(f"[ranking:toggle] {ex}")
            try:
                await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Canal de Anúncio", emoji="📢", style=discord.ButtonStyle.primary, row=0)
    async def btn_canal(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            em = _info(
                "Canal de Anúncio do Ranking",
                "Selecione o canal onde o bot vai anunciar os vencedores todo dia às 23:59 BRT.",
            )
            await inter.response.send_message(embed=em, view=_RankingCanalSelectView(), ephemeral=True)
        except Exception as ex:
            _log.error(f"[ranking:canal] {ex}")

    @discord.ui.button(label="Ver Top 3 Atual", emoji="📊", style=discord.ButtonStyle.secondary, row=0)
    async def btn_top3(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            await inter.response.defer(ephemeral=True)
            from utils.database import top_criadores_hoje, ranking_premios_get
            top3    = await asyncio.to_thread(top_criadores_hoje, 3)
            premios = await asyncio.to_thread(ranking_premios_get)
            em      = _build_ranking_embed(top3, premios)
            em.title = "📊  Top 3 Atual (Prévia Admin)"
            em.color = config.COR_INFO
            await inter.followup.send(embed=em, ephemeral=True)
        except Exception as ex:
            _log.error(f"[ranking:top3] {ex}")
            try:
                await inter.followup.send(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Configurar Premios", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def btn_premios(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            await inter.response.send_modal(_ModalPremiosRanking())
        except Exception as ex:
            _log.error(f"[ranking:premios_btn] {ex}")
            try:
                await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="Ver Previa Top 3", emoji="🖼️", style=discord.ButtonStyle.primary, row=1)
    async def btn_previa(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            await inter.response.defer(ephemeral=True)
            from utils.database import top_criadores_hoje, ranking_premios_get
            top3    = await asyncio.to_thread(top_criadores_hoje, 3)
            premios = await asyncio.to_thread(ranking_premios_get)
            premios_salas = premios["salas"]
            premios_reais = premios["reais"]
            img_bytes = await asyncio.to_thread(
                _gerar_imagem_top3, top3, premios_salas, premios_reais
            )
            dia_str = datetime.now(_BR).strftime("%d/%m/%Y")
            payload = _build_anuncio_v2_payload(
                top3, premios_salas, premios_reais, dia_str,
                ephemeral=True, com_imagem=bool(img_bytes), filename="ranking_previa.png",
            )
            app_id = inter.client.application_id
            ok = await _followup_v2(app_id, inter.token, payload, img_bytes, "ranking_previa.png")
            if not ok:
                await inter.followup.send(
                    embed=_err("Não foi possível gerar a prévia.", "Veja os logs."),
                    ephemeral=True,
                )
        except Exception as ex:
            _log.error(f"[ranking:previa] {ex}")
            try:
                await inter.followup.send(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="⚡ Forçar Reset Agora", style=discord.ButtonStyle.danger, row=2)
    async def btn_reset(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            await inter.response.defer(ephemeral=True)
            from utils.database import (
                top_criadores_hoje, distribuir_premios_diario,
                ranking_canal_anuncio_get, ranking_ultimo_reset_set,
                ranking_premios_get,
            )
            top3 = await asyncio.to_thread(top_criadores_hoje, 3)
            if not top3:
                return await inter.followup.send(
                    embed=_err("Nenhum participante hoje."), ephemeral=True
                )

            premios       = await asyncio.to_thread(ranking_premios_get)
            premios_salas = premios["salas"]
            premios_reais = premios["reais"]
            resultados    = await asyncio.to_thread(distribuir_premios_diario, top3)

            canal_id = await asyncio.to_thread(ranking_canal_anuncio_get)
            if canal_id:
                dia_str   = datetime.now(_BR).strftime("%d/%m/%Y")
                img_bytes = await asyncio.to_thread(
                    _gerar_imagem_top3, top3, premios_salas, premios_reais
                )
                payload = _build_anuncio_v2_payload(
                    top3, premios_salas, premios_reais, dia_str,
                    com_imagem=bool(img_bytes), filename="ranking_top3.png",
                )
                await _post_v2_canal(int(canal_id), payload, img_bytes, "ranking_top3.png")

            await asyncio.to_thread(ranking_ultimo_reset_set, datetime.now(_BR).isoformat())

            linhas = [
                f"{_pos_emoji(i)} **{nome}** → +{premio} salas"
                for i, (uid, nome, premio) in enumerate(resultados)
            ]
            await inter.followup.send(
                embed=_ok(f"Reset executado! {len(resultados)} premiado(s).", "\n".join(linhas)),
                ephemeral=True,
            )
        except Exception as ex:
            _log.error(f"[ranking:reset] {ex}")
            try:
                await inter.followup.send(embed=_err("Erro ao executar reset.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass


class _RankingCanalSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        placeholder="Selecione o canal de anúncio…",
        channel_types=[discord.ChannelType.text],
        row=0,
    )
    async def sel_canal(self, inter: discord.Interaction, sel: discord.ui.ChannelSelect):
        try:
            canal = sel.values[0]
            from utils.database import ranking_canal_anuncio_set
            await asyncio.to_thread(ranking_canal_anuncio_set, canal.id)
            await inter.response.send_message(
                embed=_ok(f"Canal de anúncio definido: {canal.mention}"), ephemeral=True
            )
        except Exception as ex:
            await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)

    @discord.ui.button(label="Limpar", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def btn_limpar(self, inter: discord.Interaction, btn: discord.ui.Button):
        try:
            from utils.database import ranking_canal_anuncio_set
            await asyncio.to_thread(ranking_canal_anuncio_set, None)
            await inter.response.send_message(embed=_ok("Canal de anúncio removido."), ephemeral=True)
        except Exception as ex:
            await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)


# ── Funções públicas (chamadas de cogs/main.py e cogs/botconfig.py) ──────────

async def postar_ranking_canal(inter: discord.Interaction):
    """Posta o painel de ranking público (Components V2) no canal atual."""
    try:
        if not _is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)
        from utils.database import top_criadores_hoje
        top10   = await asyncio.to_thread(top_criadores_hoje, 10)
        payload = _build_ranking_v2_payload(top10)

        token   = config.DISCORD_TOKEN
        url     = f"https://discord.com/api/v10/channels/{inter.channel.id}/messages"
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status not in (200, 201):
                    body = (await r.text())[:300]
                    _log.error(f"[postar_ranking_canal] {r.status} {body}")
                    return await inter.followup.send(
                        embed=_err("Erro ao postar ranking.", f"HTTP {r.status}: `{body[:200]}`"),
                        ephemeral=True,
                    )

        await inter.followup.send(embed=_ok("Painel de ranking diário postado no canal!"), ephemeral=True)
    except Exception as ex:
        _log.error(f"[postar_ranking_canal] {ex}")
        try:
            await inter.followup.send(embed=_err("Erro ao postar ranking.", f"`{ex}`"), ephemeral=True)
        except Exception:
            try:
                await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass


async def ver_ranking_ephemeral(inter: discord.Interaction):
    """Mostra o top 10 atual só para o admin (ephemeral)."""
    try:
        if not _is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)
        from utils.database import top_criadores_hoje, ranking_premios_get
        top10   = await asyncio.to_thread(top_criadores_hoje, 10)
        premios = await asyncio.to_thread(ranking_premios_get)
        em      = _build_ranking_embed(top10, premios)
        em.title = "📊  Top 10 Atual (Prévia Admin)"
        em.color = config.COR_INFO
        await inter.followup.send(embed=em, ephemeral=True)
    except Exception as ex:
        _log.error(f"[ver_ranking_ephemeral] {ex}")
        try:
            await inter.followup.send(embed=_err("Erro ao buscar ranking.", f"`{ex}`"), ephemeral=True)
        except Exception:
            try:
                await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass


async def abrir_config_ranking(inter: discord.Interaction):
    """Abre painel de configuração do ranking (chamado do /botconfig)."""
    try:
        if not _is_admin(inter.user.id):
            return await inter.response.send_message(embed=_err("Sem permissão."), ephemeral=True)
        await inter.response.defer(ephemeral=True)
        em = await asyncio.to_thread(_build_config_embed)
        await inter.followup.send(embed=em, view=RankingConfigView(), ephemeral=True)
    except Exception as ex:
        _log.error(f"[abrir_config_ranking] {ex}")
        try:
            await inter.followup.send(embed=_err("Erro ao abrir config.", f"`{ex}`"), ephemeral=True)
        except Exception:
            try:
                await inter.response.send_message(embed=_err("Erro.", f"`{ex}`"), ephemeral=True)
            except Exception:
                pass


# ── Cog principal ─────────────────────────────────────────────────────────────

class RankingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._check_task.start()

    def cog_unload(self):
        self._check_task.cancel()

    @tasks.loop(minutes=1)
    async def _check_task(self):
        """Verifica todo minuto se é 23:59 BRT para fazer o reset diário."""
        await self.bot.wait_until_ready()
        try:
            from utils.database import ranking_ativo_get, ranking_ultimo_reset_get, ranking_ultimo_reset_set

            if not await asyncio.to_thread(ranking_ativo_get):
                return

            agora = datetime.now(_BR)
            if not (agora.hour == 23 and agora.minute == 59):
                return

            hoje_str = agora.strftime("%Y-%m-%d")
            ultimo   = await asyncio.to_thread(ranking_ultimo_reset_get)
            if ultimo and ultimo[:10] >= hoje_str:
                return  # já fez o reset hoje

            _log.info("[ranking] Iniciando reset diário automático…")
            await self._executar_reset()
            await asyncio.to_thread(ranking_ultimo_reset_set, datetime.now(_BR).isoformat())

        except Exception as ex:
            _log.error(f"[ranking._check_task] {ex}")

    @_check_task.before_loop
    async def _before_check(self):
        await self.bot.wait_until_ready()

    async def _executar_reset(self):
        try:
            from utils.database import (
                top_criadores_hoje, distribuir_premios_diario,
                ranking_canal_anuncio_get, ranking_premios_get,
            )

            top3 = await asyncio.to_thread(top_criadores_hoje, 3)
            if not top3:
                _log.info("[ranking] Reset diário: nenhum participante hoje.")
                return

            premios       = await asyncio.to_thread(ranking_premios_get)
            premios_salas = premios["salas"]
            premios_reais = premios["reais"]

            resultados = await asyncio.to_thread(distribuir_premios_diario, top3)
            _log.info(f"[ranking] Prêmios distribuídos: {resultados}")

            canal_id = await asyncio.to_thread(ranking_canal_anuncio_get)
            if not canal_id:
                _log.warning("[ranking] Canal de anúncio não configurado.")
                return

            dia_str   = datetime.now(_BR).strftime("%d/%m/%Y")
            img_bytes = await asyncio.to_thread(_gerar_imagem_top3, top3, premios_salas, premios_reais)
            payload   = _build_anuncio_v2_payload(
                top3, premios_salas, premios_reais, dia_str,
                com_imagem=bool(img_bytes), filename="ranking_top3.png",
            )
            ok = await _post_v2_canal(int(canal_id), payload, img_bytes, "ranking_top3.png")
            _log.info(f"[ranking] Anúncio diário V2 enviado no canal {canal_id} (ok={ok})")

        except Exception as ex:
            _log.error(f"[ranking._executar_reset] {ex}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RankingCog(bot))
    bot.add_view(RankingPublicoView())  # registra a view persistente pra os botões sobreviverem ao restart
