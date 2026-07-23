"""
config.py — runtime settings loaded from the environment.

Secrets and model choices live in the environment (see ``.env.example``), never
in code. Import ``load_settings()`` to get a populated ``Settings`` object.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    # Anthropic — research, outline drafting, captions.
    anthropic_api_key: str = ""
    model: str = "claude-opus-4-8"

    # Google Gemini — the Nano Banana image provider.
    gemini_api_key: str = ""
    nano_banana_model: str = "gemini-2.5-flash-image"

    # Web image-search provider (generic JSON image search).
    image_search_endpoint: str = ""
    image_search_api_key: str = ""

    @property
    def has_anthropic(self) -> bool:
        # A bare Anthropic() client also picks up an `ant auth login` profile, but
        # for the offline-friendly defaults here we gate on the explicit key.
        return bool(self.anthropic_api_key)


def load_settings() -> Settings:
    """Read settings from the environment (loading a local .env if present)."""
    _load_dotenv()
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model=os.getenv("CAROUSEL_MODEL", "claude-opus-4-8"),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        nano_banana_model=os.getenv("NANO_BANANA_MODEL", "gemini-2.5-flash-image"),
        image_search_endpoint=os.getenv("IMAGE_SEARCH_ENDPOINT", ""),
        image_search_api_key=os.getenv("IMAGE_SEARCH_API_KEY", ""),
    )


def _load_dotenv() -> None:
    """Populate os.environ from a .env file if python-dotenv is available.

    Optional dependency: absence is fine, real environment variables still work.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()
