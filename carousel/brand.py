"""
brand.py — Brand configuration loader.

Branding is pure configuration. Everything visual that is specific to a person
or company (colours, handle, fonts, assets, chosen Sigma style) lives in a YAML
file and is loaded into a ``Brand`` object here. The rendering engine reads from
that object and never hard-codes anyone's identity.

See ``brand.example.yaml`` for the schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

try:
    from PIL import ImageFont
except ImportError:  # pragma: no cover - Pillow is a hard dependency at runtime
    ImageFont = None  # type: ignore


RGB = tuple[int, int, int]

# The five Poppins-ish weights the renderer asks for. A user may point these at
# any TTF/OTF they like in their brand file; missing weights fall back to the
# lightest available, and if nothing is found we use Pillow's bitmap default so
# the repo still runs with no fonts installed.
FONT_WEIGHTS = (
    "black",
    "extrabold",
    "extrabold_italic",
    "bold",
    "semibold",
    "medium",
    "regular",
    "mono",
)


def hex_to_rgb(value: str) -> RGB:
    """Convert ``#RRGGBB`` (or ``RRGGBB``) to an ``(r, g, b)`` tuple."""
    h = value.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected a 6-digit hex colour, got {value!r}")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


@dataclass
class Brand:
    """A resolved brand identity. Constructed from a YAML file via ``from_yaml``."""

    name: str = "Your Brand"
    handle: str = "@yourhandle"
    style: str = "sigma-dark"          # sigma-dark | sigma-light | sigma-hybrid
    pill_text: str = "Save for later"

    primary: RGB = (237, 85, 0)        # accent / headline highlight
    secondary: RGB = (46, 192, 255)    # supporting accent
    dark_bg: RGB = (0, 0, 0)           # background for dark slides
    light_bg: RGB = (248, 248, 248)    # background for light slides

    font_paths: dict[str, str] = field(default_factory=dict)
    logo_path: str | None = None
    headshot_path: str | None = None

    # Resolved font base directory, so relative asset paths work from the file.
    base_dir: Path = field(default_factory=Path.cwd)

    _font_cache: dict[tuple[str, int], object] = field(default_factory=dict, repr=False)

    # ── Construction ────────────────────────────────────────────────────────
    @classmethod
    def from_yaml(cls, path: str | Path) -> "Brand":
        path = Path(path)
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}

        colors = data.get("colors", {})
        fonts = data.get("fonts", {})
        assets = data.get("assets", {})

        def color(key: str, default: RGB) -> RGB:
            raw = colors.get(key)
            return hex_to_rgb(raw) if raw else default

        base_dir = path.resolve().parent

        return cls(
            name=data.get("name", "Your Brand"),
            handle=data.get("handle", "@yourhandle"),
            style=data.get("style", "sigma-dark"),
            pill_text=data.get("pill_text", "Save for later"),
            primary=color("primary", (237, 85, 0)),
            secondary=color("secondary", (46, 192, 255)),
            dark_bg=color("dark_bg", (0, 0, 0)),
            light_bg=color("light_bg", (248, 248, 248)),
            font_paths={k: str(v) for k, v in fonts.items()},
            logo_path=assets.get("logo"),
            headshot_path=assets.get("headshot"),
            base_dir=base_dir,
        )

    # ── Asset resolution ────────────────────────────────────────────────────
    def _resolve(self, maybe_path: str | None) -> Path | None:
        if not maybe_path:
            return None
        p = Path(maybe_path)
        if not p.is_absolute():
            p = self.base_dir / p
        return p

    def logo(self) -> Path | None:
        p = self._resolve(self.logo_path)
        return p if p and p.exists() else None

    def headshot(self) -> Path | None:
        p = self._resolve(self.headshot_path)
        return p if p and p.exists() else None

    # ── Fonts ─────────────────────────────────────────────────────────────────
    def font(self, weight: str, size: int):
        """Load a font for ``weight`` at ``size``, with graceful fallback.

        Resolution order: the exact weight from the brand file, then the lightest
        available brand weight, then Pillow's built-in bitmap font. This means the
        engine renders even when a user has supplied no font files at all.
        """
        if ImageFont is None:  # pragma: no cover
            raise ImportError("Pillow is required: pip install pillow")

        key = (weight, size)
        if key in self._font_cache:
            return self._font_cache[key]

        path = self._resolve(self.font_paths.get(weight))
        if path and path.exists():
            font = ImageFont.truetype(str(path), size)
        else:
            font = self._fallback_font(size)

        self._font_cache[key] = font
        return font

    def _fallback_font(self, size: int):
        for w in ("regular", "medium", "semibold", "bold", "extrabold", "black"):
            path = self._resolve(self.font_paths.get(w))
            if path and path.exists():
                return ImageFont.truetype(str(path), size)
        # No brand fonts at all — use the always-available bitmap default.
        try:
            return ImageFont.load_default(size)
        except TypeError:  # older Pillow without size arg
            return ImageFont.load_default()
