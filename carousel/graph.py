"""
graph.py — the same pipeline, expressed as a LangGraph ``StateGraph``.

``pipeline.py`` runs the flow as a straight line. This module runs the *identical*
steps as an explicit state machine, which buys three things a linear script can't
express cleanly:

* **A human-in-the-loop interrupt.** The ``review`` node calls LangGraph's
  ``interrupt()``; compiled with a checkpointer, the graph *pauses* there and can
  be resumed later with the human's decision — a first-class approval gate rather
  than a re-run of the script.
* **A real cycle.** After ``self_check`` a conditional edge routes back to
  ``remake`` (which re-renders only the slides that failed) and loops through
  ``self_check`` again, bounded by a retry counter in the state. This is control
  flow, not a for-loop hidden inside one function.
* **A typed, inspectable state.** Every node reads and writes one ``TypedDict``,
  so the whole run is a single serialisable object the checkpointer can persist.

The graph reuses the exact functions ``pipeline.py`` uses — nothing here
reimplements research, rendering, self-check, or captioning. It is an alternative
*orchestration* of the same execution layer.

    topic
      -> research            gather_research + draft_outline   (research.py)
      -> review              interrupt() approval gate         (review.py)
      -> generate_images     pluggable provider                (images.py)
      -> render              Sigma template                    (render.py)
      -> self_check          overlap / clipping / hierarchy    (selfcheck.py)
          |  any slide failed and retries left?
          |-- yes --> remake --> self_check   (the cycle)
          |-- no  --> captions                                 (captions.py)
      -> captions -> END

Run it, mirroring the plain CLI:

    python -m carousel.graph --topic "..." --brand brand.yaml
    python -m carousel.graph --topic "..." --brand brand.yaml --approve
    python -m carousel.graph --topic "..." --brand brand.yaml --interactive
"""
from __future__ import annotations

import argparse
import uuid
from pathlib import Path
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from config import load_settings

from .brand import Brand
from .captions import write_captions
from .images import get_image_provider
from .render import H, W, RenderResult, TextBox, render_slide
from .research import draft_outline, gather_research
from .review import approve as approve_outline
from .review import is_approved, load_outline, prompt_for_approval, write_outline
from .selfcheck import check_slide
from .themes import theme_for_slide

# Same bounds the linear pipeline uses, so both orchestrations behave identically.
MAX_REMAKE_RETRIES = 3
REMAKE_SCALE_STEP = 0.88


# ── Typed state ──────────────────────────────────────────────────────────────
class CarouselState(TypedDict, total=False):
    """The single object every node reads from and writes to.

    Kept to plain JSON-friendly types (str / list / dict / int / bool) so any
    checkpointer can serialise it. ``brand`` and ``settings`` are intentionally
    *not* stored — they are reconstructed from ``brand_path`` / the environment in
    each node, which keeps secrets out of the persisted state.
    """

    # Inputs
    topic: str
    brand_path: str
    out_dir: str
    provider_name: str

    # Research + review
    research: str
    slides: list[dict]      # the outline: one dict per slide
    style: str              # resolved Sigma style for the deck
    approved: bool          # flipped true once the human review gate clears

    # Render + self-check + remake cycle
    rendered: list[str]         # path per slide (index i-1 -> slide i)
    render_boxes: list[list[dict]]  # serialised layout boxes per slide
    scales: list[float]         # per-slide font scale; remake shrinks failures
    issues: list[dict]          # [{"slide": i, "problems": [...]}] from self-check
    failed: list[int]           # 1-based slide numbers that failed self-check
    retries: int                # remake attempts spent (bounds the cycle)

    # Output
    captions: list[str]


# ── Small (de)serialisation helpers for layout boxes ─────────────────────────
def _box_to_dict(box: TextBox) -> dict:
    return {"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1,
            "role": box.role, "font_size": box.font_size}


def _result_from_state(path: str, box_dicts: list[dict]) -> RenderResult:
    """Rebuild a ``RenderResult`` from stored, serialisable boxes so
    ``selfcheck.check_slide`` can inspect it without re-rendering."""
    boxes = [TextBox(**b) for b in box_dicts]
    return RenderResult(path=Path(path), width=W, height=H, boxes=boxes)


def _outline_path(state: CarouselState) -> Path:
    return Path(state["out_dir"]) / "outline.json"


