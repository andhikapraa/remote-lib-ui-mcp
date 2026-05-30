# Multi-stage build using the official uv image. Produces a stdio MCP server.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies first (cached layer), without the project or dev deps.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --no-install-project --no-dev --no-editable || true

ADD . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable

FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Credentials are provided at runtime via env (never baked into the image):
#   docker run -e REMOTE_LIB_USERNAME=... -e REMOTE_LIB_PASSWORD=... <image>
ENTRYPOINT ["remote-lib-ui-mcp"]
