"""
render.py — Sigma slide renderers (PIL/Pillow + NumPy).

Canvas is 1080 x 1350 (4:5 portrait, Instagram's tallest feed ratio). Each slide
is composited on a NumPy radial-glow background, then a header, headline/body,
optional image card, and a bottom accent bar are drawn on top.

Every renderer records the bounding box of everything it draws into a
``RenderResult``. ``selfcheck.py`` reads those boxes to verify nothing overlaps,
nothing is clipped, and the visual hierarchy holds — and the pipeline re-renders
at a smaller font scale if a slide fails.

Only the Sigma family is exposed. Supported slide types: hook, body, stat, cta,
prompt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .brand import RGB, Brand
from .themes import Theme

# ── Layout constants ────────────────────────────────────────────────────────
W, H = 1080, 1350
SIDE = 64
CONTENT_TOP = 108          # first usable y below the header + separator
SAFE_BOTTOM = 1255         # last usable y above the 12px accent bar
NAV_BAR_H = 12


@dataclass
class TextBox:
    """A drawn element's bounds, tagged with a semantic role and its font size."""

    x0: int
    y0: int
    x1: int
    y1: int
    role: str            # header | title | body | stat | context | cta | prompt | card | lead
    font_size: int = 0


@dataclass
class RenderResult:
    path: Path
    width: int
    height: int
    boxes: list[TextBox] = field(default_factory=list)


