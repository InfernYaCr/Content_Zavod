# One image for both processes (ADR-0004): docker-compose.yml picks bot_main.py or worker_main.py.
# Python matches `requires-python` in pyproject.toml; uv pinned so `uv sync --frozen` behaves
# the same on every build.
FROM ghcr.io/astral-sh/uv:0.11.28-python3.12-trixie-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first: this layer is rebuilt only when pyproject.toml/uv.lock change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-dev --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

RUN useradd --system --uid 10001 --no-create-home app
USER app

CMD ["python", "bot_main.py"]
