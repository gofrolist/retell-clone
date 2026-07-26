"""LLM simulation testing — run a scripted user against an agent's own prompt.

One run plays three LLM roles over a single transcript:

* the **user simulator** acts out the persona/scenario in the case's
  `user_prompt`;
* the **agent under test** answers using the agent's real `general_prompt`, its
  real tool catalog and its own model — that is the thing being graded;
* the **judge** reads the finished transcript and grades each metric.

Tools are never really executed. A matching `tool_mock` supplies the result;
absent one, the harness synthesizes a plausible success payload. A simulation
therefore can never book an appointment, send an SMS or dial a transfer, which
is what makes it safe to run against a production agent config.

Everything here is best-effort: a model or credential failure marks the run
`error` rather than raising, so one bad case can't sink a batch.
"""

import asyncio
import contextlib
import json
import logging
import secrets
from typing import Any

from ..config import Settings, get_settings
from ..models import (
    Agent,
    RetellLLM,
    TestCaseBatchJob,
    TestCaseDefinition,
    TestCaseJob,
    now_ms,
)
from .gemini import build_genai_client, genai_credentials_available, is_live_model
from .template_variables import resolve_template

log = logging.getLogger(__name__)

# A simulated call is capped so a looping agent (or a user simulator that never
# hangs up) can't burn tokens forever. Turns are user+agent exchanges.
MAX_TURNS = 16
# Consecutive tool calls the agent may make inside one turn before the harness
# forces it to speak — guards against a tool-call loop.
MAX_TOOL_CALLS_PER_TURN = 4
# How many runs of a batch execute at once.
BATCH_CONCURRENCY = 4

# Tool types that hang up when called: reaching one ends the simulated call.
_TERMINAL_TOOL_TYPES = ("end_call", "transfer_call", "agent_swap")

_AGENT_PROMPT = """\
{general_prompt}

--- SIMULATION HARNESS (not part of your persona) ---
You are the agent described above, on a live phone call. Produce the agent's
NEXT action only — never write the user's lines, and never mention this harness.
{tools}
Reply with STRICT JSON (no markdown), exactly one of:
{{"action": "speak", "content": "<what the agent says next>"}}
{{"action": "tool_call", "tool_name": "<name>", "arguments": {{<arguments object>}}}}

Conversation so far:
{history}"""

_USER_PROMPT = """\
You are role-playing a person on a phone call with an AI agent. Stay in
character at all times and never reveal that this is a test.

Your character, situation and goal:
{user_prompt}

Speak the way people actually speak on the phone: short turns, one idea at a
time, and answer what you were asked. Hang up once your goal is met or is
clearly unreachable — do not keep the call going out of politeness.

Reply with STRICT JSON (no markdown), exactly one of:
{{"action": "speak", "content": "<what you say next>"}}
{{"action": "hangup", "reason": "<why the call is over>"}}

Conversation so far:
{history}"""

_JUDGE_PROMPT = """\
You are grading a simulated phone call between an AI agent and a user. Lines
marked `Tool call` / `Tool result` are the agent's function calls and what came
back from them.

The scenario the user was playing:
{user_prompt}

Transcript:
{transcript}

Grade each criterion below independently, judging ONLY the agent's behaviour.
A criterion passes only when the transcript clearly shows it was met; if the
transcript is silent or ambiguous about it, it fails.

Criteria:
{metrics}

Return STRICT JSON (no markdown):
{{"results": [{{"metric": "<the criterion, copied verbatim>", "passed": true|false,
"explanation": "<one sentence citing what in the transcript decided it>"}}]}}"""

_TOOL_RESULT_PROMPT = """\
A simulated phone agent called the tool `{tool_name}`{description} with these
arguments:
{arguments}

Invent the JSON payload a working implementation would most plausibly return
for a successful call. Keep it small and realistic; invent concrete values
rather than placeholders. Return STRICT JSON (no markdown) — the payload only."""

