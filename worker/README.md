# arhiteq-worker

LiveKit Agents voice worker for Arhiteq (agent name `arhiteq-agent`).
One job per call: Cartesia ink-whisper STT → Gemini LLM → Cartesia Sonic TTS,
with Retell-compatible dynamic variables, custom function tools, AMD/voicemail
handling, lifecycle reporting to the control plane, and GCS call recordings.

Binding contracts: `docs/ARCHITECTURE.md`, `docs/INTERNAL_API.md`,
`docs/RETELL_INTEGRATION_MAP.md`.

## Layout

| File | Purpose |
|---|---|
| `main.py` | Worker entrypoint, session/pipeline assembly, lifecycle + finalize |
| `config.py` | Typed call-config parsing (`/internal/calls/{id}/config` shape) |
| `internal_api.py` | Control-plane client (`X-Internal-Token`) |
| `variables.py` | `{{var}}` dynamic-variable resolution (pure) |
| `tools.py` | Retell tool declarations → livekit function tools (flat-args HTTP bridge, end_call, transfer_call) |
| `amd.py` | Telnyx AMD attributes + Gemini greeting classifier, voicemail_option |
| `state.py` | Per-call state, transcript formatting, finalize payload |
| `metrics.py` | Prometheus series + the multiprocess exporter options livekit serves on `:9090` |

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `LIVEKIT_URL` | yes | LiveKit server URL (`wss://…`) |
| `LIVEKIT_API_KEY` | yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | yes | LiveKit API secret |
| `ARHITEQ_API_URL` | yes | Control-plane base URL (no trailing slash) |
| `ARHITEQ_INTERNAL_TOKEN` | yes | Shared secret for `/internal/*` (sent as `X-Internal-Token`) |
| `CARTESIA_API_KEY` | yes | Cartesia STT + TTS |
| `GOOGLE_API_KEY` | yes | Google GenAI (Gemini LLM + AMD greeting classifier) |
| `RECORDINGS_GCS_BUCKET` | no | GCS bucket for room-composite recordings; unset → no recording, `recording_url: null` |
| `GOOGLE_APPLICATION_CREDENTIALS` | no | Service-account JSON path passed to LiveKit Egress for GCS upload |
| `ARHITEQ_GEMINI_MODEL` | no | Fallback Gemini model when the agent's `llm.model` is not a Gemini model (default `gemini-2.5-flash`) |
| `ARHITEQ_CARTESIA_TTS_MODEL` | no | Cartesia TTS model (default `sonic-2`) |
| `ARHITEQ_CARTESIA_STT_MODEL` | no | Cartesia STT model (default `ink-whisper`) |
| `ARHITEQ_DIAL_TIMEOUT_S` | no | Outbound answer-wait timeout (default `60`) |
| `ARHITEQ_METRICS_PORT` | no | Prometheus port (default `9090`; `0` disables the exporter — the k8s probes target this port, so leave it alone in deployments) |
| `PROMETHEUS_MULTIPROC_DIR` | no | Where job subprocesses write their metric files (default `<tmp>/arhiteq-worker-metrics`); must be pod-local and ephemeral |
| `LOG_LEVEL` | no | Python log level (default `INFO`) |

## Retell agent-option mappings

Documented in `main.py`; summary:

- `interruption_sensitivity` → `allow_interruptions` + `min_interruption_duration` (0.1s–1.5s)
- `responsiveness` → endpointing `min_delay` (0.2s–1.2s) / `max_delay` (3s–6s)
- `enable_backchannel` → prompt instruction (no native livekit knob)
- `max_call_duration_ms` → watchdog → `max_duration_reached`
- `end_call_after_silence_ms` → `user_away_timeout` → `inactivity`
- `voice_speed` → Cartesia `speed` (−1..1); `voice_temperature` has no Cartesia equivalent (ignored)

## Metrics (`:9090/metrics`)

- `arhiteq_worker_jobs_total{direction}`
- `arhiteq_tool_calls_total{tool,outcome}`
- `arhiteq_llm_ttfb_seconds`, `arhiteq_tts_ttfb_seconds`
- `arhiteq_amd_detections_total{result}`

Every counter above is incremented inside a livekit job subprocess, one per
call, so the endpoint runs in prometheus_client multiprocess mode: `_run()`
passes livekit `prometheus_port` + `prometheus_multiproc_dir`, livekit exports
`PROMETHEUS_MULTIPROC_DIR` before spawning any job process, and its `/metrics`
handler aggregates the per-process files (livekit's own `lk_agents_*` series
come along for free). Two things follow:

- A series is absent until some job writes it — there are no zero-valued
  series at startup, so alerts must treat "missing" as "none yet".
- `PROMETHEUS_MULTIPROC_DIR` must be pod-local and ephemeral. livekit wipes it
  at worker start; pointing it at a shared or persistent volume would
  resurrect counts from an earlier run.

## Development

```bash
uv sync                                    # installs runtime deps + dev group
uv run python -m arhiteq_worker.main dev  # local dev against LIVEKIT_URL
uv run python -m arhiteq_worker.main start
uv run pytest                              # unit tests (variables + tool-bridge contract)
```

## Docker

```bash
docker build -t arhiteq-worker .
docker run --env-file .env arhiteq-worker
```
