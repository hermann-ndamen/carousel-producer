"""
research.py — topic research + outline drafting (the LLM stage).

Two responsibilities, both powered by Claude:

1. ``gather_research(topic)`` runs a live web search to collect current facts and
   angles on the topic.
2. ``draft_outline(topic, research, brand)`` turns the topic, that research, and
   the brand voice into a slide-by-slide outline — the copy for every slide plus
   an image idea per slide.

The outline is what the human reviews and edits before anything is generated or
rendered (see ``review.py``).

Both functions degrade gracefully: with no ``ANTHROPIC_API_KEY`` set they return
a sensible offline draft so the pipeline still runs end-to-end.
"""
from __future__ import annotations

import json

from config import Settings

from .brand import Brand

# The web-search server tool with dynamic filtering (Opus 4.6+ / Sonnet 4.6+).
_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}


def _client(settings: Settings):
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover
        raise ImportError("pip install anthropic") from exc
    # Anthropic() resolves ANTHROPIC_API_KEY (or an ant-login profile) itself.
    return anthropic.Anthropic()


# ── 1. Research ──────────────────────────────────────────────────────────────
def gather_research(topic: str, settings: Settings) -> str:
    """Return a compact brief of facts and angles for ``topic`` via web search."""
    if not settings.has_anthropic:
        return _offline_research(topic)

    client = _client(settings)
    resp = client.messages.create(
        model=settings.model,
        max_tokens=2000,
        tools=[_WEB_SEARCH_TOOL],
        messages=[{
            "role": "user",
            "content": (
                f"Research the topic: {topic!r}. Use web search to gather 5-8 "
                f"current, specific, verifiable facts, plus 2-3 angles that would "
                f"make a strong Instagram carousel. Return a tight brief: a bulleted "
                f"list of facts (with the essential numbers) and a short 'angles' "
                f"section. No preamble."
            ),
        }],
    )
    return _text_of(resp) or _offline_research(topic)


# ── 2. Outline drafting ──────────────────────────────────────────────────────
_OUTLINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"type": "string",
                             "enum": ["hook", "body", "stat", "cta", "prompt"]},
                    "headline": {"type": "string"},
                    "subheadline": {"type": "string"},
                    "text": {"type": "string"},
                    "accent_words": {"type": "array", "items": {"type": "string"}},
                    "stat": {"type": "string"},
                    "context": {"type": "string"},
                    "cta_text": {"type": "string"},
                    "reason_text": {"type": "string"},
                    "follow_text": {"type": "string"},
                    "prompt_text": {"type": "string"},
                    "prompt_label": {"type": "string"},
                    "image_idea": {"type": "string"},
                },
                "required": ["type", "image_idea"],
            },
        }
    },
    "required": ["slides"],
}


def draft_outline(topic: str, research: str, brand: Brand, settings: Settings) -> list[dict]:
    """Draft a 6-8 slide outline as a list of slide dicts.

    Each slide has a ``type`` (hook/body/stat/cta/prompt), the copy for that type,
    and an ``image_idea`` describing the picture to make for it.
    """
    if not settings.has_anthropic:
        return _offline_outline(topic, brand)

    client = _client(settings)
    system = (
        "You write Instagram carousels. One idea per slide, short natural "
        "sentences a 6th grader could follow, no AI-ish phrasing. The deck opens "
        "with a hook, delivers real value in the middle, and ends with a CTA. Use "
        "'accent_words' to mark 1-3 words per headline to highlight. Every slide "
        "needs a concrete 'image_idea'."
    )
    prompt = (
        f"Brand: {brand.name} ({brand.handle}). Style: {brand.style}.\n"
        f"Topic: {topic}\n\n"
        f"Research:\n{research}\n\n"
        "Produce a 6-8 slide outline. Slide 1 must be a 'hook'; the last slide "
        "must be a 'cta'. Include at least one 'stat' slide if the research has a "
        "strong number, and a 'prompt' slide if a copy-paste prompt would add "
        "value. Return JSON matching the schema."
    )
    resp = client.messages.create(
        model=settings.model,
        max_tokens=4000,
        system=system,
        output_config={"format": {"type": "json_schema", "schema": _OUTLINE_SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    text = _text_of(resp)
    try:
        slides = json.loads(text).get("slides", [])
    except (json.JSONDecodeError, AttributeError):
        return _offline_outline(topic, brand)
    return slides or _offline_outline(topic, brand)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _text_of(resp) -> str:
    parts = [b.text for b in getattr(resp, "content", []) if getattr(b, "type", "") == "text"]
    return "\n".join(parts).strip()


def _offline_research(topic: str) -> str:
    return (
        f"(offline draft — no ANTHROPIC_API_KEY set)\n"
        f"Facts about {topic}:\n"
        f"- Point one about {topic}.\n"
        f"- Point two, with a number.\n"
        f"- A common mistake people make.\n"
        f"Angles:\n- The counter-intuitive take.\n- The practical how-to.\n"
    )


def _offline_outline(topic: str, brand: Brand) -> list[dict]:
    return [
        {"type": "hook", "headline": f"The truth about {topic}",
         "subheadline": "Most people get this wrong.",
         "accent_words": ["truth"],
         "image_idea": f"a striking symbolic image about {topic}"},
        {"type": "body", "headline": "Start here",
         "text": "One clear idea, explained in a sentence anyone can follow.",
         "accent_words": ["here"],
         "image_idea": f"a simple diagram illustrating {topic}"},
        {"type": "stat", "stat": "80%",
         "context": f"of people never do this with {topic}.",
         "image_idea": ""},
        {"type": "body", "headline": "The move",
         "text": "The single most useful action to take today.",
         "accent_words": ["move"],
         "image_idea": f"a photo of someone applying {topic}"},
        {"type": "prompt", "headline": "Try this prompt",
         "text": "Paste it into your assistant and swap in your details.",
         "prompt_text": f"Act as an expert. Help me with {topic}.\nMy goal is: ___",
         "prompt_label": "PASTE THIS", "image_idea": ""},
        {"type": "cta", "cta_text": "Follow for more",
         "accent_words": ["Follow"],
         "reason_text": f"I break down {topic} every week.",
         "follow_text": f"{brand.handle}", "image_idea": ""},
    ]