_GENERATE_PROMPT = """\
You are a QA engineer writing simulation tests for the AI phone agent below.
The tests run as a role-played phone call: a simulated user follows the
scenario you write, and a judge then grades the transcript against the criteria
you write.

Agent prompt:
\"\"\"
{general_prompt}
\"\"\"

Agent's first line: {begin_message}
Who speaks first: {start_speaker}

Tools the agent can call:
{tools}

Write {count} DISTINCT test cases that together cover this agent's real risk
surface: the happy path, each tool the agent has (including the conditions that
should trigger it), and the awkward cases this specific prompt invites —
a user who refuses to answer, gives contradictory or out-of-scope information,
asks something the prompt says to decline, or tries to make the agent break its
instructions. Do not invent capabilities the prompt and tools do not describe.

For each case produce:
* "name": 3-6 words, specific to the scenario (not "Test 1").
* "user_prompt": second-person instructions to the simulated user — who they
  are, what they want, what they know, and how they behave (including anything
  they should push back on). 2-5 sentences. Never mention testing or the judge.
* "metrics": 2-4 criteria, each a single verifiable statement about what the
  agent must do in THIS scenario (e.g. "The agent calls check_availability_cal
  before offering a time"). Prefer observable behaviour over tone.
* "tool_mocks": for each tool this scenario should reach, an entry
  {{"tool_name": "<name>", "input_match_rule": {{"type": "any"}},
  "output": "<JSON string the tool would return>"}} — pick outputs that force
  the branch the scenario is about (e.g. no availability, payment declined).
  Use [] when the scenario reaches no tools.

Return STRICT JSON (no markdown): {{"test_cases": [ ... ]}}"""


