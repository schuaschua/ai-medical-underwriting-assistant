# The `verdict` image: the FastAPI service and the migrations it ships with.
# Build from the repository root, which is the uv workspace:
#   docker build -f services/verdict/Dockerfile -t aiuw-verdict:dev .

# --- 1. Install the service and its locked dependencies into a virtualenv ----
FROM python:3.13.16-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/contracts/ packages/contracts/
COPY services/verdict/pyproject.toml services/verdict/
# The migrations are inside the package, so the image carries the head
# revision that readiness compares the database with.
COPY services/verdict/src/ services/verdict/src/
# --frozen: install exactly what uv.lock says. The lock cannot be re-checked
# here because the other workspace members are not copied in; CI does that
# with `uv sync --locked`.
RUN uv sync --frozen --no-dev --no-editable --package verdict

# --- 2. The runtime image ----------------------------------------------------
FROM python:3.13.16-slim-bookworm
RUN groupadd --gid 10001 verdict \
    && useradd --uid 10001 --gid verdict --no-create-home --shell /usr/sbin/nologin verdict
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VERDICT_HOST=0.0.0.0 \
    VERDICT_PORT=8006
# Numeric, so the platform can verify the process is not root.
USER 10001:10001
EXPOSE 8006
CMD ["python", "-m", "verdict"]
