# architeq-worker

LiveKit Agents voice worker for Architeq (agent name `architeq-agent`).
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
| `metrics.py` | Prometheus exporter on `:9090` |

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `LIVEKIT_URL` | yes | LiveKit server URL (`wss://…`) |
| `LIVEKIT_API_KEY` | yes | LiveKit API key |
| `LIVEKIT_API_SECRET` | yes | LiveKit API secret |
| `ARCHITEQ_API_URL` | yes | Control-plane base URL (no trailing slash) |
| `ARCHITEQ_INTERNAL_TOKEN` | yes | Shared secret for `/internal/*` (sent as `X-Internal-Token`) |
| `CARTESIA_API_KEY` | yes | Cartesia STT + TTS |
| `GOOGLE_API_KEY` | yes | Google GenAI (Gemini LLM + AMD greeting classifier) |
| `RECORDINGS_GCS_BUCKET` | no | GCS bucket for room-composite recordings; unset → no recording, `recording_url: null` |
| `GOOGLE_APPLICATION_CREDENTIALS` | no | Service-account JSON path passed to LiveKit Egress for GCS upload |
| `ARCHITEQ_GEMINI_MODEL` | no | Fallback Gemini model when the agent's `llm.model` is not a Gemini model (default `gemini-2.5-flash`) |
| `ARCHITEQ_CARTESIA_TTS_MODEL` | no | Cartesia TTS model (default `sonic-2`) |
| `ARCHITEQ_CARTESIA_STT_MODEL` | no | Cartesia STT model (default `ink-whisper`) |
| `ARCHITEQ_DIAL_TIMEOUT_S` | no | Outbound answer-wait timeout (default `60`) |
| `ARCHITEQ_METRICS_PORT` | no | Prometheus port (default `9090`) |
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

- `architeq_worker_jobs_total{direction}`
- `architeq_tool_calls_total{tool,outcome}`
- `architeq_llm_ttfb_seconds`, `architeq_tts_ttfb_seconds`
- `architeq_amd_detections_total{result}`

Jobs run in subprocesses; each process starts the exporter if the port is
free. For exact aggregates use prometheus_client multiprocess mode (TODO).

## Development

```bash
pip install -e ".[dev]"
python main.py dev        # local dev against LIVEKIT_URL
python main.py start      # production mode
pytest                    # unit tests (variables + tool-bridge contract)
```

## Docker

```bash
docker build -t architeq-worker .
docker run --env-file .env architeq-worker
```
