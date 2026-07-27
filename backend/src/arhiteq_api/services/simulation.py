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

Three details exist so a criterion measures the agent rather than the harness:
the prompt and every tool argument resolve against `CallVariables`, so the
system placeholders a live call fills in ({{current_time}}, {{call.call_id}})
are not left literal here; once the conversation is over the agent gets one
final tool-only turn (`_wrap_up_turn`), because a prompt that says *say goodbye,
log the disposition, then hang up* otherwise loses the logging whenever the
simulated user hangs up on the goodbye; and the agent is told outright that a
turn may hold several tool calls before its spoken line, because a live model
emits them together for one user utterance while this harness asks for a single
action at a time — without that, a caller who reports two things in one breath
("I'm feeling good, and I took my pills") gets only the first one logged, and
the criterion about the second fails on the harness rather than on the agent.

Everything here is best-effort: a model or credential failure marks the run
`error` rather than raising, so one bad case can't sink a batch.
"""

import asyncio
import contextlib
import json
import logging
import re
import secrets
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from ..config import Settings, get_settings
from ..ids import new_call_id
from ..models import (
    TEST_RUN_TERMINAL_STATUSES,
    Agent,
    RetellLLM,
    TestCaseBatchJob,
    TestCaseDefinition,
    TestCaseJob,
    now_ms,
)
from .gemini import build_genai_client, genai_credentials_available, is_live_model
from .template_variables import (
    CallVariables,
    prompt_variables,
    resolve_deep,
    resolve_template,
)

log = logging.getLogger(__name__)

# A simulated call is capped so a looping agent (or a user simulator that never
# hangs up) can't burn tokens forever. Turns are user+agent exchanges.
MAX_TURNS = 16
# Consecutive tool calls the agent may make inside one turn before the harness
# forces it to speak — guards against a tool-call loop. The last iteration is
# spent on the spoken line, so a turn holds this many calls minus one and still
# says something; the agent is asked to chain the calls one utterance earns, so
# this sits above the number of per-fact loggers an agent plausibly fires at
# once rather than at the loop-guard minimum.
MAX_TOOL_CALLS_PER_TURN = 6
# How many simulated calls run at once across the whole process. A batch may
# hold up to 1000 cases and each case is dozens of model round-trips, so this
# is what stops one POST — or ten — from saturating the Gemini quota.
RUN_CONCURRENCY = 4
# A batch still `in_progress` after this long was orphaned by a restart the
# graceful-shutdown path didn't get to run. Readers treat it as finished.
STALE_BATCH_MS = 60 * 60 * 1000

# Tool types that hang up when called: reaching one ends the simulated call.
_TERMINAL_TOOL_TYPES = ("end_call", "transfer_call", "agent_swap")

# A prompt that reads any of the time family can be put at a chosen moment by
# pinning {{current_time}}, so generation offers it that variable.
_READS_THE_CLOCK = re.compile(r"\{\{\s*current_(?:time|hour|calendar)")

_AGENT_PROMPT = """\
{general_prompt}

--- SIMULATION HARNESS (not part of your persona) ---
You are the agent described above, on a live phone call. Produce the agent's
NEXT action only — never write the user's lines, and never mention this harness.
One action per reply, but your turn ends only when you speak, and the caller
hears nothing until then: when the last thing they said calls for more than one
tool, make each of those calls in its own reply first, and say your line after
the last of them. A call that hangs up or hands the call to someone else is the
exception — it takes the line down, so say what you have to say before it, never
after.
{tools}
Reply with STRICT JSON (no markdown), exactly one of:
{{"action": "speak", "content": "<what the agent says next>"}}
{{"action": "tool_call", "tool_name": "<name>", "arguments": {{<arguments object>}}}}

Conversation so far:
{history}"""

_WRAP_UP_PROMPT = """\
{general_prompt}

--- SIMULATION HARNESS (not part of your persona) ---
You are the agent described above. The call is over — {ending} — so nobody can
hear you any more: do NOT speak. Make only the calls you would make while
hanging up: disposition logging, saving notes, ending the call. Make none if
your instructions call for none, and never repeat a call already in the
transcript.
{tools}
Reply with STRICT JSON (no markdown), exactly one of:
{{"action": "tool_call", "tool_name": "<name>", "arguments": {{<arguments object>}}}}
{{"action": "done"}}

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

One exception: while the agent is closing the call — saying goodbye, or asking
whether there is anything else — answer it briefly ("no, that's all", "bye")
and let the agent hang up. Hang up first only if it will not let the call end.

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

