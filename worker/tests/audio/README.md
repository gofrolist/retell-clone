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

## How a case runs

```
    caseload.py ──► voice.synthesize ──► caller.py ──► LiveKit room ──► worker
                     (cached PCM)            │
                                             ▼
                            pcm.Recording (one continuous timeline)
                                             │
                     pcm.speech_spans ───────┴──► voice.transcribe
                                             │
                                             ▼
                            analysis.analyse ──► findings + artifacts
```

Everything expensive happens before the room is joined: the caller's lines are
synthesized and cached first, so no provider latency lands inside a turn where
the timing rules would blame it on the agent.

Two clocks, deliberately different. **Turn-taking** uses a live fixed loudness
floor, frame by frame, because the decision to speak has to be made before the
recording exists. **The report** re-segments offline over the finished buffer
with a threshold adapted to that recording, so the same audio always segments
the same way — a harness whose boundaries move between runs cannot tell a
prompt regression from its own noise.

## What is built

| file | what it does | in CI |
|---|---|---|
| `analysis.py` | the rules | yes |
| `pcm.py` | recording timeline, WAV, energy VAD | yes |
| `voice.py` | Cartesia synthesis (cached) and transcription | yes |
| `client.py` | `create-web-call` / `get-call` | yes |
| `caseload.py` | the case format and its refusals | yes |
| `caller.py` | joins the room, says the lines, records | **no** |
| `run_case.py` | the driver and the artifacts | **no** |

CI coverage is split that way on purpose: the worker's CI job installs the dev
group only (`uv sync --only-group dev`, no `livekit-agents`), so anything
importing the heavy stack cannot run there. Everything that makes a *judgement*
is stdlib-or-httpx and covered by the existing job with no workflow change.
What is left uncovered is plumbing — connect, publish, subscribe, wait.

Two rules so far, chosen because they map to bugs that actually shipped:

- **`no_duplicate_utterance`** — the agent said the same substantial thing
  twice, either as two segments ≥90% alike within 30s, or twice inside a single
  utterance. Both are needed: whether a double arrives as one segment or two
  depends on how long a pause the model left, which has nothing to do with
  whether the caller heard it. Only the agent's speech is compared (a case may
  repeat a caller line on purpose), and utterances under four words are never
  compared: "Okay." and "Okay." are 100% alike and a caller hears nothing wrong.
- **`max_silence`** — dead air from the caller stopping to the agent starting,
  over 4s. Silence after the *agent* stops is the caller thinking and is not
  measured. A caller turn the agent never answers is measured to the end of the
  call, because the agent freezing entirely must not read as a clean call.

## What the first real calls measured

Two calls against a local single-prompt agent (`gemini-3.1-flash-lite`, Cartesia
in and out, everything on localhost):

- **Agent turnaround** — 1.46s and 1.73s from the caller stopping to the agent
  starting. The shipped `max_silence` limit of 4s therefore has about 2.3s of
  headroom **on this configuration**. Production runs a Live model on GKE; the
  number will move and the limit will need re-checking there.
- **Pause between sentences of one agent turn** — 0.78s, 0.80s, 0.84s on one
  call, under 0.6s on the next. This is why `MIN_SILENCE_S` stays at 0.6 and
  segments are sentences rather than turns: set high enough to keep a turn
  whole, a doubled greeting merges into one segment and the rule that exists to
  catch it finds nothing.
- **Recording padding** — 1.4–1.8s per call, all of it before the agent's track
  is subscribed. Anything much larger means frames went missing mid-call and is
  reported as a warning, because a recording that is mostly synthesized silence
  is a network problem wearing a prompt finding's clothes.

These are three data points from one configuration. Treat every threshold in
`pcm.py` and `analysis.py` as provisional until a week of real calls has moved
them.

### The negative control

A green suite proves nothing on its own, so the rules were run against an agent
built to fail: a prompt instructed to say every reply twice. It doubled both its
greeting and its reply, and the harness caught both — the greeting as two
segments 0.7s apart, the reply as one segment repeating itself. The clean call
re-checked with the same rules stayed clean.

That control also found a real hole. The doubled *reply* was invisible to the
original rule, which only ever compared one segment with another; it took a real
call to show that a double does not always arrive as two segments.

## Running it

```bash
# the pure half, in the existing CI job
cd worker && uv run --only-group dev pytest tests/audio/ -q

# one real call (needs the local stack: docker compose up -d, make api, make worker)
cd worker && PYTHONPATH=tests:src .venv/bin/python -m audio.run_case \
    --case tests/audio/cases/smoke-greets-once.case.json \
    --agent-id agent_... --api-base http://127.0.0.1:8080
```

`ARHITEQ_WORKSPACE_API_KEY` and `CARTESIA_API_KEY` come from the environment.
Exit codes: `0` clean, `1` the rules found something, `2` the run was broken —
nobody joined, no audio, a provider refused. Only `1` says anything about the
prompt, and treating a `2` as a finding is how a suite loses its meaning.

Artifacts land in `tests/audio/artifacts/<case>-<call_id>/`: the WAV, the
segments with their times, the findings, and the platform's own record of the
call. Both the WAV and the platform transcript are kept because the
*disagreement* between them — heard twice, logged once — is a finding neither
one can show alone.

## What is not built

1. **The remaining rules** — `heard_placeholder` (a model reading
   `{{first_name}}` aloud transcribes as the words "first name", which no text
   layer can look for), `no_clipped_start`, and `heard_matches_said` (the
   recording against `transcript_object`, which is already fetched and stored
   beside it).
2. **Clara cases.** The only case in the repo is `smoke-greets-once`, which
   runs against a throwaway agent and proves the harness, not the prompt. The
   8–12 real cases need a workspace seeded from `dist/` — and anything gradeable
   from text belongs in layer 1, where it costs a hundredth as much.
3. **Running against the real model.** Every measurement above is from a
   text-pipeline agent. The gap this layer exists to close — production runs
   speech-to-speech and no other layer ever touches it — is still open until a
   case runs against a Live agent.
4. **A schedule.** Nothing runs this automatically. It costs a real call per
   case, so it belongs on a nightly beside layer 2, not in the commit path.
