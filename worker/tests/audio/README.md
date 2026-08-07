# Layer 3 — synthetic audio

Layer 3 of the Clara prompt testing design (`usan-retirement-backend`,
`docs/superpowers/specs/2026-08-05-clara-prompt-testing-design.md`).

## Why it lives here, not with the other layers

The spec put this in the prompt repo "to keep one home". That was written before
anyone checked what the prompt repo contains: it has no `pyproject.toml`, no
lockfile, and not a single `.py` file — zero dependencies, deliberately. Putting
the harness there means adding a Python toolchain and a second pin of
`livekit-agents[google]` to a repo that has neither, to duplicate a stack that
already exists here, in the package whose audio behaviour is under test, beside
`test_live_greeting.py` and `test_live_speech.py`.

The spec invited exactly this revisit. The cost accepted in exchange is that the
Clara suite is now split: prompts and layers 0–2 in `usan-retirement-backend`,
audio here.

## Why this layer exists at all

Layers 1 and 2 grade a **transcript**. A transcript records what the platform
believes was said, and cannot record:

- the greeting spoken **twice** (PR #224) or a swap reply doubled (PR #216)
- the first word **clipped** off the front of a turn
- **dead air** while a tool call runs without its filler line
- a placeholder **read aloud** — ASR of a model saying `{{first_name}}` returns
  the words "first name", never the braces `said_never: '\{\{'` looks for

There is a second gap this closes, larger than any single bug. Production's
check-in agent runs `gemini-live-2.5-flash-native-audio` — speech-to-speech. A
Live model cannot serve text generation, so the simulation engine substitutes a
stand-in (`ARHITEQ_SIMULATION_AGENT_MODEL`, defaulting to `analysis_model`).
**Every layer-1 and layer-2 number is about the stand-in, not about what
production runs.** Only a real audio call reaches the real model.

## What is built

`analysis.py` — the rules, pure and stdlib-only, with `test_analysis.py`
covering them. Stdlib-only is a requirement, not a preference: the worker's CI
job installs only the dev group (`uv sync --only-group dev`, no
`livekit-agents`), so anything importing the heavy stack cannot run there. Split
this way, the rules are covered by the existing job with no workflow change —
the same split the prompt repo's runner uses.

Two rules so far, chosen because they map to bugs that actually shipped:

- **`no_duplicate_utterance`** — two agent utterances ≥90% alike within 30s.
  Only the agent's speech is compared (a case may repeat a caller line on
  purpose), and utterances under four words are never compared at all: "Okay."
  and "Okay." are 100% alike and a caller hears nothing wrong, so without that
  guard the rule is a false-positive machine.
- **`max_silence`** — dead air from the caller stopping to the agent starting,
  over 4s. Silence after the *agent* stops is the caller thinking and is not
  measured. A caller turn the agent never answers is measured to the end of the
  call, because the agent freezing entirely must not read as a clean call.

Both thresholds are **guesses until calibrated against known-good recordings**,
exactly as the spec warns. Expect them to be the flakiest thing in the suite.

## What is not built

**No real audio call has been placed yet.** The rules above have been verified
against constructed segment lists and mutation-tested — every one of the five
load-bearing decisions breaks a test when inverted — but nothing has yet
produced a `Segment` from an actual recording.

What remains, in order:

1. **`caller.py`** — a `livekit-agents` worker that joins the room as the
   caller. Using the framework rather than hand-rolling is what makes this
   tractable: VAD, end-of-turn detection and track subscription come free, and
   the "LLM" is simply the script.
2. **Call setup** — `POST /v2/create-web-call` with pinned variables returns a
   `call_id` and a LiveKit `access_token` (`backend/src/arhiteq_api/api/calls.py`).
3. **Capture** — record the agent's audio track to WAV alongside a timestamped
   event log; the log is what becomes `Segment.start` / `.end`.
4. **ASR** — transcribe the WAV for `Segment.text`. `GET /v2/get-call/{id}`
   gives the platform transcript to compare against.
5. **Artifacts** — on failure, keep the WAV and the event log. Listening to the
   two seconds that broke is the entire advantage over placing another call and
   hoping it reproduces.

Then the remaining rules — `heard_placeholder`, `no_clipped_start`,
`heard_matches_said` — and 8–12 cases. Anything gradeable from text belongs in
layer 1, where it costs a hundredth as much.

## Running what exists

```bash
cd worker && uv run --only-group dev pytest tests/audio/ -q
```
