# Build stage: resolve and install dependencies with uv, then install the
# project itself as a wheel (non-editable) so the runtime image only needs
# the virtualenv. https://docs.astral.sh/uv/guides/integration/docker/
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0 UV_NO_DEV=1
WORKDIR /app

# Dependency layer: cached until pyproject.toml/uv.lock change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY pyproject.toml uv.lock ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

# Runtime stage: no uv, no build tools, no source tree — just the venv.
FROM python:3.14-slim@sha256:cea0e6040540fb2b965b6e7fb5ffa00871e632eef63719f0ea54bca189ce14a6

# Numeric UID so Kubernetes runAsNonRoot can verify the user.
RUN useradd --system --create-home --uid 10001 arhiteq
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY --from=builder --chown=arhiteq:arhiteq /app/.venv /app/.venv

USER 10001:10001
WORKDIR /app

EXPOSE 8080
CMD ["uvicorn", "arhiteq_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
