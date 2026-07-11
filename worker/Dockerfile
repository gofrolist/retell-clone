FROM python:3.14-slim

WORKDIR /app

# Keep this list in sync with pyproject.toml dependencies (separate layer so
# dependency downloads are cached across source-only changes).
RUN pip install --no-cache-dir \
    "livekit-agents[cartesia,google]>=1.2,<2.0" \
    "httpx>=0.27" \
    "prometheus-client>=0.20"

COPY *.py ./

RUN useradd --system --create-home worker
USER worker

# Prometheus metrics
EXPOSE 9090
# livekit-agents worker health check port (default 8081)
EXPOSE 8081

CMD ["python", "main.py", "start"]
