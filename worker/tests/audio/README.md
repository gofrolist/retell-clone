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
| `analysis.py` | the rules that apply to every call | yes |
| `expectations.py` | the ones a single case states for itself | yes |
| `toolsink.py` | keeps a case's tool calls off the real endpoints | yes (bar the socket) |
| `pcm.py` | recording timeline, WAV, energy VAD | yes |
| `voice.py` | Cartesia synthesis (cached) and transcription | yes |
| `client.py` | `create-web-call` / `get-call`, and the tool rewrite | yes |
| `caseload.py` | the case format and its refusals | yes |
| `verdict.py` | what a run's exit code means | yes |
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
- **`no_restarted_turn`** — the agent began a sentence, abandoned it, and
  started again. The only rule that reads the **platform's** transcript rather
  than the recording, because that is where the evidence is legible: the audio
  has a stutter with no clean seam, the transcript has a fragment followed by
  the same opening finished. Requires the two turns to be adjacent (a caller
  turn between them makes a repeated opening ordinary) and the first to be
  unfinished — no terminal punctuation — which is what keeps a legitimate
  run-through out, since a model that finished its sentence did not abandon it.
- **`max_silence`** — dead air from the caller stopping to the agent starting,
  over 6s (baselined; see below). Silence after the *agent* stops is the caller
  thinking and is not measured. A caller turn the agent never answers is
  measured to the end of the call, because the agent freezing entirely must not
  read as a clean call.

## What a case says for itself

The two rules above apply to every call. A case adds what only it can know, in
`expect`, using the shape layer 1's `assert` already uses so the same case reads
the same way in both places:

```json
"expect": [
  { "heard": "(988|nine eight eight)" },
  { "never_heard": "first name" },
  { "tool_called": "log_medication_taken", "with": { "medication_name": "lipitor" } },
  { "tool_not_called": "purchase_offer" }
]
```

`heard` / `never_heard` are checked against **the ASR of the recording** — what
a listener's ear received. `tool_called` / `tool_not_called` are checked against
the platform's `transcript_with_tool_calls`, because a tool call makes no sound.
That split is the whole point of the layer: the other layers grade both against
one transcript and cannot tell them apart.

Patterns are regexes matched over normalised text (lower-cased, punctuation
blanked), so three things are refused **at load time**, each because the symptom
is a case that runs, costs a call, and reports clean:

- a pattern looking for `\{\{` — layer 1's placeholder check translated
  literally. ASR of a model reading `{{first_name}}` aloud returns the words
  "first name", never the braces. Match the words.
- a pattern carrying an apostrophe or other punctuation that normalisation
  removes: `it's clara` matches nothing, including a call that said exactly that.
- a pattern that will not compile.

Numbers are the sharp edge and nothing can refuse them for you: a transcriber
may write `988` or `nine eight eight` for the same audio, and which one it picks
is not a fact about the prompt. Write patterns that accept both.

## Nothing a case does leaves this machine

Clara's tools are HTTP tools pointed at live Supabase functions. A real call
runs real tools, so an audio case that answers "yes, I took my Lipitor" writes a
real dose to a real member's record, and one that reaches `send_family_sms`
texts a real phone. Layers 1 and 2 mock tools for determinism; this layer needs
it for safety first.

So every custom tool on the agents under test is repointed at a local sink,
which answers from the case's `tools` map — the same mocks, the same reasons —
and records what it was asked. `run_case` refuses to place a call if any tool
still points at a real endpoint, and it reads that from the **agent's config**,
not from the case: a case that mocks nothing is not a case that calls nothing.

The check follows `agent_swap` to every agent the call could reach, and so does
the rewrite. Clara is five agents: a call that starts at the check-in and asks
what a medication costs finishes inside the pharmacy specialist, running the
pharmacy's `send_coupon_sms`. Checking only where a call starts would clear a
run that texts a real coupon to a real phone — and would look like it had done
its job.

