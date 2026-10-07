# The `retrieval` image: the FastAPI service, the ingestion job and the
# migrations they ship with. The job runs on this image with another command:
#   python -m retrieval.ingest
# Build from the repository root, which is the uv workspace:
#   docker build -f services/retrieval/Dockerfile -t aiuw-retrieval:dev .

# --- 1. Install the service and its locked dependencies into a virtualenv ----
FROM python:3.13.16-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts/ packages/contracts/
COPY services/retrieval/pyproject.toml services/retrieval/
# The migrations are inside the package, so the image carries the head
# revision that readiness compares the database with.
COPY services/retrieval/src/ services/retrieval/src/
# --frozen: install exactly what uv.lock says. The lock cannot be re-checked
# here because the other workspace members are not copied in; CI does that
# with `uv sync --locked`.
RUN uv sync --frozen --no-dev --no-editable --package retrieval

# --- 2. The runtime image ----------------------------------------------------
FROM python:3.13.16-slim-bookworm
RUN groupadd --gid 10001 retrieval \
    && useradd --uid 10001 --gid retrieval --no-create-home --shell /usr/sbin/nologin retrieval
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RETRIEVAL_HOST=0.0.0.0 \
    RETRIEVAL_PORT=8004
# Numeric, so the platform can verify the process is not root.
USER 10001:10001
EXPOSE 8004
CMD ["python", "-m", "retrieval"]
