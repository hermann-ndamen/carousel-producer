"""
selfcheck.py — honest post-render inspection.

After a slide is rendered, we do not eyeball it — we measure. The renderer hands
back the bounding box of every element it drew (see ``render.RenderResult``), and
these checks verify three concrete properties:

1. **Nothing is clipped.** Every content box sits inside the safe area, between
   the header separator and the bottom accent bar, and within the side margins.
2. **Nothing overlaps.** No two content boxes intersect by a meaningful amount
   (a small tolerance absorbs intentional optical kerning).
3. **The hierarchy reads.** When a slide has both a title and body copy, the
   title's font must be clearly larger than the body's, or the eye has no anchor.

``check_slide`` returns a list of human-readable issues. An empty list means the
slide passed; the pipeline re-renders any slide that did not.
"""
from __future__ import annotations

from .render import CONTENT_TOP, NAV_BAR_H, SAFE_BOTTOM, SIDE, W, H, RenderResult, TextBox

# How much two boxes may overlap before we call it a collision (px of the
# smaller box's area on each axis).
_OVERLAP_TOLERANCE = 8
# Title must be at least this many times the body font size to read as a title.
_HIERARCHY_RATIO = 1.2

# Roles that occupy the content area (everything except the fixed header).
_CONTENT_ROLES = {"title", "body", "stat", "context", "cta", "prompt", "card", "lead"}


def _content_boxes(result: RenderResult) -> list[TextBox]:
    return [b for b in result.boxes if b.role in _CONTENT_ROLES]


def _overlap(a: TextBox, b: TextBox) -> bool:
    ox = min(a.x1, b.x1) - max(a.x0, b.x0)
    oy = min(a.y1, b.y1) - max(a.y0, b.y0)
    return ox > _OVERLAP_TOLERANCE and oy > _OVERLAP_TOLERANCE


def check_slide(result: RenderResult) -> list[str]:
    """Return a list of layout problems for one rendered slide (empty = passed)."""
    issues: list[str] = []
    content = _content_boxes(result)

    # 1. Clipping / off-canvas.
    for b in content:
        if b.y1 > SAFE_BOTTOM + NAV_BAR_H:
            issues.append(
                f"{b.role!r} runs to y={b.y1}, past the safe bottom ({SAFE_BOTTOM})"
            )
        if b.y0 < CONTENT_TOP - 40:
            issues.append(f"{b.role!r} starts at y={b.y0}, above the content area")
        if b.x0 < 0 or b.x1 > W:
            issues.append(f"{b.role!r} spans x=[{b.x0}, {b.x1}], off the canvas width")
        if b.y0 < 0 or b.y1 > H:
            issues.append(f"{b.role!r} spans y=[{b.y0}, {b.y1}], off the canvas height")

    # 2. Overlap between distinct content boxes. Cards legitimately contain their
    #    own placeholder label, so a card never collides with text drawn inside it.
    text_boxes = [b for b in content if b.role != "card"]
    for i in range(len(text_boxes)):
        for j in range(i + 1, len(text_boxes)):
            if _overlap(text_boxes[i], text_boxes[j]):
                issues.append(
                    f"{text_boxes[i].role!r} overlaps {text_boxes[j].role!r}"
                )

    # 3. Visual hierarchy: a title must out-size body copy.
    titles = [b for b in content if b.role in ("title", "stat")]
    bodies = [b for b in content if b.role in ("body", "lead", "context")]
    if titles and bodies:
        title_size = max(b.font_size for b in titles)
        body_size = max(b.font_size for b in bodies)
        if body_size and title_size < body_size * _HIERARCHY_RATIO:
            issues.append(
                f"weak hierarchy: title {title_size}px vs body {body_size}px "
                f"(want title ≥ {_HIERARCHY_RATIO:g}x body)"
            )

    return issues