# ── Nodes ────────────────────────────────────────────────────────────────────
def research_node(state: CarouselState) -> dict:
    """Stage 1-2: research the topic and draft the outline.

    Mirrors ``pipeline.py``: if an ``outline.json`` already exists we load it
    instead of paying for the LLM again, so a resumed run is cheap and
    deterministic.
    """
    settings = load_settings()
    brand = Brand.from_yaml(state["brand_path"])
    outline_path = _outline_path(state)

    if outline_path.exists():
        doc = load_outline(outline_path)
        print("[research] existing outline found; loading it.")
        return {"slides": doc.get("slides", []),
                "style": doc.get("style", brand.style)}

    topic = state["topic"]
    print(f"[research] researching {topic!r} ...")
    research = gather_research(topic, settings)
    print("[research] drafting outline ...")
    slides = draft_outline(topic, research, brand, settings)
    write_outline(topic, brand.style, slides, outline_path)
    return {"research": research, "slides": slides, "style": brand.style}


def review_node(state: CarouselState) -> dict:
    """The one human-in-the-loop gate, as a LangGraph ``interrupt()``.

    If the outline is already approved (e.g. a pre-edited file, or a resumed run)
    we pass straight through. Otherwise we ``interrupt`` — the compiled graph
    pauses here and returns control to the caller, who resumes with the human's
    decision via ``Command(resume=...)``.
    """
    outline_path = _outline_path(state)

    if not is_approved(outline_path):
        # Pauses the graph. On resume, interrupt() returns the value the caller
        # passed to Command(resume=...); we then re-read the (possibly edited)
        # outline the human just approved.
        interrupt({
            "gate": "human_review",
            "outline": str(outline_path),
            "message": ("Review and edit every slide's copy and image idea, then "
                        "approve to continue."),
        })

    doc = load_outline(outline_path)
    print("[review] approved; continuing with the edited outline.")
    return {"approved": True,
            "slides": doc.get("slides", state.get("slides", [])),
            "style": doc.get("style", state.get("style", ""))}


def generate_images_node(state: CarouselState) -> dict:
    """Stage 4: turn each slide's image idea into a picture via a pluggable
    provider. Failures are non-fatal — the slide renders without an image."""
    settings = load_settings()
    brand = Brand.from_yaml(state["brand_path"])
    provider_name = state["provider_name"]
    provider = get_image_provider(provider_name, settings, brand)
    print(f"[generate_images] provider={provider_name!r} ...")

    slides = state["slides"]
    img_dir = Path(state["out_dir"]) / "images"
    for i, slide in enumerate(slides, start=1):
        idea = (slide.get("image_idea") or "").strip()
        if not idea:
            continue
        img_path = img_dir / f"slide_{i:02d}.png"
        try:
            provider.generate(idea, img_path)
            slide["image"] = str(img_path)
        except Exception as exc:  # noqa: BLE001 - image is optional, keep rendering
            print(f"  slide {i}: image generation failed ({exc}); skipping image.")
    return {"slides": slides}


def render_node(state: CarouselState) -> dict:
    """Stage 5: render every slide on its Sigma theme at the current font scale."""
    brand = Brand.from_yaml(state["brand_path"])
    slides = state["slides"]
    style = state["style"]
    out = Path(state["out_dir"])

    scales = state.get("scales") or [1.0] * len(slides)
    rendered: list[str] = []
    render_boxes: list[list[dict]] = []
    for i, slide in enumerate(slides, start=1):
        theme = theme_for_slide(style, brand, i - 1)
        out_path = out / f"slide_{i:02d}.png"
        result = render_slide(slide, theme, brand, out_path, font_scale=scales[i - 1])
        rendered.append(str(result.path))
        render_boxes.append([_box_to_dict(b) for b in result.boxes])
    print(f"[render] rendered {len(rendered)} slides.")
    return {"scales": scales, "rendered": rendered, "render_boxes": render_boxes}


def self_check_node(state: CarouselState) -> dict:
    """Stage 6a: measure each rendered slide for overlap / clipping / weak
    hierarchy. Populates the ``failed`` list the router branches on."""
    issues: list[dict] = []
    failed: list[int] = []
    for i, (path, boxes) in enumerate(
        zip(state["rendered"], state["render_boxes"]), start=1
    ):
        problems = check_slide(_result_from_state(path, boxes))
        if problems:
            issues.append({"slide": i, "problems": problems})
            failed.append(i)
    if failed:
        print(f"[self_check] {len(failed)} slide(s) flagged: {failed}")
    else:
        print("[self_check] all slides pass.")
    return {"issues": issues, "failed": failed}


