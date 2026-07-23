"""
themes.py — Sigma template tokens.

The producer exposes exactly one template family: **Sigma**. It ships in three
variants:

* **Sigma Dark**   — pure-dark canvas, glow atmosphere, white type, accent highlights.
* **Sigma Light**  — off-white canvas, deep-navy type, the same layout DNA inverted.
* **Sigma Hybrid** — alternates dark and light slides across the deck.

All colour values flow from the user's ``Brand`` object, so the same tokens
render anyone's palette. A ``Theme`` is a flat bag of resolved colours plus the
two parameters that drive the NumPy radial-glow background.
"""
from __future__ import annotations

from dataclasses import dataclass

from .brand import RGB, Brand

VALID_STYLES = ("sigma-dark", "sigma-light", "sigma-hybrid")


@dataclass(frozen=True)
class Theme:
    name: str
    is_dark: bool

    bg_rgb: RGB
    text_rgb: RGB          # primary text
    text_mid_rgb: RGB      # secondary / body text
    accent_rgb: RGB        # highlight + nav bar + CTA ring

    glow_rgb: RGB          # colour of the radial glow blobs
    glow_sigma: float      # gaussian spread in pixels
    glow_intensity: float  # 0..1 peak strength

    separator_rgb: RGB
    card_bg_rgb: RGB
    card_border_rgb: RGB

    pill_bg_rgb: RGB
    pill_text_rgb: RGB
    header_text_rgb: RGB


def _dark_theme(brand: Brand) -> Theme:
    # Derive a deep, low-luminance version of the accent for the glow so it reads
    # as atmosphere rather than a coloured spotlight.
    glow = tuple(int(c * 0.5) for c in brand.primary)  # type: ignore[assignment]
    return Theme(
        name="Sigma Dark",
        is_dark=True,
        bg_rgb=brand.dark_bg,
        text_rgb=(255, 255, 255),
        text_mid_rgb=(210, 205, 196),
        accent_rgb=brand.primary,
        glow_rgb=glow,  # type: ignore[arg-type]
        glow_sigma=330.0,
        glow_intensity=0.42,
        separator_rgb=(42, 42, 42),
        card_bg_rgb=(16, 16, 16),
        card_border_rgb=(50, 50, 50),
        pill_bg_rgb=(255, 255, 255),
        pill_text_rgb=(17, 17, 17),
        header_text_rgb=(255, 255, 255),
    )


def _light_theme(brand: Brand) -> Theme:
    return Theme(
        name="Sigma Light",
        is_dark=False,
        bg_rgb=brand.light_bg,
        text_rgb=(11, 15, 26),
        text_mid_rgb=(80, 85, 100),
        accent_rgb=brand.primary,
        glow_rgb=brand.primary,
        glow_sigma=260.0,
        glow_intensity=0.05,
        separator_rgb=(210, 210, 215),
        card_bg_rgb=(232, 232, 235),
        card_border_rgb=(200, 200, 205),
        pill_bg_rgb=(17, 17, 17),
        pill_text_rgb=(255, 255, 255),
        header_text_rgb=(11, 15, 26),
    )


def theme_for_slide(style: str, brand: Brand, index: int) -> Theme:
    """Resolve the theme for slide ``index`` given the deck ``style``.

    For hybrid decks, even slides are dark and odd slides are light so the deck
    visually alternates as the viewer swipes.
    """
    style = (style or "sigma-dark").lower()
    if style == "sigma-light":
        return _light_theme(brand)
    if style == "sigma-hybrid":
        return _dark_theme(brand) if index % 2 == 0 else _light_theme(brand)
    # default + explicit sigma-dark
    return _dark_theme(brand)
