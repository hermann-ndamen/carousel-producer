# Carousel Producer

Give it a topic. It researches the topic live, drafts a slide-by-slide outline,
pauses for one human review, generates an image per slide, lays everything out on
a branded **Sigma** template, checks its own work for overlaps and clipping, and
writes two caption options. The output is a finished, on-brand Instagram carousel
(1080 × 1350) plus captions — not a pile of assets you still have to assemble.

The whole thing is driven by one topic string and one brand file. Branding is
configuration, so the same engine renders anyone's deck from their own colours,
fonts, handle, and assets.

## Pipeline

```mermaid
flowchart TD
    A([Topic in]) --> B[Research + brand<br/><i>live web search · load brand context</i>]
    B --> C{{Review &amp; edit<br/><b>the one human step</b>}}
    C -- edit copy &amp; image ideas, approve --> D[Design<br/><i>Sigma template · glow background</i>]
    C -. not approved .-> C
    D --> E[Self-check<br/><i>overlap · hierarchy · clipping</i>]
    E -- fails --> F[Remake<br/><i>re-render smaller</i>]
    F --> E
    E -- passes --> G[Two captions]
    G --> H([Finished carousel + captions])
```

The image step sits inside **Design**: for each slide's image idea, a pluggable
provider produces the picture, then the slide is laid out on its Sigma template.

## Two orchestrations, one execution layer

The same seven steps ship in two interchangeable orchestrations over the identical
functions in `research.py`, `render.py`, `selfcheck.py`, etc.:

- **`carousel.pipeline`** — the plain orchestrator. A straight-line script. Easiest
  to read and the default entry point.