```bash
# once per seeded workspace: repoint every tool at the sink (loopback APIs only)
cd worker && PYTHONPATH=tests:src .venv/bin/python -m audio.toolsink --rewrite \
    --agents tests/audio/agents.json --api-base http://127.0.0.1:8080
```

**The rewrite publishes, and that is the whole trick.** `PATCH
/update-retell-llm` opens a *draft*; a call runs `latest_published`
(`docs/AGENT_VERSIONING.md`). So repointing every tool and reading the live rows
back reports a completely sunk workspace while every call still dials the real
endpoints. That is not a hypothetical — it is what the first Clara audio call
did, and it POSTed a medication log to production Supabase (the request failed
on the caller secret, which was luck, not design). Both the rewrite and the
refusal now read the published snapshot, and the rewrite publishes when what a
call would dial is not sunk, including when an earlier run patched and stopped.

The rewrite is refused against anything but a loopback API, and that refusal is
not hypothetical: run it against production and every real Clara tool call
starts arriving at a laptop that is not listening — quietly, because an agent
talks its way past a tool error. The local worker needs
`ARHITEQ_ALLOW_PRIVATE_WEBHOOKS=1`, or its SSRF guard rejects the sink's address
and every case that grades a tool call fails for a reason that is not the
prompt.

A tool with no mock still gets `{"ok": true}` and is named in the report. It is
not an error — a case cannot anticipate every tool — but the payload was
invented here, and an invented payload steers every turn after it.

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
  is subscribed. That wait is structural (the track does not exist yet) and is
  measured apart from the rest, so the "audio went missing mid-call" warning
  stays quiet on a healthy run. Counted together it fired on every clean call,
  and a warning that is always on is one nobody reads.

These are three data points from one configuration. Treat every threshold in
`pcm.py` and `analysis.py` as provisional until a week of real calls has moved
them.

### The first Clara call

`A01-greeting-heard-once` against the five seeded agents on
`gemini-3.1-flash-lite`: 41.7s, greeting heard once, `log_mood`,
`log_medication_taken` and `log_outcome` all answered by the sink, no findings.

It failed twice first, and both failures were worth more than the pass:

1. **The published-snapshot hole above.** The run reported a clean guard and
   then dialled production.
2. **A scripted caller that ignored a question.** The mood phase asks "how did
   you sleep?"; line 2 answered about medication instead, the phase never
   closed, `log_mood` never fired, and the case failed on its own rigidity. A
   script has to answer what the prompt asks before moving on — a real listener
   would, and layer 1 has the same rule for the same reason.

One rough edge left: the caller logs `error putting to queue: Event loop is
closed` as the room tears down. It happens after the last line is played, does
not reach the recording, and has not changed a verdict.

### What the first pass over all six found

Every case ran on `gemini-3.1-flash-lite`. **Two of the three failures were the
harness, not the prompt**, which is the ratio to expect from a new layer and the
reason to distrust its first red run:

- **The caller ignored a hangup.** It waited out its 25s reply timeout and
  played the rest of the script into an empty room, so three cases reported
  25.0s of "dead air" on calls the agent had ended correctly. Fixed: the caller
  now stops on the agent's disconnect (`agent_ended_call`, a gradeable ending,
  not a broken run) and names the lines that went unspoken.
- **A03's filler finding was the caller talking over the lookup.** At the
  default 1.2s settle, a short backchannel read as a finished turn. At 5s the
  agent says "One moment, let me check that" exactly as the prompt requires and
  the case is clean.
- **A06's failure is real and reproduces.** In two of three runs the agent
  called `log_outcome` and `end_call` without saying any closing at all — it
  hangs up on the caller. In the third it read the scripted line in full. This
  is the phase-skipping class the prompt work has been fighting, arriving at the
  closing gate.

One smaller thing worth a note: the agent asked the same mood question twice in
several runs, 10–11s apart, under `no_duplicate_utterance`'s 0.9 word-similarity
bar.

### `max_silence`, baselined

The 4s limit came from 1.46s/1.73s measurements against a two-line smoke agent.
Clara is a 63k-character prompt and is simply slower. Across the 13 calls above
— 35 answered gaps — the distribution splits by whether a tool ran inside the
gap:

