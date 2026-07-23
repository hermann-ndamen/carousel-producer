# Carousel Producer — container image.
#
# Ships the full pipeline (both the plain orchestrator, carousel.pipeline, and the
# LangGraph orchestration, carousel.graph). No keys are baked in: pass them at run
# time via -e / --env-file, e.g.
#
#   docker build -t carousel-producer .
#   docker run --rm carousel-producer                       # prints CLI help
#   docker run --rm -e ANTHROPIC_API_KEY=... \
#       -v "$PWD/output:/app/output" \
#       carousel-producer \
#       python -m carousel.graph --topic "how to price a project" \
#           --brand examples/example_brand.yaml --interactive
FROM python:3.12-slim

# Faster, quieter, unbuffered Python in a container.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so the layer caches across code changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application.
COPY . .

# Generated decks land here; mount a volume to keep them on the host.
RUN mkdir -p /app/output

# Default: show how to run. Override the command to produce a deck (see header).
CMD ["python", "-m", "carousel.pipeline", "--help"]