# ── Text helpers ────────────────────────────────────────────────────────────
def text_width(font, text: str) -> int:
    try:
        return int(font.getlength(text))
    except AttributeError:  # very old Pillow
        return len(text) * (getattr(font, "size", 16) // 2)


def wrap(text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for word in words:
        trial = " ".join(cur + [word])
        if text_width(font, trial) <= max_w or not cur:
            cur.append(word)
        else:
            lines.append(" ".join(cur))
            cur = [word]
    if cur:
        lines.append(" ".join(cur))
    return lines or [""]


def _font_size(font, fallback: int) -> int:
    return int(getattr(font, "size", fallback))


# ── Background ──────────────────────────────────────────────────────────────
def make_glow_background(theme: Theme) -> Image.Image:
    """Solid background with two soft radial-glow blobs (top and bottom).

    Mirrors the gaussian-glow technique of the source system: for each blob we
    evaluate ``intensity * exp(-dist^2 / (2*sigma^2))`` across the pixel grid and
    add the glow colour, then clip. Cheap, vectorised, and gives real depth
    rather than a flat fill.
    """
    canvas = np.zeros((H, W, 3), dtype=np.float32)
    canvas[:, :] = np.array(theme.bg_rgb, dtype=np.float32)

    glow = np.array(theme.glow_rgb, dtype=np.float32)
    sigma = theme.glow_sigma
    intensity = theme.glow_intensity

    ys, xs = np.mgrid[0:H, 0:W]
    for (gx, gy) in ((W // 2, -55), (W // 2, H + 55)):
        dist_sq = (xs - gx).astype(np.float32) ** 2 + (ys - gy).astype(np.float32) ** 2
        strength = np.exp(-dist_sq / (2.0 * sigma ** 2)) * intensity
        for c in range(3):
            canvas[:, :, c] += strength * glow[c]

    canvas = np.clip(canvas, 0, 255).astype(np.uint8)
    return Image.fromarray(canvas, "RGB")


# ── Shared drawing primitives ───────────────────────────────────────────────
def _draw_header(canvas: Image.Image, draw: ImageDraw.ImageDraw, brand: Brand,
                 theme: Theme, boxes: list[TextBox]) -> None:
    """Handle (+ optional logo) on the left, a 'save' pill on the right, and a
    thin separator line under both."""
    # Pill (right)
    f_pill = brand.font("medium", 26)
    pill_text = brand.pill_text
    pill_w = text_width(f_pill, pill_text) + 36
    pill_h = 44
    px = W - SIDE - pill_w
    py = 22
    draw.rounded_rectangle([px, py, px + pill_w, py + pill_h], radius=22,
                           fill=theme.pill_bg_rgb)
    draw.text((px + pill_w // 2, py + pill_h // 2), pill_text,
              fill=theme.pill_text_rgb, font=f_pill, anchor="mm")
    boxes.append(TextBox(px, py, px + pill_w, py + pill_h, "header", 26))

    # Logo (optional) + handle (left)
    x = SIDE
    logo = brand.logo()
    if logo:
        try:
            mark = Image.open(logo).convert("RGBA")
            mh = 40
            mw = int(mark.width * (mh / mark.height))
            mark = mark.resize((mw, mh), Image.LANCZOS)
            canvas.paste(mark, (x, py + (pill_h - mh) // 2), mark)
            x += mw + 12
        except Exception:
            pass

    f_h = brand.font("regular", 28)
    handle_y = py + (pill_h - 28) // 2
    draw.text((x, handle_y), brand.handle, fill=theme.header_text_rgb, font=f_h)
    boxes.append(TextBox(x, handle_y, x + text_width(f_h, brand.handle),
                         handle_y + 28, "header", 28))

    # Separator line
    draw.rectangle([SIDE, 84, W - SIDE, 85], fill=theme.separator_rgb)


def _draw_nav_bar(draw: ImageDraw.ImageDraw, theme: Theme) -> None:
    draw.rectangle([0, H - NAV_BAR_H, W, H], fill=theme.accent_rgb)


def _draw_two_tone_headline(draw: ImageDraw.ImageDraw, headline: str,
                            accent_words: list[str], y: int, brand: Brand,
                            theme: Theme, size: int, boxes: list[TextBox]) -> int:
    """Center-aligned headline. Words in ``accent_words`` render italic + accent
    colour; the rest render in the primary text colour. Records one title box."""
    accent_set = {w.lower().strip(".,!?;:'\"") for w in (accent_words or [])}
    f_normal = brand.font("extrabold", size)
    f_accent = brand.font("extrabold_italic", size)
    max_w = W - SIDE * 3
    line_h = int(size * 1.22)
    cx = W // 2

    tokens = []
    for word in headline.split():
        is_accent = word.lower().strip(".,!?;:'\"") in accent_set
        tokens.append({
            "text": word,
            "font": f_accent if is_accent else f_normal,
            "color": theme.accent_rgb if is_accent else theme.text_rgb,
        })

    # Greedy line wrap keeping accent/normal fonts per token.
    lines: list[list[dict]] = []
    cur: list[dict] = []
    cur_w = 0
    for tok in tokens:
        tw = text_width(tok["font"], tok["text"] + " ")
        if cur and cur_w + tw > max_w:
            lines.append(cur)
            cur, cur_w = [tok], tw
        else:
            cur.append(tok)
            cur_w += tw
    if cur:
        lines.append(cur)

    y0 = y
    max_line_w = 0
    for line in lines:
        line_w = sum(text_width(t["font"], t["text"] + " ") for t in line)
        max_line_w = max(max_line_w, line_w)
        x = cx - line_w // 2
        for tok in line:
            draw.text((x, y), tok["text"], fill=tok["color"], font=tok["font"])
            x += text_width(tok["font"], tok["text"] + " ")
        y += line_h

    if headline.strip():
        boxes.append(TextBox(cx - max_line_w // 2, y0, cx + max_line_w // 2, y,
                             "title", size))
    return y


def _draw_centered_text(draw: ImageDraw.ImageDraw, text: str, y: int, font,
                        color: RGB, role: str, boxes: list[TextBox],
                        max_w: int | None = None, leading: float = 1.45) -> int:
    if max_w is None:
        max_w = W - SIDE * 2 - 20
    size = _font_size(font, 40)
    step = int(size * leading)
    cx = W // 2
    y0 = y
    max_line_w = 0
    for raw in text.split("\n"):
        for line in (wrap(raw, font, max_w) if raw.strip() else [""]):
            lw = text_width(font, line)
            max_line_w = max(max_line_w, lw)
            draw.text((cx - lw // 2, y), line, fill=color, font=font)
            y += step
    if text.strip():
        boxes.append(TextBox(cx - max_line_w // 2, y0, cx + max_line_w // 2, y,
                             role, size))
    return y


def _paste_image_card(canvas: Image.Image, draw: ImageDraw.ImageDraw,
                      image_path: str | None, image_idea: str, top_y: int,
                      card_h: int, theme: Theme, brand: Brand,
                      boxes: list[TextBox], width_ratio: float = 0.84) -> None:
    """Rounded image card. If a real image is available it is cover-cropped into
    the card; otherwise a labelled placeholder is drawn so the deck still renders
    (and the reviewer can see where the image will go)."""
    card_w = int(W * width_ratio)
    card_x = (W - card_w) // 2
    radius = 20

    draw.rounded_rectangle([card_x, top_y, card_x + card_w, top_y + card_h],
                           radius=radius, fill=theme.card_bg_rgb,
                           outline=theme.card_border_rgb, width=1)
    boxes.append(TextBox(card_x, top_y, card_x + card_w, top_y + card_h, "card"))

    pad = 10
    inner = (card_x + pad, top_y + pad, card_x + card_w - pad, top_y + card_h - pad)
    img = _load_cover(image_path, inner)
    if img is not None:
        canvas.paste(img, (inner[0], inner[1]), img)
    else:
        # Placeholder: subtle diagonal tint + the image idea text.
        f = brand.font("medium", 26)
        label = f"[ image: {image_idea} ]" if image_idea else "[ image ]"
        _draw_centered_text(draw, label, top_y + card_h // 2 - 20, f,
                            theme.text_mid_rgb, "card", boxes,
                            max_w=card_w - 60)


def _load_cover(image_path: str | None, box: tuple[int, int, int, int]):
    if not image_path:
        return None
    p = Path(image_path)
    if not p.exists():
        return None
    try:
        img = Image.open(p).convert("RGBA")
    except Exception:
        return None
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    ir, br = img.width / img.height, bw / bh
    if ir > br:
        nh, nw = bh, int(bh * ir)
    else:
        nw, nh = bw, int(bw / ir)
    img = img.resize((max(1, nw), max(1, nh)), Image.LANCZOS)
    cx, cy = (nw - bw) // 2, (nh - bh) // 2
    img = img.crop((cx, cy, cx + bw, cy + bh))
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, bw - 1, bh - 1], radius=16, fill=255)
    img.putalpha(mask)
    return img


# ── Slide renderers ─────────────────────────────────────────────────────────
def _render_hook(slide, brand, theme, draw, canvas, boxes, scale):
    headline = slide.get("headline", "")
    subheadline = slide.get("subheadline", "")
    accent_words = slide.get("accent_words", [])
    has_image = bool(slide.get("image") or slide.get("image_idea"))

    hl_size = int(84 * scale)
    f_sub = brand.font("semibold", int(44 * scale))

    hl_lines = len(wrap(headline, brand.font("extrabold", hl_size), W - SIDE * 3))
    hl_h = int(hl_size * 1.22) * max(1, hl_lines)
    sub_lines = wrap(subheadline, f_sub, W - SIDE * 2 - 20) if subheadline else []
    sub_h = int(_font_size(f_sub, 44) * 1.45) * len(sub_lines)
    text_h = hl_h + (28 + sub_h if subheadline else 0)

    card_h = 0
    if has_image:
        avail = SAFE_BOTTOM - CONTENT_TOP - text_h - 48
        card_h = max(300, min(600, avail))
    card_top = SAFE_BOTTOM - card_h if has_image else SAFE_BOTTOM

    avail_text = (card_top - 16) - CONTENT_TOP
    y = CONTENT_TOP + max(0, (avail_text - text_h) // 2)

    y = _draw_two_tone_headline(draw, headline, accent_words, y, brand, theme,
                                hl_size, boxes)
    if subheadline:
        y += 28
        _draw_centered_text(draw, subheadline, y, f_sub, theme.text_mid_rgb,
                            "body", boxes)
    if has_image:
        _paste_image_card(canvas, draw, slide.get("image"),
                          slide.get("image_idea", ""), card_top, card_h,
                          theme, brand, boxes, width_ratio=0.86)


def _render_body(slide, brand, theme, draw, canvas, boxes, scale):
    headline = slide.get("headline", "")
    body = slide.get("text", "")
    accent_words = slide.get("accent_words", [])
    has_image = bool(slide.get("image") or slide.get("image_idea"))

    hl_size = int(74 * scale)
    f_body = brand.font("regular", int(42 * scale))

    hl_lines = len(wrap(headline, brand.font("extrabold", hl_size), W - SIDE * 3))
    hl_h = int(hl_size * 1.22) * max(1, hl_lines)
    body_lines = 0
    for raw in body.split("\n"):
        body_lines += len(wrap(raw, f_body, W - SIDE * 2 - 20)) if raw.strip() else 1
    body_h = int(_font_size(f_body, 42) * 1.5) * body_lines if body else 0

    gap = 28
    text_h = hl_h + (gap + body_h if body else 0)

    card_h = 0
    if has_image:
        avail = SAFE_BOTTOM - CONTENT_TOP - text_h - 48
        card_h = max(260, min(480, avail))
    card_top = SAFE_BOTTOM - card_h if has_image else SAFE_BOTTOM

    avail_text = (card_top - 16) - CONTENT_TOP
    y = CONTENT_TOP + max(16, (avail_text - text_h) // 2)

    if headline:
        y = _draw_two_tone_headline(draw, headline, accent_words, y, brand,
                                    theme, hl_size, boxes)
    if body:
        y += gap
        _draw_centered_text(draw, body, y, f_body, theme.text_mid_rgb, "body", boxes)
    if has_image:
        _paste_image_card(canvas, draw, slide.get("image"),
                          slide.get("image_idea", ""), card_top, card_h,
                          theme, brand, boxes, width_ratio=0.80)


def _render_stat(slide, brand, theme, draw, canvas, boxes, scale):
    stat = str(slide.get("stat", ""))
    context = slide.get("context", "")

    f_stat = brand.font("black", int(190 * scale))
    f_ctx = brand.font("medium", int(44 * scale))
    max_w = W - SIDE * 2

    stat_lines = wrap(stat, f_stat, max_w)
    ctx_lines = wrap(context, f_ctx, max_w) if context else []
    stat_step = int(_font_size(f_stat, 190) * 1.05)
    ctx_step = int(_font_size(f_ctx, 44) * 1.45)
    stat_h = stat_step * len(stat_lines)
    ctx_h = ctx_step * len(ctx_lines)
    gap, sep_h = 40, 4
    total = stat_h + (gap + sep_h + gap + ctx_h if ctx_lines else 0)

    avail = SAFE_BOTTOM - CONTENT_TOP
    y = CONTENT_TOP + max(0, (avail - total) // 2)

    cx = W // 2
    y0 = y
    max_line_w = 0
    for line in stat_lines:
        lw = text_width(f_stat, line)
        max_line_w = max(max_line_w, lw)
        draw.text((cx - lw // 2, y), line, fill=theme.text_rgb, font=f_stat)
        y += stat_step
    boxes.append(TextBox(cx - max_line_w // 2, y0, cx + max_line_w // 2, y,
                         "stat", _font_size(f_stat, 190)))

    if ctx_lines:
        y += gap
        draw.rectangle([cx - 44, y, cx + 44, y + sep_h], fill=theme.accent_rgb)
        y += sep_h + gap
        _draw_centered_text(draw, context, y, f_ctx, theme.text_mid_rgb,
                            "context", boxes)


def _render_cta(slide, brand, theme, draw, canvas, boxes, scale):
    cta_text = slide.get("cta_text", "Follow for more")
    accent_words = slide.get("accent_words", [])
    reason_text = slide.get("reason_text", "")
    follow_text = slide.get("follow_text", "")

    hs_d = 220
    ring_d = hs_d + 22
    f_sub = brand.font("regular", int(44 * scale))
    hl_size = int(84 * scale)

    hl_lines = len(wrap(cta_text, brand.font("extrabold", hl_size), W - SIDE * 3))
    hl_h = int(hl_size * 1.22) * max(1, hl_lines)
    reason_h = 0
    if reason_text:
        reason_h = int(_font_size(f_sub, 44) * 1.45) * len(
            wrap(reason_text, f_sub, W - SIDE * 2 - 20))
    follow_h = 0
    if follow_text:
        follow_h = int(_font_size(f_sub, 44) * 1.45) * len(
            wrap(follow_text, f_sub, W - SIDE * 2 - 20))
    gap_hs, gap_rsn, gap_flw = 50, 16, 56
    total = (ring_d + gap_hs + hl_h + (gap_rsn + reason_h if reason_text else 0)
             + (gap_flw + follow_h if follow_text else 0))

    avail = SAFE_BOTTOM - CONTENT_TOP
    y = CONTENT_TOP + max(16, (avail - total) // 2)
    cx = W // 2

    # Accent ring + headshot circle
    draw.ellipse([cx - ring_d // 2, y, cx + ring_d // 2, y + ring_d],
                 fill=theme.accent_rgb)
    boxes.append(TextBox(cx - ring_d // 2, y, cx + ring_d // 2, y + ring_d, "cta"))
    hs_x, hs_y = cx - hs_d // 2, y + 11
    headshot = brand.headshot()
    pasted = False
    if headshot:
        try:
            hs = Image.open(headshot).convert("RGBA").resize((hs_d, hs_d), Image.LANCZOS)
            mask = Image.new("L", (hs_d, hs_d), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, hs_d - 1, hs_d - 1], fill=255)
            hs.putalpha(mask)
            canvas.paste(hs, (hs_x, hs_y), hs)
            pasted = True
        except Exception:
            pasted = False
    if not pasted:
        draw.ellipse([hs_x, hs_y, hs_x + hs_d, hs_y + hs_d], fill=(28, 28, 28))
        initial = brand.handle.lstrip("@")[:1].upper() or "?"
        draw.text((cx, hs_y + hs_d // 2), initial, fill=(255, 255, 255),
                  font=brand.font("black", 80), anchor="mm")

    y = y + ring_d + gap_hs
    y = _draw_two_tone_headline(draw, cta_text, accent_words, y, brand, theme,
                                hl_size, boxes)
    if reason_text:
        y += gap_rsn
        y = _draw_centered_text(draw, reason_text, y, f_sub, theme.text_mid_rgb,
                                "body", boxes)
    if follow_text:
        y += gap_flw
        _draw_centered_text(draw, follow_text, y, f_sub, theme.text_mid_rgb,
                            "body", boxes)


def _render_prompt(slide, brand, theme, draw, canvas, boxes, scale):
    headline = slide.get("headline", "")
    lead = slide.get("text", "")
    prompt_text = slide.get("prompt_text", "")
    label = (slide.get("prompt_label", "PASTE THIS") or "PASTE THIS").upper()
    accent_words = slide.get("accent_words", [])

    hl_size = int(70 * scale)
    f_lead = brand.font("regular", int(38 * scale))
    f_cap = brand.font("bold", 22)
    f_mono = brand.font("mono", int(28 * scale))

    hl_lines = len(wrap(headline, brand.font("extrabold", hl_size), W - SIDE * 3))
    hl_h = int(hl_size * 1.22) * max(1, hl_lines) if headline else 0
    lead_h = 0
    if lead:
        lead_h = int(_font_size(f_lead, 38) * 1.45) * len(
            wrap(lead, f_lead, W - SIDE * 2 - 20))

    panel_w = W - SIDE * 2 + 24
    panel_x = (W - panel_w) // 2
    pad_x, pad_y = 44, 40
    inner_w = panel_w - pad_x * 2
    mono_lines: list[str] = []
    for raw in prompt_text.split("\n"):
        mono_lines.extend(wrap(raw, f_mono, inner_w) if raw.strip() else [""])
    mono_step = int(_font_size(f_mono, 28) * 1.55)
    panel_h = pad_y * 2 + max(mono_step * len(mono_lines), 74)

    cap_h = int(22 * 1.4)
    gaps = 22 + 28 + 14
    total = hl_h + (22 + lead_h if lead else 0) + 28 + cap_h + 14 + panel_h
    avail = SAFE_BOTTOM - CONTENT_TOP
    y = CONTENT_TOP + max(12, (avail - total) // 2)

    if headline:
        y = _draw_two_tone_headline(draw, headline, accent_words, y, brand,
                                    theme, hl_size, boxes)
    if lead:
        y += 22
        y = _draw_centered_text(draw, lead, y, f_lead, theme.text_mid_rgb,
                                "lead", boxes)

    # 'PASTE THIS' cap
    y += 28
    cap_w = text_width(f_cap, label)
    draw.text((W // 2 - cap_w // 2, y), label, fill=theme.accent_rgb, font=f_cap)
    boxes.append(TextBox(W // 2 - cap_w // 2, y, W // 2 + cap_w // 2, y + cap_h,
                         "lead", 22))
    y += cap_h + 14

    # Mono panel
    if theme.is_dark:
        panel_bg, panel_border, mono_color = (20, 20, 22), (50, 50, 54), (230, 230, 232)
    else:
        panel_bg, panel_border, mono_color = (252, 239, 227), (230, 215, 200), (11, 15, 26)
    draw.rounded_rectangle([panel_x, y, panel_x + panel_w, y + panel_h],
                           radius=24, fill=panel_bg, outline=panel_border, width=1)
    boxes.append(TextBox(panel_x, y, panel_x + panel_w, y + panel_h, "prompt",
                         _font_size(f_mono, 28)))
    ty = y + pad_y
    for line in mono_lines:
        draw.text((panel_x + pad_x, ty), line, fill=mono_color, font=f_mono)
        ty += mono_step


_RENDERERS = {
    "hook": _render_hook,
    "body": _render_body,
    "stat": _render_stat,
    "cta": _render_cta,
    "prompt": _render_prompt,
}


def render_slide(slide: dict, theme: Theme, brand: Brand, out_path: Path,
                 font_scale: float = 1.0) -> RenderResult:
    """Render one slide to ``out_path`` and return its layout metadata.

    ``font_scale`` shrinks all content type (headers stay fixed); the pipeline
    lowers it and re-renders when the self-check flags clipping.
    """
    canvas = make_glow_background(theme)
    draw = ImageDraw.Draw(canvas)
    boxes: list[TextBox] = []

    _draw_header(canvas, draw, brand, theme, boxes)

    stype = slide.get("type", "body")
    renderer = _RENDERERS.get(stype, _render_body)
    renderer(slide, brand, theme, draw, canvas, boxes, font_scale)

    _draw_nav_bar(draw, theme)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), "PNG")
    return RenderResult(path=out_path, width=W, height=H, boxes=boxes)