| gap | n | median | p90 | max |
|---|---|---|---|---|
| no tool call | 16 | 1.95s | 3.29s | 5.11s |
| a tool ran | 19 | 3.41s | 4.71s | 4.99s |

Nothing exceeded 5.11s, and 4s flagged **6 of the 35 ordinary turns** — the rate
at which a rule stops being read. The limit is now **6.0s**: clear of every gap
measured by ~0.9s, and still short of the point where a listener concludes the
line is dead. Re-grading all 13 stored recordings at 6s leaves zero silence
findings and changes nothing else.

One limit rather than two, even though the tool split is real. The honest
reading of a slow tool gap is not "allow more silence" — it is that the prompt
requires a filler line before a lookup, and that is a per-case `heard`
expectation (A03), not a threshold.

**This number belongs to one configuration.** Production runs a
speech-to-speech Live model against the same prompt; it should be faster, 6s may
be far too generous there, and the measurement has to be redone before any of it
is believed. The re-grade above also only covers *answered* turnaround — a turn
the agent never answers is measured to the end of the call, and no run since the
hangup fix has produced one.

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

# a Clara case, against a workspace seeded from dist/ and rewritten once
cd worker && PYTHONPATH=tests:src .venv/bin/python -m audio.run_case \
    --case tests/audio/cases/A01-greeting-heard-once.case.json \
    --agents tests/audio/agents.json --api-base http://127.0.0.1:8080
