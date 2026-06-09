"""
utils/render_cards.py — geração de cards PNG (Pillow) para o serviço de render.

Este módulo é o "serviço de render" que o selfbot chama via
POST /render/sala  e  POST /render/resultado (ver main.py). Em vez de Puppeteer/
Chromium (que não cabe nos 512MB do Discloud junto com Flask+gunicorn+IMAP),
desenhamos os cards direto com Pillow — mesmo visual dark-neon que o bot já usa
como fallback, só que agora centralizado aqui. Sem dependência de libs de
sistema (libcairo etc.), então não há risco de quebrar o boot no Discloud.

Contrato (idêntico ao que o bot envia):
  sala      -> {eq1:[nome|{nome,id}], eq2:[...], total:"3/8", sala:"123"}  -> PNG
  resultado -> {nome1, placar1, jogs1:[{nome,sub,kda,dmg,mvp}],
                nome2, placar2, jogs2, sala, modo}                        -> PNG

Funções públicas:
  gerar_sala(payload)      -> bytes PNG  (ou None se Pillow indisponível)
  gerar_resultado(payload) -> bytes PNG  (ou None)
"""

import io


# ───────────────────────── helpers de desenho ─────────────────────────

def _img_grad_diag(w_, h_, c1, c2):
    """Gradiente diagonal (sup-esq -> inf-dir)."""
    from PIL import Image, ImageDraw
    base = Image.new('RGB', (w_, h_), c1)
    top = Image.new('RGB', (w_, h_), c2)
    mask = Image.new('L', (w_, h_)); md = ImageDraw.Draw(mask)
    md_max = max(1, w_ + h_)
    for y in range(h_):
        v0 = int(255 * y / md_max)
        for seg in range(0, w_, 8):
            v = min(255, v0 + int(255 * seg / md_max))
            md.line([(seg, y), (min(w_, seg + 8), y)], fill=v)
    base.paste(top, (0, 0), mask)
    return base


def _img_font(sz, bold=False):
    base = '/usr/share/fonts/truetype/dejavu/'
    for c in [base + ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'),
              ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')]:
        try:
            from PIL import ImageFont
            return ImageFont.truetype(c, sz)
        except Exception:
            continue
    try:
        from PIL import ImageFont
        return ImageFont.load_default()
    except Exception:
        return None


def _img_fundo_premium(W, H, base_rgb=(15, 17, 26)):
    """Fundo dark premium: gradiente vertical sutil."""
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (W, H), base_rgb)
    d = ImageDraw.Draw(img)
    for y in range(H):
        f = y / max(1, H)
        r = int(base_rgb[0] + (1 - f) * 10)
        g = int(base_rgb[1] + (1 - f) * 11)
        b = int(base_rgb[2] + (1 - f) * 16)
        d.line([(0, y), (W, y)], fill=(min(255, r), min(255, g), min(255, b)))
    return img