- **`carousel.graph`** — a [LangGraph](https://langchain-ai.github.io/langgraph/)
  `StateGraph`. The same steps as an explicit state machine with a typed state, a
  first-class human-in-the-loop **interrupt**, and a real **self-check → remake
  cycle**. See [LangGraph orchestration](#langgraph-orchestration).

Neither reimplements a step; they are two ways to wire the same execution layer.

## How it works

1. **Topic in.** You pass one topic string.
2. **Research + brand.** A live web search (Claude's `web_search` tool) gathers
   current facts and angles; the brand file is loaded for voice, handle, colours,
   and assets.
3. **Review & edit — the one human step.** Before any image or slide is made, the
   outline (every slide's copy + its image idea) is written to `outline.json` and
   the run pauses. You edit any line or image concept and approve. Nothing else in
   the pipeline asks for a human.
4. **Images.** For each slide's image idea, a pluggable `ImageProvider` makes the
   picture. Three ship: a stub placeholder (no keys), a web image-search provider,
   and a Nano Banana (Gemini) generator. Adding a provider is one new class.
5. **Design.** Each slide is composited on one of three Sigma templates — Sigma
   Dark, Sigma Light, or Sigma Hybrid (alternating) — with a NumPy radial-glow
   background, header, headline/body, image card, and CTA headshot.
6. **Self-check + remake.** The renderer records the bounding box of everything it
   draws. The checker measures those boxes: no clipping past the safe area, no
   overlaps, and a title that out-sizes body copy. Any slide that fails is
   re-rendered at a smaller font scale, up to a retry cap.
7. **Two captions.** The producer drafts two distinct captions and lets you pick.

## Branding is configuration

Nothing about a specific person or company lives in the code. A single
`brand.yaml` supplies everything visual:

```yaml
name: "Your Brand"
handle: "@yourhandle"
style: "sigma-dark"          # sigma-dark | sigma-light | sigma-hybrid
colors:
  primary: "#ed5500"         # accent + highlighted words + CTA ring
  secondary: "#2ec0ff"
  dark_bg: "#000000"
  light_bg: "#f8f8f8"
fonts:
  regular: "assets/fonts/Poppins-Regular.ttf"
  # ... other weights, plus a 'mono' for prompt slides
assets:
  logo: "assets/logo.png"
  headshot: "assets/headshot.jpg"
```

The engine reads colours, fonts, handle, and assets from this object and never
hard-codes an identity. See [`brand.example.yaml`](brand.example.yaml) for the
full schema, and [`examples/example_brand.yaml`](examples/example_brand.yaml) for
a zero-asset file you can run immediately.

## Tech stack

| Layer            | Choice                                   | Why |
| ---------------- | ---------------------------------------- | --- |
| Rendering        | Pillow (PIL)                             | Precise text layout and compositing |
| Backgrounds      | NumPy                                    | Vectorised gaussian radial-glow |
| Research/copy    | Anthropic Claude (`claude-opus-4-8`)     | Web search + structured outline + captions |
| Image generation | Google Gemini "Nano Banana" (`google-genai`) | On-brand generated imagery |
| Image search     | Any JSON image-search API (`requests`)   | Real photos as an alternative source |
| Config           | PyYAML + python-dotenv                   | Brand as data; secrets in env |

## Project structure

```
carousel-producer/
├── README.md
├── LICENSE
├── .env.example
├── .gitignore
├── Dockerfile                # container image (both orchestrations)
├── .dockerignore
├── requirements.txt
├── config.py                 # settings from environment
├── brand.example.yaml        # brand config schema
├── carousel/
│   ├── pipeline.py           # plain orchestrator (topic -> finished deck)
│   ├── graph.py              # LangGraph orchestration (same steps, state machine)
│   ├── research.py           # web-search research + outline drafting (Claude)
│   ├── review.py             # human-in-the-loop review gate
│   ├── images.py             # ImageProvider interface + stub / web-search / Nano Banana
│   ├── render.py             # Sigma renderers (PIL + NumPy glow)
│   ├── themes.py             # Sigma Dark / Light / Hybrid tokens
│   ├── selfcheck.py          # overlap / hierarchy / clipping checks
│   ├── captions.py           # two caption options (Claude)
│   └── brand.py              # loads brand.yaml
└── examples/
    └── example_brand.yaml    # runnable, no external assets
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # add keys (optional — see below)
```

Run it — the first pass stops at the review gate, the second finishes:

```bash
# 1. Research + draft; writes output/outline.json and pauses
python -m carousel.pipeline --topic "how to price a freelance project" \
    --brand examples/example_brand.yaml

# 2. Edit output/outline.json, then approve to finish
python -m carousel.pipeline --topic "how to price a freelance project" \
    --brand examples/example_brand.yaml --approve
```

Or approve inline in one run with `--interactive`. Choose an image source with
`--provider stub|web_search|nano_banana` (defaults to `stub`).

**Runs with no keys.** With no API keys set, research and captions fall back to
sensible offline drafts and the `stub` provider draws labelled placeholder image
cards — so you can exercise the full pipeline, then add keys to make it live.

## LangGraph orchestration

`carousel/graph.py` expresses the pipeline as a LangGraph `StateGraph`. Every node
reads and writes one typed state (`CarouselState`, a `TypedDict` holding the topic,
brand path, research, the slide outline, the `approved` flag, rendered paths,
per-slide self-check issues, the retry counter, and the captions), and each node
wraps an *existing* function — nothing here reimplements research, rendering,
self-check, or captioning.

```mermaid
flowchart TD
    START([START]) --> R[research<br/><i>gather_research + draft_outline</i>]
    R --> V{{review<br/><b>interrupt · human approval</b>}}
    V -- Command resume --> G[generate_images<br/><i>pluggable provider</i>]
    G --> D[render<br/><i>Sigma template</i>]
    D --> S[self_check<br/><i>overlap · clipping · hierarchy</i>]
    S -- any slide failed &amp; retries left --> M[remake<br/><i>re-render failures smaller</i>]
    M --> S
    S -- all pass / retries spent --> C[captions]
    C --> E([END])
```

Two things a straight-line script can't express cleanly:

- **Human-in-the-loop interrupt.** The `review` node calls LangGraph's
  `interrupt()`. Because the graph is compiled with a checkpointer (`MemorySaver`),
  it *pauses* at the gate and hands control back to the caller, who resumes with the
  human's decision via `Command(resume=...)` — a genuine approval gate, not a
  re-run of the script. `--approve` / `--interactive` resolve it exactly as the
  plain pipeline does.
- **The self-check → remake cycle.** After `self_check`, a conditional edge
  (`route_after_self_check`) routes back to `remake` while any slide failed *and*
  the retry counter is under `MAX_REMAKE_RETRIES`; `remake` re-renders only the
  failed slides at a smaller font scale and loops through `self_check` again.
  Otherwise it falls through to `captions`. The loop back into `self_check` is the
  point — real cyclic control flow, bounded by state.

Run it exactly like the plain pipeline:

```bash
# First pass: research + draft, then pause at the review interrupt
python -m carousel.graph --topic "how to price a freelance project" \
    --brand examples/example_brand.yaml

# Edit output/outline.json, then approve to resume and finish
python -m carousel.graph --topic "how to price a freelance project" \
    --brand examples/example_brand.yaml --approve
```

## Run with Docker

The image bundles both orchestrations. No keys are baked in — pass them at run time.

```bash
docker build -t carousel-producer .

# Default command prints the CLI help
docker run --rm carousel-producer

# Produce a deck (mount ./output to keep it; add -e KEY=... to go live)
docker run --rm -v "$PWD/output:/app/output" \
    -e ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" \
    carousel-producer \
    python -m carousel.graph --topic "how to price a project" \
        --brand examples/example_brand.yaml --interactive
```

With no keys set it still runs end-to-end on offline drafts and the `stub` image
provider.

## Security notes

- Secrets live only in `.env` (git-ignored). `.env.example` contains placeholders.
- No credential, token, personal email, phone number, or private asset is baked
  into the code — brand identity is supplied at runtime via `brand.yaml`.
- Generated output and brand assets are git-ignored so nothing personal is
  committed by accident.

## License

MIT © 2026 Hermann Ndamen — <https://www.hermannndamen.com>