```

`ARHITEQ_WORKSPACE_API_KEY` and `CARTESIA_API_KEY` come from the environment.
`--agents` is a JSON map of the name a case uses (`clara-checkin`) to the agent
id the seed created; the prompt repo writes one to `prompts/clara/dist/
agent-ids.json`. The worker wants `ARHITEQ_ALLOW_PRIVATE_WEBHOOKS=1` so the tool
sink is reachable.

Exit codes: `0` clean, `1` the rules found something, `2` the run was broken —
nobody joined, no audio, a provider refused, the call hit its own time limit
before the script finished. Only `1` says anything about the prompt, and
treating a `2` as a finding is how a suite loses its meaning: a rotated
credential would be reported as the prompt getting worse. The rule lives in
`verdict.py` rather than as `return` statements in the driver, because the
driver imports `livekit` and cannot be tested, and this is the one decision a
scheduler actually reads.

Artifacts land in `tests/audio/artifacts/<case>-<call_id>/`: the WAV, the
segments with their times, the findings, and the platform's own record of the
call. Both the WAV and the platform transcript are kept because the
*disagreement* between them — heard twice, logged once — is a finding neither
one can show alone.

The WAV is written the moment the call ends, before transcription or fetching
the platform record. Everything after the call is network and any of it can
fail; written last, a 429 from the speech provider throws away a recording that
cost a minute of wall clock and real spend — exactly when the recording is
worth most.

## The cases

`smoke-greets-once` proves the harness against a throwaway agent and asserts
nothing about any prompt. The rest are Clara, and each one is here because no
cheaper layer can run it — anything gradeable from text belongs in layer 1,
where it costs a hundredth as much.

| case | the failure only audio can see |
|---|---|
| `A01-greeting-heard-once` | the begin message spoken twice (PR #224). The platform logs one greeting, because it only ever generated one. |
| `A02-pharmacy-handoff-not-doubled` | `agent_swap` rebuilds the Live socket (~850ms) and the reply came back doubled and clipped (PR #216). Layers 1–2 re-point the prompt in-process; there is no socket to break. |
| `A03-lookup-filler-not-dead-air` | the prompt requires a filler line *out loud* before `web_lookup`. A transcript shows the line and the tool call, never the silence between them. |
| `A04-medication-name-survives-the-round-trip` | a drug name synthesized, heard by ASR, written into a tool argument and said back. Layer 1 hands the model the string and can only prove it copied one. |
| `A05-crisis-number-said-aloud` | 988, intelligible at the pace of a spoken sentence. The highest-stakes thing Clara says is the thing speech models are worst at. |
| `A06-goodbye-heard-in-full` | `end_call` firing while the last sentence is still playing. The transcript has the whole sentence — it was generated whole. |

The sixteen dispatcher variables live in `cases/_checkin.vars.json` and are
pulled in with `variables_from`, so what stays in a case file is the override —
the sentence the case is actually making. It holds the same values as layer 1's
`_fixtures.js` for the same reasons, which is what makes a run here comparable
with a run there.

## The Live model, and what it cost the rules

The first run against `gemini-live-2.5-flash-native-audio` — a fresh workspace
seeded from `dist/`, Vertex serving Live at `us-east1` and refusing it at
`global` — is the reason this layer exists, and it arrived as a rebuke to the
rules rather than to the prompt. Five of six cases reported **no findings** on
calls that stutter audibly.

What the model actually does is abandon turns and start them again. The
platform's transcript and the microphone tell the two halves:

| platform believes it said | microphone heard |
|---|---|
| "That's wonderful to" · "That's wonderful to hear. Did you sleep alright last night?" | "That's wonderful to hear. That's wonderful to hear. Did you sleep all right last night?" |
| "Good for you. Is there anything" · "Good for you. Before I let you go, is there anything else…" | "Good for you. Is there any… Good for you. Before I let you go…" |

Three gaps, all of them under-reporting, now closed:

1. `repeats_itself` only knew how to see an utterance that is two copies of
   itself. The Live stutter is a repeated **prefix** with the sentence carrying
   on after it, and the midpoint seam search cannot reach a seam a quarter of
   the way in. It now also scans for an exact repeated prefix — exact, not
   approximate, because a scan over every prefix length would start finding
   "repeats" in ordinary parallel phrasing.
2. `tool_called` had no `times`. One Live call logged the mood, the dose and the
   outcome **twice each** — a turn abandoned after its tools ran, then
   regenerated — and every assertion that only asks whether a tool ran called
   that clean. A01 now pins `times: 1`.
3. Nothing compared the platform's transcript with the recording, though both
   were already written to the artifacts. `no_restarted_turn` is that
   comparison, narrowed to the shape that has no innocent explanation.

Re-grading the six stored Live recordings with the rules as they now stand:
A01 goes 0 → 3 findings, A06 1 → 4, and A03/A04/A05 stay at 0. Re-grading the
13 text-model recordings introduces **zero** new findings, which is the check
that matters — a rule that fires on the calls that were fine is worse than no
rule.

Two prompt-level findings from the same run, neither yet fixed: it called the
listener **"Mark"** with `first_name=Margaret`, and it fabricated continuity
("yesterday evening you mentioned wanting to read the new mystery book") with
`prior_conversation` empty. The turn-restarting itself is a platform question
rather than a prompt one — the worker log shows adaptive interruption failing
and falling back to VAD, and a speech-to-speech model that barges in on itself
would produce exactly this.

## What is not built

1. **The remaining rules** — `no_clipped_start`, and `heard_matches_said` (the
   recording against `transcript_object`, which is already fetched and stored
   beside it). A placeholder read aloud is covered by `never_heard` per case
   rather than as a global rule: "first name" is a phrase an agent may
   legitimately say, and a rule that cannot tell the difference is one people
   mute.
2. **Barge-in.** The caller waits for the agent to settle before every line, so
   nothing here interrupts. Talking over the agent is a first-class audio
   failure mode (the clipped first word after an interruption) and reaching it
   needs `caller.py` to be able to start a line while the agent is still
   talking.
3. **A suite runner.** `caseload.discover` reads a whole directory and nothing
   calls it: `run_case` still runs one case per invocation.
4. **Running against the real model.** Every measurement above is from a
   text-pipeline agent. The gap this layer exists to close — production runs
   speech-to-speech and no other layer ever touches it — is still open until a
   case runs against a Live agent.
5. **A schedule.** Nothing runs this automatically. It costs a real call per
   case, so it belongs on a nightly beside layer 2, not in the commit path.