def remake_node(state: CarouselState) -> dict:
    """Stage 6b (the cycle): re-render only the failed slides at a smaller font
    scale, bump the retry counter, and hand back to ``self_check``."""
    brand = Brand.from_yaml(state["brand_path"])
    slides = state["slides"]
    style = state["style"]
    out = Path(state["out_dir"])

    scales = list(state["scales"])
    rendered = list(state["rendered"])
    render_boxes = [list(b) for b in state["render_boxes"]]

    retries = state.get("retries", 0) + 1
    for i in state["failed"]:
        scales[i - 1] *= REMAKE_SCALE_STEP
        theme = theme_for_slide(style, brand, i - 1)
        out_path = out / f"slide_{i:02d}.png"
        result = render_slide(slides[i - 1], theme, brand, out_path,
                              font_scale=scales[i - 1])
        rendered[i - 1] = str(result.path)
        render_boxes[i - 1] = [_box_to_dict(b) for b in result.boxes]
        print(f"  slide {i}: remade at {scales[i - 1]:.2f}x (attempt {retries}).")

    return {"scales": scales, "rendered": rendered,
            "render_boxes": render_boxes, "retries": retries}


def captions_node(state: CarouselState) -> dict:
    """Stage 7: draft two caption options and write them next to the slides."""
    settings = load_settings()
    brand = Brand.from_yaml(state["brand_path"])
    captions = write_captions(state["topic"], state["slides"], brand, settings)
    captions_path = Path(state["out_dir"]) / "captions.txt"
    with open(captions_path, "w") as fh:
        for n, cap in enumerate(captions, start=1):
            fh.write(f"--- Option {n} ---\n{cap}\n\n")
    print(f"[captions] wrote {len(captions)} option(s) to {captions_path}.")
    return {"captions": captions}


# ── Conditional edge (the cycle's decision) ──────────────────────────────────
def route_after_self_check(state: CarouselState) -> str:
    """Loop back to ``remake`` while slides fail and retries remain; otherwise
    fall through to ``captions``."""
    if state.get("failed") and state.get("retries", 0) < MAX_REMAKE_RETRIES:
        return "remake"
    return "captions"


# ── Graph assembly ───────────────────────────────────────────────────────────
def build_graph(checkpointer=None):
    """Compile the carousel ``StateGraph``.

    A checkpointer is required for the ``review`` interrupt to pause and resume;
    we default to an in-process ``MemorySaver``.
    """
    graph = StateGraph(CarouselState)

    graph.add_node("research", research_node)
    graph.add_node("review", review_node)
    graph.add_node("generate_images", generate_images_node)
    graph.add_node("render", render_node)
    graph.add_node("self_check", self_check_node)
    graph.add_node("remake", remake_node)
    graph.add_node("captions", captions_node)

    graph.add_edge(START, "research")
    graph.add_edge("research", "review")
    graph.add_edge("review", "generate_images")
    graph.add_edge("generate_images", "render")
    graph.add_edge("render", "self_check")
    # The conditional edge + the loop back through remake: this is the point.
    graph.add_conditional_edges(
        "self_check",
        route_after_self_check,
        {"remake": "remake", "captions": "captions"},
    )
    graph.add_edge("remake", "self_check")
    graph.add_edge("captions", END)

    return graph.compile(checkpointer=checkpointer or MemorySaver())


# ── Driver (mirrors pipeline.run) ────────────────────────────────────────────
def _pending_interrupt(app, config) -> bool:
    """True if the graph is paused on an interrupt awaiting a resume."""
    snapshot = app.get_state(config)
    return any(task.interrupts for task in snapshot.tasks)


def run(topic: str, brand_path: str, out_dir: str, provider_name: str,
        approve: bool, interactive: bool) -> dict:
    """Execute the graph. Returns a summary dict; prints progress to stdout.

    The ``review`` node interrupts before generation. This driver resolves the
    gate the same three ways ``pipeline.py`` does — ``--approve`` (flip the file),
    ``--interactive`` (prompt), or stop and report ``awaiting_review`` — then
    resumes the graph with ``Command(resume=...)``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    outline_path = out / "outline.json"

    app = build_graph()
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    initial: CarouselState = {
        "topic": topic,
        "brand_path": brand_path,
        "out_dir": str(out),
        "provider_name": provider_name,
        "retries": 0,
    }

    result = app.invoke(initial, config)

    # Resolve the human-review interrupt, then resume, until the graph completes.
    while _pending_interrupt(app, config):
        print(f"\n[review gate] outline written to: {outline_path}")
        if approve:
            approve_outline(outline_path)
        elif interactive:
            prompt_for_approval(outline_path)  # flips the file on 'yes'

        if is_approved(outline_path):
            result = app.invoke(Command(resume=True), config)
        else:
            print("Not yet approved. Edit the outline and re-run with --approve.")
            return {"status": "awaiting_review", "outline": str(outline_path)}

    print("\nDone.")
    print(f"  Slides:   {out}/slide_*.png")
    print(f"  Captions: {out}/captions.txt")
    return {
        "status": "complete",
        "slides": result.get("rendered", []),
        "captions": result.get("captions", []),
        "captions_path": str(out / "captions.txt"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Branded Instagram carousel producer (LangGraph orchestration).")
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