def _json_object(raw: str | None) -> dict[str, Any]:
    """Parse a model reply that should be a JSON object, tolerating fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    # Some models still prepend a sentence; fall back to the outermost braces.
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"no JSON object in model reply: {(raw or '')[:200]!r}")
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError("model reply was not a JSON object")
    return data


def tool_catalog(llm: RetellLLM | None) -> list[dict[str, Any]]:
    """The agent's tools as {name, type, description, parameters} entries.

    Mirrors how the worker names tools, so `tool_mocks` written against a
    simulation match the names the live agent actually calls.
    """
    catalog: list[dict[str, Any]] = []
    for entry in (llm.general_tools if llm else None) or []:
        if not isinstance(entry, dict):
            continue
        tool_type = str(entry.get("type") or "custom")
        name = str(entry.get("name") or tool_type)
        catalog.append(
            {
                "name": name,
                "type": tool_type,
                "description": str(entry.get("description") or ""),
                "parameters": entry.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return catalog


def _tool_block(catalog: list[dict[str, Any]]) -> str:
    if not catalog:
        return "\nYou have no tools available; you can only speak.\n"
    lines = ["\nTools you may call (use the exact name; arguments must match the schema):"]
    for tool in catalog:
        lines.append(
            f"- {tool['name']}: {tool['description'] or '(no description)'}\n"
            f"  arguments schema: {json.dumps(tool['parameters'])}"
        )
    lines.append("")
    return "\n".join(lines)


def _history(transcript: list[dict[str, Any]]) -> str:
    """Render the transcript for a prompt. Tool traffic is included so the agent
    can act on results and the judge can grade tool usage."""
    lines: list[str] = []
    for item in transcript:
        role = item.get("role")
        if role == "agent":
            lines.append(f"Agent: {item.get('content', '')}")
        elif role == "user":
            lines.append(f"User: {item.get('content', '')}")
        elif role == "tool_call_invocation":
            lines.append(f"Tool call: {item.get('name')}({item.get('arguments') or '{}'})")
        elif role == "tool_call_result":
            lines.append(f"Tool result: {item.get('name')} -> {item.get('content', '')}")
    return "\n".join(lines) if lines else "(the call has just connected)"


def match_tool_mock(
    mocks: list[Any] | None, tool_name: str, arguments: dict[str, Any]
) -> dict[str, Any] | None:
    """First mock whose `tool_name` and `input_match_rule` accept this call.

    `{"type": "any"}` matches everything; `{"type": "partial_match", "args": …}`
    matches when every listed argument equals the one in the call (extra
    arguments in the call are ignored). An unknown rule type never matches, so a
    typo degrades to "no mock" instead of silently mocking every call.
    """
    for mock in mocks or []:
        if not isinstance(mock, dict) or mock.get("tool_name") != tool_name:
            continue
        rule = mock.get("input_match_rule") or {"type": "any"}
        if not isinstance(rule, dict):
            continue
        if rule.get("type") == "any":
            return mock
        if rule.get("type") == "partial_match":
            expected = rule.get("args")
            if isinstance(expected, dict) and all(
                arguments.get(k) == v for k, v in expected.items()
            ):
                return mock
    return None


class _Simulator:
    """One simulated call. Holds the models and the growing transcript."""

    def __init__(
        self,
        *,
        settings: Settings,
        general_prompt: str,
        catalog: list[dict[str, Any]],
        definition: dict[str, Any],
        agent_model: str,
        begin_message: str | None,
        start_speaker: str,
    ) -> None:
        self._client = build_genai_client(settings)
        self._settings = settings
        self._general_prompt = general_prompt
        self._catalog = catalog
        self._definition = definition
        self._agent_model = agent_model
        self._begin_message = begin_message
        self._start_speaker = start_speaker
        self.transcript: list[dict[str, Any]] = []

    async def _json_call(self, model: str, prompt: str, temperature: float) -> dict[str, Any]:
        resp = await self._client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": temperature},
        )
        return _json_object(resp.text)

    def _tool_by_name(self, name: str) -> dict[str, Any] | None:
        return next((t for t in self._catalog if t["name"] == name), None)

    async def _tool_result(
        self, tool: dict[str, Any] | None, name: str, args: dict[str, Any]
    ) -> str:
        """Mocked output for a tool call: an explicit mock, else an invented one."""
        mock = match_tool_mock(self._definition.get("tool_mocks"), name, args)
        if mock is not None:
            return str(mock.get("output") or "")
        if tool is None:
            # The agent invented a tool it does not have. Say so rather than
            # fabricating success — the judge should see the mistake.
            return json.dumps({"error": f"unknown tool {name}"})
        if tool["type"] in _TERMINAL_TOOL_TYPES:
            # Hang-up/transfer tools return nothing the agent could act on (the
            # call is over), so there is no payload worth inventing.
            return json.dumps({"success": True})
        description = f" ({tool['description']})" if tool.get("description") else ""
        try:
            data = await self._json_call(
                self._settings.analysis_model,
                _TOOL_RESULT_PROMPT.format(
                    tool_name=name,
                    description=description,
                    arguments=json.dumps(args, ensure_ascii=False),
                ),
                0.4,
            )
            return json.dumps(data, ensure_ascii=False)
        except Exception:
            log.exception("simulation: could not synthesize a result for tool %s", name)
            return json.dumps({"success": True})

    async def _agent_turn(self) -> bool:
        """Let the agent act until it speaks. Returns False if the call ended."""
        for _ in range(MAX_TOOL_CALLS_PER_TURN):
            action = await self._json_call(
                self._agent_model,
                _AGENT_PROMPT.format(
                    general_prompt=self._general_prompt,
                    tools=_tool_block(self._catalog),
                    history=_history(self.transcript),
                ),
                0.3,
            )
            if action.get("action") != "tool_call":
                content = str(action.get("content") or "").strip()
                if content:
                    self.transcript.append({"role": "agent", "content": content})
                return True

            name = str(action.get("tool_name") or "")
            args = action.get("arguments")
            args = args if isinstance(args, dict) else {}
            call_id = f"tool_{secrets.token_hex(8)}"
            self.transcript.append(
                {
                    "role": "tool_call_invocation",
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                    "tool_call_id": call_id,
                }
            )
            tool = self._tool_by_name(name)
            output = await self._tool_result(tool, name, args)
            self.transcript.append(
                {
                    "role": "tool_call_result",
                    "name": name,
                    "content": output,
                    "tool_call_id": call_id,
                }
            )
            # end_call / transfer_call / agent_swap take the call away from this
            # agent; nothing after them belongs in the transcript.
            if tool is not None and tool["type"] in _TERMINAL_TOOL_TYPES:
                return False
        # Stuck in tool calls: force one spoken turn so the transcript shows it.
        self.transcript.append(
            {"role": "agent", "content": "(the agent kept calling tools without speaking)"}
        )
        return True

    async def _user_turn(self) -> bool:
        """Let the simulated user speak. Returns False if they hung up."""
        action = await self._json_call(
            self._settings.analysis_model,
            _USER_PROMPT.format(
                user_prompt=self._definition.get("user_prompt") or "",
                history=_history(self.transcript),
            ),
            0.7,
        )
        if action.get("action") == "hangup":
            return False
        content = str(action.get("content") or "").strip()
        if not content:
            return False
        self.transcript.append({"role": "user", "content": content})
        return True

    async def run(self) -> list[dict[str, Any]]:
        # Retell semantics: `start_speaker == "user"` means the agent waits for
        # the user; otherwise it opens with begin_message, or improvises one.
        if self._start_speaker != "user":
            if self._begin_message:
                self.transcript.append({"role": "agent", "content": self._begin_message})
            elif not await self._agent_turn():
                return self.transcript
        for _ in range(MAX_TURNS):
            if not await self._user_turn():
                break
            if not await self._agent_turn():
                break
        return self.transcript

    async def judge(self) -> tuple[str, list[dict[str, Any]]]:
        """Grade the transcript. Returns (status, per-metric results)."""
        metrics = [str(m) for m in (self._definition.get("metrics") or []) if str(m).strip()]
        if not metrics:
            return "pass", []
        data = await self._json_call(
            self._settings.analysis_model,
            _JUDGE_PROMPT.format(
                user_prompt=self._definition.get("user_prompt") or "",
                transcript=_history(self.transcript),
                metrics="\n".join(f"{i + 1}. {m}" for i, m in enumerate(metrics)),
            ),
            0.0,
        )
        raw = data.get("results")
        by_metric = {str(r.get("metric")): r for r in (raw or []) if isinstance(r, dict)}
        results: list[dict[str, Any]] = []
        for metric in metrics:
            entry = by_metric.get(metric)
            if entry is None:
                # The judge dropped or reworded a criterion. An ungraded metric
                # is a failure, not a silent pass.
                results.append(
                    {
                        "metric": metric,
                        "passed": False,
                        "explanation": "The judge returned no verdict for this criterion.",
                    }
                )
                continue
            results.append(
                {
                    "metric": metric,
                    "passed": bool(entry.get("passed")),
                    "explanation": str(entry.get("explanation") or ""),
                }
            )
        status = "pass" if all(r["passed"] for r in results) else "fail"
        return status, results


def _explain(status: str, results: list[dict[str, Any]]) -> str:
    if status == "pass":
        return f"All {len(results)} criteria passed." if results else "No criteria to grade."
    failed = [r for r in results if not r["passed"]]
    head = f"{len(failed)} of {len(results)} criteria failed."
    return "\n".join([head, *(f"- {r['metric']}: {r['explanation']}" for r in failed)])


def definition_snapshot(definition: TestCaseDefinition) -> dict[str, Any]:
    """The fields a run is executed from, frozen at run time."""
    return {
        "test_case_definition_id": definition.test_case_definition_id,
        "name": definition.name,
        "user_prompt": definition.user_prompt,
        "metrics": list(definition.metrics or []),
        "dynamic_variables": dict(definition.dynamic_variables or {}),
        "tool_mocks": list(definition.tool_mocks or []),
        "llm_model": definition.llm_model,
    }


async def _run_one(factory: Any, job_id: str) -> str:
    """Execute one test run to a terminal status, committing as it goes."""
    settings = get_settings()
    async with factory() as session:
        job = await session.get(TestCaseJob, job_id)
        if job is None:
            return "error"
        job.status = "in_progress"
        job.user_modified_timestamp = now_ms()
        await session.commit()

        snapshot = dict(job.test_case_definition_snapshot or {})
        batch = await session.get(TestCaseBatchJob, job.test_case_batch_job_id)
        llm = await session.get(RetellLLM, batch.llm_id) if batch and batch.llm_id else None
        agent = await session.get(Agent, batch.agent_id) if batch and batch.agent_id else None

    status, results, transcript = "error", [], []
    explanation = ""
    try:
        if not genai_credentials_available(settings):
            raise RuntimeError(
                "No Gemini credentials configured; simulation runs need GOOGLE_API_KEY "
                "or Vertex ADC."
            )
        if llm is None:
            raise RuntimeError("The response engine for this test no longer exists.")

        # Dynamic variables resolve exactly as they do on a live call, so a
        # prompt that depends on {{name}} is tested with a value, not the
        # literal placeholder.
        variables = {str(k): str(v) for k, v in (snapshot.get("dynamic_variables") or {}).items()}
        merged = {**(llm.default_dynamic_variables or {}), **variables}
        general_prompt = resolve_template(llm.general_prompt or "", merged)
        begin_message = resolve_template(llm.begin_message or "", merged) or None

        # The case may pin a model; Live (audio-only) models can't serve text
        # generation, so those fall back to the platform analysis model.
        agent_model = snapshot.get("llm_model") or llm.model or settings.analysis_model
        if is_live_model(agent_model):
            agent_model = settings.analysis_model

        simulator = _Simulator(
            settings=settings,
            general_prompt=general_prompt,
            catalog=tool_catalog(llm),
            definition=snapshot,
            agent_model=agent_model,
            begin_message=begin_message,
            start_speaker=str(llm.start_speaker or "agent"),
        )
        transcript = await simulator.run()
        status, results = await simulator.judge()
        explanation = _explain(status, results)
    except Exception as exc:
        log.exception("simulation run %s failed", job_id)
        status, explanation = "error", f"{type(exc).__name__}: {exc}"
        transcript = transcript or []

    async with factory() as session:
        job = await session.get(TestCaseJob, job_id)
        if job is None:
            return status
        job.status = status
        job.transcript_snapshot = {
            "type": "retell-llm",
            "agent_id": agent.agent_id if agent else None,
            "messages": transcript,
        }
        job.metric_results = results
        job.result_explanation = explanation
        job.user_modified_timestamp = now_ms()
        await session.commit()
    return status


async def run_batch(factory: Any, batch_job_id: str, job_ids: list[str]) -> None:
    """Run every job of a batch, then roll the counts up onto the batch row."""
    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)

    async def guarded(job_id: str) -> str:
        async with semaphore:
            try:
                return await _run_one(factory, job_id)
            except Exception:  # a crashed run must not cancel its siblings
                log.exception("simulation run %s crashed", job_id)
                return "error"

    statuses = await asyncio.gather(*(guarded(j) for j in job_ids))
    async with factory() as session:
        batch = await session.get(TestCaseBatchJob, batch_job_id)
        if batch is None:
            return
        batch.pass_count = sum(s == "pass" for s in statuses)
        batch.fail_count = sum(s == "fail" for s in statuses)
        batch.error_count = sum(s not in ("pass", "fail") for s in statuses)
        batch.status = "complete"
        batch.user_modified_timestamp = now_ms()
        await session.commit()


# Strong references to in-flight batches so they aren't garbage-collected.
_batch_tasks: set[asyncio.Task] = set()


def spawn_batch(factory: Any, batch_job_id: str, job_ids: list[str]) -> None:
    """Start a batch in the background (create-batch-test returns immediately)."""
    task = asyncio.create_task(run_batch(factory, batch_job_id, job_ids))
    _batch_tasks.add(task)
    task.add_done_callback(_batch_tasks.discard)


async def shutdown() -> None:
    """Cancel in-flight batches on app shutdown so the loop can close cleanly."""
    for task in list(_batch_tasks):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def generate_test_cases(llm: RetellLLM, count: int) -> list[dict[str, Any]]:
    """Write test cases from the agent's own prompt and tool catalog.

    This is the "agent tests itself" path: nothing but the saved prompt, the
    greeting and the tool schemas goes in, and drafted cases come back for the
    operator to keep or edit. Raises on failure — the caller surfaces it,
    because a silent empty list reads as "your agent needs no tests".
    """
    settings = get_settings()
    if not genai_credentials_available(settings):
        raise RuntimeError(
            "No Gemini credentials configured; test-case generation needs "
            "GOOGLE_API_KEY or Vertex ADC."
        )
    catalog = tool_catalog(llm)
    tools = (
        "\n".join(
            f"- {t['name']} ({t['type']}): {t['description'] or '(no description)'}\n"
            f"  arguments: {json.dumps(t['parameters'])}"
            for t in catalog
        )
        or "(none — this agent can only talk)"
    )
    client = build_genai_client(settings)
    resp = await client.aio.models.generate_content(
        model=settings.analysis_model,
        contents=_GENERATE_PROMPT.format(
            general_prompt=(llm.general_prompt or "(empty prompt)")[:20000],
            begin_message=llm.begin_message or "(none)",
            start_speaker=llm.start_speaker or "agent",
            tools=tools,
            count=count,
        ),
        config={"response_mime_type": "application/json", "temperature": 0.9},
    )
    data = _json_object(resp.text)
    known = {t["name"] for t in catalog}
    cases: list[dict[str, Any]] = []
    for raw in data.get("test_cases") or []:
        if not isinstance(raw, dict):
            continue
        user_prompt = str(raw.get("user_prompt") or "").strip()
        if not user_prompt:
            continue
        metrics = [str(m).strip() for m in (raw.get("metrics") or []) if str(m).strip()]
        # Drop mocks for tools the agent doesn't have: they'd never match, and
        # they'd mislead an operator reading the generated case.
        mocks = [
            m
            for m in (raw.get("tool_mocks") or [])
            if isinstance(m, dict) and m.get("tool_name") in known
        ]
        for mock in mocks:
            mock.setdefault("input_match_rule", {"type": "any"})
            mock["output"] = str(mock.get("output") or "{}")
        cases.append(
            {
                "name": str(raw.get("name") or "Generated test").strip()[:255],
                "user_prompt": user_prompt,
                "metrics": metrics,
                "tool_mocks": mocks,
            }
        )
    if not cases:
        raise RuntimeError("The model returned no usable test cases; try again.")
    return cases[:count]
