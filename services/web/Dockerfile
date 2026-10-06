# The `web` image: the built SPA and the FastAPI service that serves it.
# Build from the repository root, which is the uv workspace:
#   docker build -f services/web/Dockerfile -t aiuw-web:dev .

# --- 1. Build the SPA ---------------------------------------------------------
FROM node:24.21.0-bookworm-slim AS spa
WORKDIR /spa
COPY services/web/spa/package.json services/web/spa/package-lock.json ./
RUN npm ci
COPY services/web/spa/ ./
RUN npm run build

# --- 2. Install the service and its locked dependencies into a virtualenv ----
FROM python:3.13.16-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts/ packages/contracts/
COPY services/web/pyproject.toml services/web/
COPY services/web/src/ services/web/src/
# --frozen: install exactly what uv.lock says. The lock cannot be re-checked
# here because the other workspace members are not copied in; CI does that
# with `uv sync --locked`.
RUN uv sync --frozen --no-dev --no-editable --package web

# --- 3. The runtime image ----------------------------------------------------
FROM python:3.13.16-slim-bookworm
RUN groupadd --gid 10001 web \
    && useradd --uid 10001 --gid web --no-create-home --shell /usr/sbin/nologin web
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=spa /spa/dist /app/spa
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=8000 \
    WEB_SPA_DIR=/app/spa
# Numeric, so the platform can verify the process is not root.
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "web"]
