# Build stage: resolve and install dependencies with uv, then install the
# project itself as a wheel (non-editable) so the runtime image only needs
# the virtualenv. https://docs.astral.sh/uv/guides/integration/docker/
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder
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
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4

# Numeric UID so Kubernetes runAsNonRoot can verify the user.
RUN useradd --system --create-home --uid 10001 worker
ENV PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
COPY --from=builder --chown=worker:worker /app/.venv /app/.venv

USER 10001:10001
WORKDIR /app

# Prometheus metrics
EXPOSE 9090
# livekit-agents worker health check port (default 8081)
EXPOSE 8081

CMD ["python", "-m", "arhiteq_worker.main", "start"]
