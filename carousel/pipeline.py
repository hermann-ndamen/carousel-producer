"""
pipeline.py — the orchestrator.

Wires the whole flow together:

    topic
      -> research + brand          (research.py, brand.py)
      -> draft outline
      -> HUMAN REVIEW & EDIT       (review.py)   <-- the one manual step
      -> generate images           (images.py, pluggable provider)
      -> render slides             (render.py, Sigma template)
      -> self-check + remake       (selfcheck.py)
      -> two caption options       (captions.py)

Run it twice: the first run stops at the review gate after writing
``outline.json``; edit that file, then re-run with ``--approve`` to finish. Or
pass ``--interactive`` to approve inline.

    python -m carousel.pipeline --topic "..." --brand brand.yaml
    python -m carousel.pipeline --topic "..." --brand brand.yaml --approve
"""
from __future__ import annotations

import argparse
from pathlib import Path

from config import Settings, load_settings

from .brand import Brand
from .captions import write_captions
from .images import get_image_provider
from .render import render_slide
from .research import draft_outline, gather_research
from .review import is_approved, load_outline, prompt_for_approval, write_outline
from .selfcheck import check_slide
from .themes import theme_for_slide

MAX_REMAKE_RETRIES = 3
REMAKE_SCALE_STEP = 0.88


def run(topic: str, brand_path: str, out_dir: str, provider_name: str,
        approve: bool, interactive: bool, settings: Settings | None = None) -> dict:
    """Execute the pipeline. Returns a summary dict; prints progress to stdout."""
    settings = settings or load_settings()
    brand = Brand.from_yaml(brand_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    outline_path = out / "outline.json"

    # ── Stages 1-3: research, draft, and the human review gate ───────────────
    if not outline_path.exists():
        print(f"[1/7] Researching {topic!r} ...")
        research = gather_research(topic, settings)
        print("[2/7] Drafting outline ...")
        slides = draft_outline(topic, research, brand, settings)
        write_outline(topic, brand.style, slides, outline_path)
        print(f"[3/7] Review gate: edit {outline_path}, then approve.")

    if approve:
        from .review import approve as _approve
        _approve(outline_path)

    if not is_approved(outline_path):
        if interactive and prompt_for_approval(outline_path):
            pass
        else:
            print("\nNot yet approved. Edit the outline and re-run with --approve.")
            return {"status": "awaiting_review", "outline": str(outline_path)}

    doc = load_outline(outline_path)
    slides = doc["slides"]
    style = doc.get("style", brand.style)

    # ── Stage 4: images (pluggable provider) ─────────────────────────────────
    print(f"[4/7] Generating images via {provider_name!r} provider ...")
    provider = get_image_provider(provider_name, settings, brand)
    img_dir = out / "images"
    for i, slide in enumerate(slides, start=1):
        idea = slide.get("image_idea", "").strip()
        if not idea:
            continue
        img_path = img_dir / f"slide_{i:02d}.png"
        try:
            provider.generate(idea, img_path)
            slide["image"] = str(img_path)
        except Exception as exc:
            print(f"  slide {i}: image generation failed ({exc}); rendering without it.")

    # ── Stages 5-6: render, self-check, remake ───────────────────────────────
    print("[5/7] Rendering slides ...")
    rendered: list[Path] = []
    for i, slide in enumerate(slides, start=1):
        theme = theme_for_slide(style, brand, i - 1)
        out_path = out / f"slide_{i:02d}.png"
        scale = 1.0
        for attempt in range(1, MAX_REMAKE_RETRIES + 2):
            result = render_slide(slide, theme, brand, out_path, font_scale=scale)
            issues = check_slide(result)
            if not issues:
                break
            if attempt > MAX_REMAKE_RETRIES:
                print(f"  slide {i}: still imperfect after {attempt} tries: "
                      f"{issues[0]}")
                break
            print(f"  slide {i}: self-check flagged {len(issues)} issue(s) "
                  f"({issues[0]}); remaking at {scale * REMAKE_SCALE_STEP:.2f}x.")
            scale *= REMAKE_SCALE_STEP
        rendered.append(out_path)
    print(f"[6/7] Self-check complete: {len(rendered)} slides.")

    # ── Stage 7: captions ────────────────────────────────────────────────────
    print("[7/7] Writing two caption options ...")
    captions = write_captions(topic, slides, brand, settings)
    captions_path = out / "captions.txt"
    with open(captions_path, "w") as fh:
        for n, cap in enumerate(captions, start=1):
            fh.write(f"--- Option {n} ---\n{cap}\n\n")

    print("\nDone.")
    print(f"  Slides:   {out}/slide_*.png")
    print(f"  Captions: {captions_path}")
    return {
        "status": "complete",
        "slides": [str(p) for p in rendered],
        "captions": captions,
        "captions_path": str(captions_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Branded Instagram carousel producer.")
    parser.add_argument("--topic", required=True, help="the carousel topic")
    parser.add_argument("--brand", required=True, help="path to brand YAML")
    parser.add_argument("--out", default="output", help="output directory")
    parser.add_argument("--provider", default="stub",
                        choices=["stub", "web_search", "nano_banana"],
                        help="image provider")
    parser.add_argument("--approve", action="store_true",
                        help="mark the existing outline approved and finish the run")
    parser.add_argument("--interactive", action="store_true",
                        help="approve the outline inline via a prompt")
    args = parser.parse_args()

    run(args.topic, args.brand, args.out, args.provider, args.approve, args.interactive)


if __name__ == "__main__":
    main()
