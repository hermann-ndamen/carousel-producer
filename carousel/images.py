"""
images.py — pluggable image providers.

Every slide carries an *image idea* (a short description written during the
research step). An ``ImageProvider`` turns that idea into an actual image file.
Providers are interchangeable behind one small interface, so adding a new source
(a different generator, a stock library, an internal asset search) is one new
class — never a rewrite of the pipeline.

Three providers ship:

* ``StubImageProvider``       — draws a branded placeholder. No keys, always works.
* ``WebSearchImageProvider``  — finds a relevant real photo via an image-search API.
* ``NanoBananaImageProvider`` — generates an image with Google's Gemini image model
                                ("Nano Banana").

``get_image_provider(name, ...)`` returns the right one; unknown names and missing
credentials fall back to the stub so the repo always runs end-to-end.
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from config import Settings

from .brand import Brand


class ImageProvider(ABC):
    """Turn an image idea into an image file. One method to implement."""

    name: str = "provider"

    @abstractmethod
    def generate(self, idea: str, out_path: Path) -> Path:
        """Produce an image for ``idea`` at ``out_path`` and return the path."""
        raise NotImplementedError


# ── Stub (default, no keys) ──────────────────────────────────────────────────
class StubImageProvider(ImageProvider):
    """Deterministic placeholder art. Lets the whole pipeline run offline.

    The gradient is seeded from the idea text so each slide gets a distinct — but
    reproducible — image, which is handy for reviewing layout before wiring real
    generation.
    """

    name = "stub"

    def __init__(self, brand: Brand | None = None, size: tuple[int, int] = (1024, 1024)):
        self.brand = brand
        self.size = size

    def generate(self, idea: str, out_path: Path) -> Path:
        w, h = self.size
        seed = int(hashlib.sha256(idea.encode("utf-8")).hexdigest(), 16) % (2 ** 32)
        rng = np.random.default_rng(seed)

        top = rng.integers(20, 90, size=3)
        bottom = rng.integers(90, 200, size=3)
        grad = np.zeros((h, w, 3), dtype=np.float32)
        for c in range(3):
            grad[:, :, c] = np.linspace(top[c], bottom[c], h)[:, None]
        img = Image.fromarray(np.clip(grad, 0, 255).astype(np.uint8), "RGB")

        draw = ImageDraw.Draw(img)
        label = (idea or "placeholder")[:60]
        draw.text((w // 2, h // 2), label, fill=(255, 255, 255), anchor="mm")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), "PNG")
        return out_path


# ── Web image search ─────────────────────────────────────────────────────────
class WebSearchImageProvider(ImageProvider):
    """Find a relevant real photo via an image-search API and download it.

    This targets a generic JSON image-search endpoint configured via env
    (``IMAGE_SEARCH_ENDPOINT`` + ``IMAGE_SEARCH_API_KEY``). The response is
    expected to expose image URLs; we take the first result and save it. Swap the
    ``_search`` method to target whichever provider you use — the rest of the
    pipeline is unaffected.
    """

    name = "web_search"

    def __init__(self, settings: Settings):
        self.settings = settings

    def generate(self, idea: str, out_path: Path) -> Path:
        import requests

        url = self._search(idea)
        if not url:
            raise RuntimeError(f"no image found for idea: {idea!r}")

        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Normalise to PNG via Pillow so downstream cropping is uniform.
        from io import BytesIO

        Image.open(BytesIO(resp.content)).convert("RGB").save(str(out_path), "PNG")
        return out_path

    def _search(self, idea: str) -> str | None:
        import requests

        endpoint = self.settings.image_search_endpoint
        key = self.settings.image_search_api_key
        if not endpoint or not key:
            raise RuntimeError(
                "web_search provider needs IMAGE_SEARCH_ENDPOINT and "
                "IMAGE_SEARCH_API_KEY in the environment"
            )
        resp = requests.get(
            endpoint,
            params={"q": idea, "per_page": 5},
            headers={"Authorization": key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return _first_image_url(data)


def _first_image_url(data) -> str | None:
    """Best-effort extraction of the first image URL from a search JSON payload.

    Different image-search APIs nest the URL differently; this walks the common
    shapes (``results``/``photos``/``items``/``hits`` arrays with ``url`` /
    ``src`` / ``link`` / ``image`` fields) so the provider works against several
    services without editing.
    """
    if isinstance(data, dict):
        for list_key in ("results", "photos", "items", "hits", "images", "data"):
            items = data.get(list_key)
            if isinstance(items, list) and items:
                return _url_from_item(items[0])
        return _url_from_item(data)
    if isinstance(data, list) and data:
        return _url_from_item(data[0])
    return None


def _url_from_item(item) -> str | None:
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return None
    for key in ("url", "link", "image", "src", "thumbnail", "largeImageURL"):
        val = item.get(key)
        if isinstance(val, str) and val.startswith("http"):
            return val
        if isinstance(val, dict):  # e.g. {"src": {"large": "..."}}
            nested = _url_from_item(val)
            if nested:
                return nested
    return None


# ── Nano Banana (Gemini image generation) ────────────────────────────────────
class NanoBananaImageProvider(ImageProvider):
    """Generate an image with Google's Gemini image model ("Nano Banana").

    Uses the ``google-genai`` SDK. Requires ``GEMINI_API_KEY``. The model returns
    inline image bytes, which we save to ``out_path``.
    """

    name = "nano_banana"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.nano_banana_model

    def generate(self, idea: str, out_path: Path) -> Path:
        if not self.settings.gemini_api_key:
            raise RuntimeError("nano_banana provider needs GEMINI_API_KEY")
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise ImportError("pip install google-genai") from exc

        client = genai.Client(api_key=self.settings.gemini_api_key)
        prompt = (
            f"{idea}. High quality, clean composition, strong subject, "
            f"suitable as a single social-media carousel slide image."
        )
        response = client.models.generate_content(model=self.model, contents=[prompt])

        image_bytes = _extract_inline_image(response)
        if image_bytes is None:
            raise RuntimeError("Gemini returned no image data")

        from io import BytesIO

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.open(BytesIO(image_bytes)).convert("RGB").save(str(out_path), "PNG")
        return out_path


def _extract_inline_image(response) -> bytes | None:
    """Pull the first inline image payload out of a Gemini response."""
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None) if inline else None
            if data:
                return data
    return None


# ── Factory ──────────────────────────────────────────────────────────────────
def get_image_provider(name: str, settings: Settings, brand: Brand | None = None) -> ImageProvider:
    """Return the provider for ``name``, falling back to the stub when a real
    provider can't be used (unknown name, or missing credentials)."""
    name = (name or "stub").lower()
    try:
        if name == "nano_banana":
            if not settings.gemini_api_key:
                raise RuntimeError("GEMINI_API_KEY not set")
            return NanoBananaImageProvider(settings)
        if name == "web_search":
            if not (settings.image_search_endpoint and settings.image_search_api_key):
                raise RuntimeError("image search not configured")
            return WebSearchImageProvider(settings)
    except RuntimeError as exc:
        print(f"[images] {name} unavailable ({exc}); using stub provider.")
        return StubImageProvider(brand)
    return StubImageProvider(brand)
