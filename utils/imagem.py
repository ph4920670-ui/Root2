# utils/imagem.py — Overlay do ID na imagem da sala

from io import BytesIO
import os

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

_FONT_DIR = "/usr/share/fonts/truetype/dejavu"
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BERMUDA  = os.path.join(_BASE_DIR, "bermuda.jpg")


def _font(name="DejaVuSans-Bold.ttf", size=20):
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
    except Exception:
        return ImageFont.load_default()


def gerar_imagem_sala(sala_id: str, senha: str = "", mapa: str = "BERMUDA",
                       img_bytes: bytes = None) -> bytes | None:
    """
    Gera imagem da sala com ID no canto superior esquerdo.
    Usa imagem da API se disponível, senão usa bermuda.jpg.
    """
    if not _HAS_PIL:
        return None

    W, H = 480, 270

    # ── Fundo: imagem da API ou bermuda.jpg ──
    base = None
    if img_bytes:
        try:
            base = Image.open(BytesIO(img_bytes)).convert("RGBA")
        except Exception:
            pass

    if base is None:
        try:
            base = Image.open(_BERMUDA).convert("RGBA")
        except Exception:
            return None

    base = base.resize((W, H), Image.LANCZOS)

    # ── Overlay: ID no topo esquerdo ──
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font_id = _font("DejaVuSans-Bold.ttf", 15)
    text = f"ID {sala_id}"

    bbox = draw.textbbox((0, 0), text, font=font_id)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Barra escura só atrás do ID
    px, py = 8, 5
    draw.rounded_rectangle(
        (6, 6, 6 + tw + px * 2, 6 + th + py * 2),
        radius=5, fill=(0, 0, 0, 170)
    )

    # Texto
    draw.text((6 + px, 6 + py - 1), text, fill=(255, 255, 255), font=font_id)

    # ── Mapa nome no rodapé ──
    font_map = _font("DejaVuSans-Bold.ttf", 13)
    mapa_txt = (mapa or "BERMUDA").upper()
    mbbox = draw.textbbox((0, 0), mapa_txt, font=font_map)
    mtw = mbbox[2] - mbbox[0]
    mth = mbbox[3] - mbbox[1]

    mpx, mpy = 12, 5
    bar_x = (W - mtw - mpx * 2) // 2
    draw.rounded_rectangle(
        (bar_x, H - 6 - mth - mpy * 2, bar_x + mtw + mpx * 2, H - 6),
        radius=5, fill=(0, 0, 0, 180)
    )
    draw.text((bar_x + mpx, H - 6 - mth - mpy), mapa_txt, fill=(255, 255, 255), font=font_map)

    # ── Borda fina ──
    draw.rounded_rectangle((2, 2, W - 2, H - 2), radius=10,
                           outline=(255, 255, 255, 30), width=1)

    # ── Composição ──
    result = Image.alpha_composite(base, overlay)

    buf = BytesIO()
    result.convert("RGB").save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf.read()