def _glow_rect(img, box, cor, radius=16, passes=5):
    """Glow neon ao redor de um retângulo arredondado."""
    from PIL import Image, ImageDraw
    x0, y0, x1, y1 = box
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(passes, 0, -1):
        a = int(38 * (i / passes))
        od.rounded_rectangle(
            [x0 - i*2, y0 - i*2, x1 + i*2, y1 + i*2],
            radius=radius + i*2, outline=(cor[0], cor[1], cor[2], a), width=2)
    img.paste(Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB'), (0, 0))


def _painel(img, box, fill, radius=14, outline=None, ow=2):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(list(box), radius=radius, fill=fill)
    if outline:
        d.rounded_rectangle(list(box), radius=radius, outline=outline, width=ow)


# ───────────────────────── card de RESULTADO (VS) ─────────────────────────

def _gerar_imagem_resultado_ff(nome1, placar1, jogs1, nome2, placar2, jogs2, sala='', modo=''):
    """Card 'VS' premium. jogs: [{'nome','sub','kda','dmg','mvp'}]. Retorna bytes PNG."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    try:
        def w(d, xy, t, f, fill, anchor=None):
            if f: d.text(xy, t, font=f, fill=fill, anchor=anchor)
        W = 1500
        nlin = max(len(jogs1), len(jogs2), 1)
        HEAD, ROW = 150, 100
        H = HEAD + 60 + nlin * ROW + 70
        img = _img_fundo_premium(W, H, (14, 16, 24))

        AZUL, AZUL2 = (38, 96, 180), (90, 165, 240)
        LARANJA, LARANJA2 = (200, 86, 28), (245, 150, 60)
        DOURADO = (255, 205, 80)
        VERDE = (120, 235, 170)

        img.paste(_img_grad_diag(W//2, HEAD, AZUL2, AZUL), (0, 0))
        img.paste(_img_grad_diag(W//2, HEAD, LARANJA, LARANJA2), (W//2, 0))
        d = ImageDraw.Draw(img)
        d.polygon([(W//2-120, 0), (W//2+120, 0), (W//2+44, HEAD), (W//2-44, HEAD)], fill=(14, 16, 24))
        d.rectangle([0, HEAD, W, HEAD+4], fill=(0, 0, 0))

        w(d, (74, 52), str(nome1)[:22], _img_font(40, True), (255, 255, 255))
        w(d, (W-74, 52), str(nome2)[:22], _img_font(40, True), (255, 255, 255), anchor='ra')

        cx = W//2
        cyc = HEAD//2
        _glow_rect(img, [cx-86, cyc-50, cx+86, cyc+50], DOURADO, radius=10, passes=4)
        d = ImageDraw.Draw(img)
        d.polygon([(cx, 6), (cx+86, cyc), (cx, HEAD-6), (cx-86, cyc)],
                  fill=(14, 16, 24), outline=DOURADO)
        d.polygon([(cx, 6), (cx+86, cyc), (cx, HEAD-6), (cx-86, cyc)], outline=DOURADO)
        w(d, (cx-46, cyc-26), str(placar1), _img_font(44, True), (150, 200, 255), anchor='ma')
        w(d, (cx, cyc-16), "VS", _img_font(24, True), DOURADO, anchor='ma')
        w(d, (cx+46, cyc-26), str(placar2), _img_font(44, True), (255, 180, 120), anchor='ma')

        cy = HEAD+20
        for x0 in (50, W//2+40):
            w(d, (x0+104, cy), "JOGADOR", _img_font(18, True), (140, 150, 168))
            w(d, (x0+452, cy), "K / D / A", _img_font(18, True), (140, 150, 168))
            w(d, (x0+624, cy), "DANO", _img_font(18, True), (140, 150, 168))

        def coluna(x0, jogs, cor_barra, cor_av1, cor_av2):
            y = HEAD+52
            for jg in jogs:
                mvp = jg.get('mvp')
                pbox = [x0, y, x0+710, y+ROW-14]
                if mvp:
                    _glow_rect(img, pbox, DOURADO, radius=12, passes=3)
                dd = ImageDraw.Draw(img)
                dd.rounded_rectangle(pbox, radius=12,
                                     fill=(48, 42, 26) if mvp else (26, 30, 44),
                                     outline=(DOURADO if mvp else (44, 50, 68)), width=2 if mvp else 1)
                dd.rounded_rectangle([x0, y, x0+8, y+ROW-14], radius=4, fill=cor_barra)
                av = _img_grad_diag(60, 60, cor_av1, cor_av2)
                img.paste(av, (x0+22, y+16))
                dd = ImageDraw.Draw(img)
                dd.rounded_rectangle([x0+22, y+16, x0+82, y+76], radius=12, outline=(255, 255, 255, 60), width=1)
                ini = (str(jg.get('nome', '?'))[:1] or '?').upper()
                w(dd, (x0+52, y+26), ini, _img_font(30, True), (255, 255, 255), anchor='ma')
                est = "[MVP] " if mvp else ""
                w(dd, (x0+104, y+18), (est+str(jg.get('nome', '?')))[:20], _img_font(27, True),
                  DOURADO if mvp else (240, 244, 250))
                if jg.get('sub'):
                    w(dd, (x0+104, y+54), f"ID {str(jg['sub'])[:20]}", _img_font(17), (140, 150, 168))
                w(dd, (x0+452, y+30), str(jg.get('kda', '0 / 0 / 0')), _img_font(25, True), (240, 244, 250))
                w(dd, (x0+624, y+30), str(jg.get('dmg', 0)), _img_font(25, True), VERDE)
                y += ROW
        coluna(50, jogs1, (90, 165, 240), (50, 90, 160), (30, 55, 110))
        coluna(W//2+40, jogs2, (245, 150, 60), (150, 80, 35), (95, 50, 22))

        d = ImageDraw.Draw(img)
        d.line([(50, H-54), (W-50, H-54)], fill=(40, 46, 62), width=1)
        rod = []
        if modo: rod.append(f"MODO: {modo}")
        if sala: rod.append(f"SALA: {sala}")
        if rod:
            w(d, (W//2, H-42), "      ".join(rod), _img_font(19, True), (150, 160, 178), anchor='ma')
        buf = io.BytesIO(); img.save(buf, 'PNG'); buf.seek(0); return buf.getvalue()
    except Exception:
        return None


# ───────────────────────── card de SALA (lobby) ─────────────────────────

def _gerar_imagem_sala_lobby(eq1, eq2, total='', sala=''):
    """Card 'SALA INICIADA' premium. eq1/eq2 = [{'nome','id'}] ou strings. Retorna bytes PNG."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    try:
        def w(d, xy, t, f, fill, anchor=None):
            if f: d.text(xy, t, font=f, fill=fill, anchor=anchor)
        def _norm(lst):
            out = []
            for it in (lst or []):
                if isinstance(it, dict):
                    out.append({'nome': str(it.get('nome') or ''), 'id': str(it.get('id') or '')})
                else:
                    out.append({'nome': str(it or ''), 'id': ''})
            return out
        eq1, eq2 = _norm(eq1), _norm(eq2)
        W = 1160
        slots = max(len(eq1), len(eq2), 4)
        slot_h = 90
        HEAD = 130
        H = HEAD + slots*slot_h + 64
        VERDE = (90, 235, 150)
        AZUL1, AZUL2 = (60, 110, 200), (40, 70, 140)
        LAR1, LAR2 = (210, 120, 50), (150, 80, 32)

        img = _img_fundo_premium(W, H, (16, 18, 28))
        d = ImageDraw.Draw(img)

        _glow_rect(img, [40, 34, 360, 84], VERDE, radius=12, passes=3)
        d = ImageDraw.Draw(img)
        w(d, (52, 40), "SALA INICIADA", _img_font(34, True), (255, 255, 255))
        w(d, (52, 84), "Bom jogo a todos!", _img_font(18), VERDE)

        pct = 0.5
        try:
            if total and '/' in total:
                a, b = total.split('/'); pct = min(1.0, int(a)/max(1, int(b)))
        except Exception:
            pass
        bar_y = 96
        d.rounded_rectangle([W-360, bar_y, W-52, bar_y+16], radius=8, fill=(36, 40, 56))
        bw = int((W-52) - (W-360)) * max(0.08, pct)
        d.rounded_rectangle([W-360, bar_y, W-360+int(bw), bar_y+16], radius=8, fill=VERDE)
        if total:
            w(d, (W-52, 60), f"VAGAS {total}", _img_font(24, True), (220, 226, 236), anchor='ra')

        def coluna(x0, num, jogs, cor1, cor2, cor_num):
            top = HEAD
            _painel(img, [x0, top, x0+52, top+slots*slot_h-16], (24, 28, 40),
                    radius=12, outline=cor_num, ow=2)
            dd = ImageDraw.Draw(img)
            w(dd, (x0+26, top+(slots*slot_h)//2-34), str(num), _img_font(40, True), cor_num, anchor='ma')
            y = top
            for i in range(slots):
                bx0, bx1 = x0+62, x0+540
                jg = jogs[i] if i < len(jogs) else None
                if jg and jg.get('nome'):
                    _painel(img, [bx0, y, bx1, y+slot_h-14], (30, 34, 48),
                            radius=12, outline=(52, 58, 78), ow=1)
                    av = _img_grad_diag(54, 54, cor1, cor2)
                    img.paste(av, (bx0+14, y+16))
                    dd = ImageDraw.Draw(img)
                    dd.rounded_rectangle([bx0+14, y+16, bx0+68, y+70], radius=12, outline=(255, 255, 255, 50), width=1)
                    ini = (str(jg['nome'])[:1] or '?').upper()
                    w(dd, (bx0+41, y+26), ini, _img_font(26, True), (255, 255, 255), anchor='ma')
                    if i == 0:
                        dd.polygon([(bx0+6, y+8), (bx0+26, y+8), (bx0+6, y+28)], fill=(255, 205, 80))
                    tx = bx0+82
                    w(dd, (tx, y+18), str(jg['nome'])[:20], _img_font(25, True), (240, 244, 250))
                    if jg.get('id'):
                        w(dd, (tx, y+50), f"ID {str(jg['id'])}", _img_font(16), (150, 160, 178))
                else:
                    _painel(img, [bx0, y, bx1, y+slot_h-14], (22, 26, 38),
                            radius=12, outline=(40, 46, 62), ow=1)
                    dd = ImageDraw.Draw(img)
                    w(dd, ((bx0+bx1)//2, y+slot_h//2-24), "— aguardando —", _img_font(18), (90, 98, 118), anchor='ma')
                y += slot_h
        coluna(44, 1, eq1, AZUL1, AZUL2, (110, 165, 240))
        coluna(W//2+22, 2, eq2, LAR1, LAR2, (240, 155, 80))

        d = ImageDraw.Draw(img)
        if sala:
            d.line([(44, H-50), (W-44, H-50)], fill=(38, 44, 60), width=1)
            w(d, (W//2, H-40), f"ID DA SALA: {sala}", _img_font(18, True), (160, 170, 188), anchor='ma')
        buf = io.BytesIO(); img.save(buf, 'PNG'); buf.seek(0); return buf.getvalue()
    except Exception:
        return None


# ───────────────────────── API pública do módulo ─────────────────────────

def gerar_sala(payload):
    """payload: {eq1, eq2, total, sala}. Retorna bytes PNG (ou None)."""
    p = payload or {}
    return _gerar_imagem_sala_lobby(
        p.get('eq1') or [],
        p.get('eq2') or [],
        total=str(p.get('total') or ''),
        sala=str(p.get('sala') or ''),
    )


def gerar_resultado(payload):
    """payload: {nome1,placar1,jogs1,nome2,placar2,jogs2,sala,modo}. Retorna bytes PNG (ou None)."""
    p = payload or {}
    return _gerar_imagem_resultado_ff(
        p.get('nome1') or 'EQUIPE 1', p.get('placar1', 0), p.get('jogs1') or [],
        p.get('nome2') or 'EQUIPE 2', p.get('placar2', 0), p.get('jogs2') or [],
        sala=str(p.get('sala') or ''), modo=str(p.get('modo') or ''),
    )
