"""
Carousel Producer — a topic-in, finished-deck-out Instagram carousel engine.

The package modules import the repo-root ``config`` module for settings. To make
that resolve no matter how the package is launched, we ensure the repo root (the
parent of this package directory) is on ``sys.path``.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

__all__ = ["pipeline"]
__version__ = "0.1.0"