How the call ended: {ending}. Tool calls that follow the agent's last spoken
line are the work it does while hanging up, exactly as on a live call.

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

Dynamic variables the prompt reads:
{variables}

A variable you leave unset stays the literal text `{{{{name}}}}` when the case
runs, exactly as an unset variable would on a real call. Any branch the prompt
gates on that variable is then unreachable, so a criterion about that branch
fails no matter how well the agent behaves. Give every scenario the variables
its branch needs.

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
  before offering a time"). Prefer observable behaviour over tone. Only assert
  behaviour the variables you set actually put in reach.
* "dynamic_variables": {{"<name>": "<value>"}} over the names listed above —
  a concrete string value for every variable this scenario depends on, chosen
  to put the agent in the state the scenario describes (the flags gating the
  branch under test, plus anything the prompt uses in tool arguments or the
  greeting). Values must be strings, written in the shape the prompt reads them
  in: if the prompt picks times, names or items out of a variable, write those
  in ("Lipitor at 08:00, Metformin at 19:00", not "Lipitor"), following any
  example the prompt gives. A criterion about a step the prompt only reaches
  when a value is in a particular shape needs that shape, or it cannot pass.
  When the prompt gates a step on the time of day ("within 60 minutes of the
  dose", "only after 14:00", a morning flow versus an evening one), also set
  "current_time" to "<YYYY-MM-DDTHH:MM>" inside the window that step needs, and
  write every other time this scenario depends on relative to it. Setting it
  pins the clock the call runs on; leaving it out grades the case against
  whatever time it happens to be run at, which puts the branch it is about out
  of reach for most of the day.
  Use {{}} only if the prompt reads none.
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


def _variable_value(value: Any) -> str:
    """A model-written variable as the string a live call would carry.

    The generate prompt asks for strings, but models routinely answer a
    true/false flag with a JSON boolean. Plain ``str()`` would store that as
    Python's ``"True"``, so a prompt branching on ``= "true"`` still would not
    fire — the exact failure these variables exist to prevent. Booleans and
    null therefore render JSON-style; everything else stringifies normally.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _metric_key(metric: str) -> str:
    """Normalize a criterion for matching a judge verdict back to its criterion.

    Drops a leading list number (the judge prompt numbers them), collapses
    whitespace, and folds case and trailing punctuation.
    """
    text = re.sub(r"^\s*\d+[.)]\s*", "", metric)
    return re.sub(r"\s+", " ", text).strip().rstrip(".").casefold()


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
        variables: Mapping[str, Any] | None = None,
    ) -> None:
        self._client = build_genai_client(settings)
        self._settings = settings
        self._general_prompt = general_prompt
        self._catalog = catalog
        self._definition = definition
        self._agent_model = agent_model
        self._begin_message = begin_message
        self._start_speaker = start_speaker
        # The same mapping the prompt was resolved from, kept so tool-call
        # arguments resolve too — a live call runs resolve_deep over them.
        self._variables: Mapping[str, Any] = variables or {}
        self.transcript: list[dict[str, Any]] = []
        # How the call finished, in the judge's and the wrap-up prompt's words.
        # Set by run(); the default covers a transcript nothing ever ran.
        self.ending = "the call did not get started"

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

    async def _invoke_tool(self, action: dict[str, Any]) -> bool:
        """Record one tool call and its mocked result. False if the call ended.

        Arguments are template-resolved first, the way the worker resolves them
        before hitting a customer endpoint: a prompt that says to pass
        ``retell_call_id={{call.call_id}}`` would otherwise put the literal
        placeholder in the transcript, and mock `partial_match` rules would be
        compared against it.
        """
        name = str(action.get("tool_name") or "")
        args = action.get("arguments")
        args = resolve_deep(args if isinstance(args, dict) else {}, self._variables)
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
        return tool is None or tool["type"] not in _TERMINAL_TOOL_TYPES

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
            if not await self._invoke_tool(action):
                return False
        # Stuck in tool calls: force one spoken turn so the transcript shows it.
        self.transcript.append(
            {"role": "agent", "content": "(the agent kept calling tools without speaking)"}
        )
        return True

    async def _wrap_up_turn(self) -> None:
        """Let the agent finish the tool work a hang-up interrupted.

        A live agent's closing sequence is speak-then-log: prompts routinely say
        to say goodbye, log the disposition and only then end the call — and the
        agent under test is told to produce one action at a time, so its turn is
        over the moment it speaks. Without this turn, whether "the agent logs the
        outcome" passes would come down to whether the simulated user happened to
        answer the goodbye (agent gets another turn) or hang up on it (agent
        never acts again) — a coin flip, not a verdict about the agent.

        Speaking is not offered: the line is already down. The calls land in the
        transcript in order, which is what the same wrap-up looks like on a real
        call.
        """
        for _ in range(MAX_TOOL_CALLS_PER_TURN):
            action = await self._json_call(
                self._agent_model,
                _WRAP_UP_PROMPT.format(
                    general_prompt=self._general_prompt,
                    ending=self.ending,
                    tools=_tool_block(self._catalog),
                    history=_history(self.transcript),
                ),
                0.0,
            )
            if action.get("action") != "tool_call":
                return
            if not await self._invoke_tool(action):
                return

    async def _user_turn(self) -> str | None:
        """Let the simulated user speak. Returns why the call ended, or None.

        A hang-up and a user simulator that produced nothing to say both end the
        run, but they are not the same fact about the scenario: only one of them
        is something the caller did, and only that one earns the agent a wrap-up
        turn.
        """
        action = await self._json_call(
            self._settings.analysis_model,
            _USER_PROMPT.format(
                user_prompt=self._definition.get("user_prompt") or "",
                history=_history(self.transcript),
            ),
            0.7,
        )
        if action.get("action") == "hangup":
            return "the caller hung up"
        content = str(action.get("content") or "").strip()
        if not content:
            return "the harness got no reply from the simulated caller"
        self.transcript.append({"role": "user", "content": content})
        return None

    async def run(self) -> list[dict[str, Any]]:
        # Retell semantics: `start_speaker == "user"` means the agent waits for
        # the user; otherwise it opens with begin_message, or improvises one.
        if self._start_speaker != "user":
            if self._begin_message:
                self.transcript.append({"role": "agent", "content": self._begin_message})
            elif not await self._agent_turn():
                self.ending = "the agent ended it"
                return self.transcript
        self.ending = "the harness hit its turn limit before the call ended"
        for _ in range(MAX_TURNS):
            ended = await self._user_turn()
            if ended is not None:
                self.ending = ended
                break
            if not await self._agent_turn():
                # The agent hung up (or transferred) itself: it already ran
                # whatever it meant to run, so there is nothing to finish.
                self.ending = "the agent ended it"
                return self.transcript
        # Only a caller who hung up on an agent that was talking leaves work
        # unfinished. A run that ran out of turns was cut off mid-conversation,
        # and one where the agent never spoke has nothing to wrap up — handing
        # either a free tool-only turn would pass "the agent logs the outcome"
        # for a call the agent never actually closed.
        if self.ending == "the caller hung up" and any(
            item["role"] == "agent" for item in self.transcript
        ):
            # Best-effort, like everything else here: the conversation is
            # already complete and gradeable, so a failure on this one extra
            # model call must not turn a finished run into `error`.
            try:
                await self._wrap_up_turn()
            except Exception:
                log.exception("simulation: the wrap-up turn failed; grading the call as it is")
        return self.transcript

    async def judge(self) -> tuple[str, list[dict[str, Any]]]:
        """Grade the transcript. Returns (status, per-metric results).

        Raises when the case has no criteria: a run that graded nothing is not
        a pass, and reporting it as one would put a green badge on an untested
        agent.
        """
        metrics = [str(m) for m in (self._definition.get("metrics") or []) if str(m).strip()]
        if not metrics:
            raise ValueError("This test case has no success criteria to grade.")
        data = await self._json_call(
            self._settings.analysis_model,
            _JUDGE_PROMPT.format(
                user_prompt=self._definition.get("user_prompt") or "",
                transcript=_history(self.transcript),
                ending=self.ending,
                metrics="\n".join(f"{i + 1}. {m}" for i, m in enumerate(metrics)),
            ),
            0.0,
        )
        raw = [r for r in (data.get("results") or []) if isinstance(r, dict)]
        # Match on a normalized key: the prompt numbers the criteria, and models
        # routinely echo the "1. " back or re-wrap the whitespace. Without this,
        # a cosmetic difference would score every criterion "ungraded" and turn
        # a clean run into a hard FAIL.
        by_metric = {_metric_key(str(r.get("metric"))): r for r in raw}
        results: list[dict[str, Any]] = []
        for position, metric in enumerate(metrics):
            entry = by_metric.get(_metric_key(metric))
            # Last resort when the judge rewrote a criterion outright: trust the
            # order it was asked to grade in, but only when it returned exactly
            # as many verdicts as there were criteria.
            if entry is None and len(raw) == len(metrics):
                entry = raw[position]
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
        return f"All {len(results)} criteria passed."
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

    status: str = "error"
    results: list[dict[str, Any]] = []
    explanation = ""
    # Held outside the try so a mid-call failure can still save the turns that
    # did happen — how far the call got is the most useful thing about a
    # failed run, and discarding it leaves the drawer blank.
    simulator: _Simulator | None = None
    try:
        # Checked before anything else: a case with nothing to grade can only
        # ever end in `error`, so don't spend a whole simulated call finding out.
        if not [m for m in (snapshot.get("metrics") or []) if str(m).strip()]:
            raise RuntimeError("This test case has no success criteria to grade.")
        if not genai_credentials_available(settings):
            raise RuntimeError(
                "No Gemini credentials configured; simulation runs need GOOGLE_API_KEY "
                "or Vertex ADC."
            )
        if llm is None:
            raise RuntimeError("The response engine for this test no longer exists.")

        # Dynamic variables resolve exactly as they do on a live call, so a
        # prompt that depends on {{name}} is tested with a value, not the
        # literal placeholder. System variables come from CallVariables for the
        # same reason: a prompt that reads {{current_time}} has to be told the
        # time, or every branch behind it is tested in the one state a live call
        # never runs in.
        start_speaker = str(llm.start_speaker or "agent")
        variables = {str(k): str(v) for k, v in (snapshot.get("dynamic_variables") or {}).items()}
        merged = CallVariables(
            {**(llm.default_dynamic_variables or {}), **variables},
            call_id=new_call_id(),
            start_timestamp_ms=now_ms(),
            default_timezone=agent.timezone if agent else None,
            # Only a simulated call may pin its clock: a case that says it is
            # testing the morning dose window has to be graded in the morning,
            # whatever hour the operator pressed Run at.
            pin_clock=True,
        )
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
            start_speaker=start_speaker,
            variables=merged,
        )
        await simulator.run()
        status, results = await simulator.judge()
        explanation = _explain(status, results)
    except Exception as exc:
        log.exception("simulation run %s failed", job_id)
        status, explanation = "error", f"{type(exc).__name__}: {exc}"
    transcript = simulator.transcript if simulator is not None else []

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


_run_slots: asyncio.Semaphore | None = None
_run_slots_loop: asyncio.AbstractEventLoop | None = None


def _slots() -> asyncio.Semaphore:
    """The process-wide run limiter, created against the running loop.

    A per-batch semaphore would multiply — ten concurrent batches at four each
    is forty live conversations — so every batch shares this one. It is rebuilt
    when the loop changes, since tests run each case on a fresh loop and a
    semaphore with waiters parked on a dead loop would never release.
    """
    global _run_slots, _run_slots_loop
    loop = asyncio.get_running_loop()
    if _run_slots is None or _run_slots_loop is not loop:
        _run_slots = asyncio.Semaphore(RUN_CONCURRENCY)
        _run_slots_loop = loop
    return _run_slots


async def run_batch(factory: Any, batch_job_id: str, job_ids: list[str]) -> None:
    """Run every job of a batch, then roll the counts up onto the batch row."""
    slots = _slots()

    async def guarded(job_id: str) -> str:
        async with slots:
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


# In-flight batches, kept as strong references so they aren't garbage-collected
# — and so shutdown knows which rows to close out.
_batch_tasks: dict[asyncio.Task, str] = {}


def spawn_batch(factory: Any, batch_job_id: str, job_ids: list[str]) -> None:
    """Start a batch in the background (create-batch-test returns immediately)."""
    task = asyncio.create_task(run_batch(factory, batch_job_id, job_ids))
    _batch_tasks[task] = batch_job_id
    task.add_done_callback(_batch_tasks.pop)


async def _abandon_batch(factory: Any, batch_job_id: str) -> None:
    """Close out a batch whose runs were cancelled, so it can't poll forever.

    Unfinished runs become `error` with a reason; the batch reaches `complete`
    with what it did manage to grade. Without this a restart would leave the
    row `in_progress` for good and the dashboard would poll it indefinitely.
    """
    async with factory() as session:
        batch = await session.get(TestCaseBatchJob, batch_job_id)
        if batch is None or batch.status == "complete":
            return
        jobs = (
            await session.scalars(
                select(TestCaseJob).where(TestCaseJob.test_case_batch_job_id == batch_job_id)
            )
        ).all()
        for job in jobs:
            if job.status in TEST_RUN_TERMINAL_STATUSES:
                continue
            job.status = "error"
            job.result_explanation = "The API restarted while this run was in progress."
            job.user_modified_timestamp = now_ms()
        batch.pass_count = sum(j.status == "pass" for j in jobs)
        batch.fail_count = sum(j.status == "fail" for j in jobs)
        batch.error_count = sum(j.status not in ("pass", "fail") for j in jobs)
        batch.status = "complete"
        batch.user_modified_timestamp = now_ms()
        await session.commit()


async def shutdown(factory: Any = None) -> None:
    """Cancel in-flight batches on app shutdown and close out their rows."""
    pending = dict(_batch_tasks)
    for task in pending:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    if factory is None:
        return
    for batch_job_id in set(pending.values()):
        try:
            await _abandon_batch(factory, batch_job_id)
        except Exception:  # shutdown must not raise past the lifespan
            log.exception("could not close out abandoned batch %s", batch_job_id)


async def generate_test_cases(llm: RetellLLM, count: int) -> list[dict[str, Any]]:
    """Write test cases from the agent's own prompt and tool catalog.

    This is the "agent tests itself" path: nothing but the saved prompt, the
    greeting and the tool schemas goes in, and drafted cases come back for the
    operator to keep or edit. Raises on failure — the caller surfaces it,
    because a silent empty list reads as "your agent needs no tests".

    Each draft carries the `dynamic_variables` its scenario needs. Without them
    a prompt that gates a branch on `{{is_last_day_of_trial}}` would generate a
    case about that branch and then run it with the flag unset — the branch
    never fires and the case fails on its own missing setup, not on the agent.
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
    # The prompt's own placeholders, so the model writes variables that exist
    # rather than inventing names the prompt never reads. Defaults are shown as
    # such: they already have a value, so a case only needs to override them.
    defaults = llm.default_dynamic_variables or {}
    names = dict.fromkeys(
        prompt_variables(llm.general_prompt or "") + prompt_variables(llm.begin_message or "")
    )
    lines = [
        f"- {{{{{name}}}}}"
        + (f' (agent default: "{str(defaults[name])[:60]}")' if name in defaults else "")
        for name in names
    ]
    # `current_time` is settable but usually not *listed*: a prompt asks the
    # time as {{current_time_{{user_timezone}}}}, and `prompt_variables` reports
    # the inner name only. Without this line the clock instruction below names a
    # key the model was told not to invent, which is exactly the prompt shape
    # that needs it most.
    if _READS_THE_CLOCK.search(f"{llm.general_prompt or ''}\n{llm.begin_message or ''}"):
        lines.append(
            "- {{current_time}} (settable: pins the clock the whole call is "
            "read on, including the {{current_time_<zone>}} and {{current_hour}} "
            "forms this prompt uses)"
        )
    variables = "\n".join(lines) or "(none — this prompt reads no dynamic variables)"
    client = build_genai_client(settings)
    resp = await client.aio.models.generate_content(
        model=settings.analysis_model,
        contents=_GENERATE_PROMPT.format(
            general_prompt=(llm.general_prompt or "(empty prompt)")[:20000],
            begin_message=llm.begin_message or "(none)",
            start_speaker=llm.start_speaker or "agent",
            tools=tools,
            variables=variables,
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
        metrics = [str(m).strip() for m in (raw.get("metrics") or []) if str(m).strip()]
        # A case with no scenario has nothing to act out, and one with no
        # criteria can never be graded — keeping either would put an
        # unrunnable row in the operator's suite.
        if not user_prompt or not metrics:
            continue
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
        # Kept as written rather than filtered to `names`: a prompt can reach a
        # variable the regex never sees (a nested time key resolves to
        # `current_time_<zone>`), and an unused key costs nothing at run time.
        raw_variables = raw.get("dynamic_variables")
        case_variables = (
            {str(k).strip(): _variable_value(v) for k, v in raw_variables.items() if str(k).strip()}
            if isinstance(raw_variables, dict)
            else {}
        )
        cases.append(
            {
                "name": str(raw.get("name") or "Generated test").strip()[:255],
                "user_prompt": user_prompt,
                "metrics": metrics,
                "dynamic_variables": case_variables,
                "tool_mocks": mocks,
            }
        )
    if not cases:
        raise RuntimeError("The model returned no usable test cases; try again.")
    return cases[:count]
