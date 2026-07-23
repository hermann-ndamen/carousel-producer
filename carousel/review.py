"""
review.py — the one human-in-the-loop step.

Before a single image is generated or a slide is rendered, the outline is written
to disk as ``outline.json``. A human opens it, edits any line of copy or any image
idea, sets ``approved: true``, and the pipeline picks up from there.

This is deliberately the *only* place a person is asked to intervene. Everything
before it (research, drafting) and after it (image generation, rendering,
self-check, captions) runs automatically.

Two ways to approve:

* **File gate** (default) — run once to write ``outline.json``; edit it; re-run
  with ``--approve`` (or set ``approved: true`` yourself).
* **Interactive** — call ``prompt_for_approval`` to pause on ``input()`` in a TTY.
"""
from __future__ import annotations

import json
from pathlib import Path


def write_outline(topic: str, style: str, slides: list[dict], path: Path) -> Path:
    """Write the reviewable outline. ``approved`` starts false."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Number slides for the reader; ids are informational only.
    for i, slide in enumerate(slides, start=1):
        slide.setdefault("id", i)
    doc = {
        "topic": topic,
        "style": style,
        "approved": False,
        "_instructions": (
            "Review and edit every slide's copy and 'image_idea'. When you are "
            "happy, set \"approved\": true (or re-run with --approve) to continue."
        ),
        "slides": slides,
    }
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
    return path


def load_outline(path: Path) -> dict:
    with open(Path(path)) as fh:
        return json.load(fh)


def is_approved(path: Path) -> bool:
    try:
        return bool(load_outline(path).get("approved"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False


def approve(path: Path) -> None:
    """Flip ``approved`` to true in place (used by the ``--approve`` flag)."""
    doc = load_outline(path)
    doc["approved"] = True
    with open(Path(path), "w") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)


def prompt_for_approval(path: Path) -> bool:
    """Interactive gate: show where the outline is and wait for confirmation.

    Returns True if approved. Safe to call in non-interactive contexts (returns
    False immediately if stdin is not a TTY).
    """
    import sys

    print(f"\nOutline written to: {path}")
    print("Edit the copy and image ideas, then confirm.")
    if not sys.stdin.isatty():
        print("Non-interactive shell: re-run with --approve once you've edited it.")
        return False
    answer = input("Approve and continue? [y/N] ").strip().lower()
    if answer in ("y", "yes"):
        approve(path)
        return True
    return False
