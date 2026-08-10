"""A local sink standing where an audio case's tool calls would otherwise land.

**Why this exists, before anything about how it works.** Layers 1 and 2 mock
tools because an invented result steers the rest of the conversation. This
layer has a harder problem: its calls are real. Clara's tools are HTTP tools
pointed at live Supabase functions, so an audio case that says "yes, I took my
Lipitor" writes a real dose to a real member's record, and one that reaches
`send_family_sms` texts a real phone. A test suite that has to be run carefully
is one that gets run rarely, and the last thing this layer needs is a reason
not to run it.

So a case never talks to the real endpoints. Every custom tool on the agents
under test is repointed at this server, which answers from the case's `tools`
map and records what it was asked. Two things follow, and the second is the one
worth stating out loud:

* the results are deterministic, the way layer 1's mocks are; and
* **nothing a case does can escape the machine it runs on.** That is a property
  of the agent's configuration, not of the case, which is why `require_sunk`
  checks the configuration and refuses the run rather than trusting cases to
  behave.

Repointing is a separate, explicit step (`python -m audio.toolsink --rewrite`)
because it *edits agent config*. It is refused against anything but a loopback
API for the obvious reason: run it against production and every Clara tool call
in production starts arriving at a laptop that is not listening.

The worker rejects non-public tool URLs as SSRF unless
`ARHITEQ_ALLOW_PRIVATE_WEBHOOKS=1` is set (`arhiteq_worker/tools.py`), so that
belongs in the local worker's environment alongside this. Without it the tools
fail closed — which is safe, but every case that grades a tool call fails for a
reason that has nothing to do with the prompt.

stdlib `http.server` and httpx, so the CI job that installs only the dev group
covers all of it except the socket.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx

from audio.client import (
    ControlPlaneError,
    get_agent,
    get_agent_version,
    get_llm,
    publish_agent,
    update_llm_tools,
)

log = logging.getLogger("audio-toolsink")

# The port the sink listens on. Fixed rather than ephemeral because the agents
# were rewritten to a URL that names it: a port chosen at startup would be a
# port nothing points at.
DEFAULT_PORT = 8099

# The path every rewritten tool URL gets, with the tool's own name on the end.
# The name is how the sink knows which canned result to answer with — the body
# does not carry it.
PREFIX = "/tool/"

# What a tool with no entry in the case's `tools` map is answered with.
#
# Success rather than an error, and recorded rather than refused. A tool the
# case did not think about is not a reason to fail the call — but the payload
# is invented, so `unmocked` names them and the run says so in its report.
DEFAULT_RESULT = '{"ok": true}'

# Tool types the platform executes itself. They have no URL, never reach this
# server, and are not what the URL half of `require_sunk` is checking.
BUILT_IN_TYPES = frozenset(
    {
        "end_call",
        "transfer_call",
        "kb_lookup",
        "agent_swap",
        "press_digit",
        "check_availability_cal",
        "book_appointment_cal",
        "extract_dynamic_variable",
        "send_sms",
    }
)

# The built-ins that still reach the outside world, with nothing on a web call
# to stop them.
#
# Being built-in is not the same as being safe, and the module's promise is that
# nothing a case does escapes this machine. These two run against a real Cal.com
# account (`arhiteq_worker/tools.py`, `_make_cal_tool`), and `book_appointment_cal`
# WRITES: a case that talks its way to a booking creates one, on a real calendar,
# for a real event type. They carry no URL, so there is nowhere to repoint them —
# the only answers are to take them off the agent under test or to say
# `--allow-live-tools` and mean it.
#
# The rest of the list is genuinely harmless here, and it is worth writing down
# why rather than rediscovering it: `send_sms` fails closed because a web call
# has no E.164 numbers on either end (`tools.py`, `sms_numbers`), and
# `transfer_call` answers "transfer not supported on this call" with no SIP
# participant to transfer (`main.py`).
UNSINKABLE_TYPES = frozenset(
    {
        "check_availability_cal",
        "book_appointment_cal",
    }
)


class SinkError(RuntimeError):
    """The sink could not be set up, or the agents are not safe to call."""


@dataclass
class Invocation:
    """One tool call the sink answered."""

    name: str
    arguments: dict[str, Any]
    # Whether the case had a canned result for it. False means the payload the
    # agent received was invented here, which is worth knowing when reading
    # what the agent did next.
    mocked: bool = True


@dataclass
class ToolSink:
    """The server, its canned results, and what it was asked.

    Not a context manager by accident: `start`/`stop` are separate so a run
    that fails mid-call still stops the thread in its `finally`, the same way
    `place_call` always closes its room.
    """

    results: dict[str, str] = field(default_factory=dict)
    port: int = DEFAULT_PORT
    # The interface to listen on, which is NOT the same question as the address
    # the agents were rewritten to. A worker in a container reaches this machine
    # at `host.docker.internal`, which on Linux is the bridge gateway address —
    # a loopback-only socket refuses that connection, every rewritten tool URL
    # times out, and the agent talks its way past the errors while the case
    # quietly grades the harness. `bind_host_for` derives this from the address
    # the agents were given, so the two cannot drift apart.
    bind_host: str = "127.0.0.1"
    invocations: list[Invocation] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    @property
    def unmocked(self) -> list[str]:
        """Tools that were called with no canned result, in call order, once each."""
        seen = {}
        for invocation in self.invocations:
            if not invocation.mocked:
                seen[invocation.name] = None
        return list(seen)

    def record(self, name: str, arguments: dict[str, Any]) -> str:
        """Answer one call and remember it. The whole behaviour, minus the socket."""
        mocked = name in self.results
        self.invocations.append(Invocation(name=name, arguments=arguments, mocked=mocked))
        return self.results.get(name, DEFAULT_RESULT)

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = ThreadingHTTPServer((self.bind_host, self.port), _handler_for(self))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        log.info("tool sink listening on %s", base_url(self.port, host=self.bind_host))

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None


def _handler_for(sink: ToolSink) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if not self.path.startswith(PREFIX):
                self.send_error(404, "not a tool path")
                return
            name = self.path[len(PREFIX) :].strip("/")
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                arguments = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                # A tool call whose body is not JSON is worth recording as
                # having happened: it is the agent's decision that a case
                # grades, and the body is the platform's business.
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            body = sink.record(name, arguments).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            """Silence. The run's own report is the record of what was called."""

    return Handler


def base_url(port: int = DEFAULT_PORT, *, host: str = "127.0.0.1") -> str:
    return f"http://{host}:{port}"


def tool_url(name: str, *, port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> str:
    return f"{base_url(port, host=host)}{PREFIX}{name}"


def bind_host_for(sink_host: str) -> str:
    """The interface to listen on, given the address the agents were sent to.

    Loopback stays loopback: when the worker runs on this machine there is no
    reason to accept a connection from anywhere else. Anything else has to be
    reachable from off the loopback interface — `host.docker.internal` resolves
    to the bridge gateway on Linux, and a container's request arrives there —
    so the socket opens on every interface. Widening it is the point at which
    the sink becomes reachable from the network, which is why it happens only
    when the operator has already said the worker is somewhere else.
    """
    host = sink_host.strip()
    if host in ("localhost", ""):
        return "127.0.0.1"
    try:
        return "127.0.0.1" if ip_address(host).is_loopback else "0.0.0.0"
    except ValueError:
        return "0.0.0.0"


def is_sunk(url: object, *, sink_base: str) -> bool:
    """Whether a tool's URL points at the sink rather than at the world."""
    return isinstance(url, str) and url.startswith(f"{sink_base.rstrip('/')}{PREFIX}")


def live_tools(tools: list[dict], *, sink_base: str) -> list[tuple[str, str]]:
    """Every tool that would still reach a real endpoint, as (name, url).

    Built-in types are skipped: they carry no URL because the platform runs
    them, and `end_call` appearing in this list would send someone looking for
    a URL that never existed.
    """
    out = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") in BUILT_IN_TYPES:
            continue
        url = tool.get("url")
        if url and not is_sunk(url, sink_base=sink_base):
            out.append((str(tool.get("name") or "?"), str(url)))
    return out


def unsinkable_tools(tools: list[dict]) -> list[tuple[str, str]]:
    """Every built-in tool that would still reach the world, as (name, type).

    Separate from `live_tools` because the answer is different: a custom tool
    with a URL can be repointed, and one of these cannot. See `UNSINKABLE_TYPES`
    for which ones and why.
    """
    return [
        (str(tool.get("name") or tool.get("type")), str(tool.get("type")))
        for tool in tools
        if isinstance(tool, dict) and tool.get("type") in UNSINKABLE_TYPES
    ]


def sink_tools(tools: list[dict], *, sink_base: str) -> tuple[list[dict], list[str]]:
    """The same tools with every live URL repointed at the sink.

    Returns the rewritten list and the names that changed. Everything else about
    each tool — its description, its parameter schema, its name — is left
    exactly as it was, because the agent under test has to be the agent that
    runs in production in every respect but where its calls land.
    """
    rewritten = []
    changed = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") in BUILT_IN_TYPES or not tool.get("url"):
            rewritten.append(tool)
            continue
        name = str(tool.get("name") or "")
        if is_sunk(tool.get("url"), sink_base=sink_base):
            rewritten.append(tool)
            continue
        rewritten.append({**tool, "url": f"{sink_base.rstrip('/')}{PREFIX}{name}"})
        changed.append(name)
    return rewritten, changed


def require_sunk(tools: list[dict], *, agent: str, sink_base: str) -> None:
    """Refuse to place a call whose tools would reach the real world.

    This is the check the whole module exists for, and it deliberately runs on
    the *configuration* rather than on the case: a case that declares no mocks
    is not a case that makes no tool calls, and "I didn't think this one would
    log anything" is exactly how a test suite texts somebody's daughter at
    seven in the morning.

    Two kinds of escape, refused with different advice, because the fix is
    different: a custom tool pointed at a real URL can be repointed, and a
    built-in that runs against a third party cannot.
    """
    unsinkable = unsinkable_tools(tools)
    if unsinkable:
        listed = ", ".join(f"{name} ({kind})" for name, kind in unsinkable)
        raise SinkError(
            f"{agent} carries {len(unsinkable)} built-in tool(s) that reach a real service "
            f"and cannot be repointed at the sink: {listed}. A call could write to the real "
            f"calendar. Take them off the agent under test, or pass --allow-live-tools if "
            f"you mean it."
        )

    live = live_tools(tools, sink_base=sink_base)
    if not live:
        return
    listed = ", ".join(f"{name} → {url}" for name, url in live[:4])
    more = f" (+{len(live) - 4} more)" if len(live) > 4 else ""
    raise SinkError(
        f"{agent} still has {len(live)} tool(s) pointed at real endpoints: {listed}{more}. "
        f"A call would write real data. Run `python -m audio.toolsink --rewrite` first, "
        f"or pass --allow-live-tools if you mean it."
    )


@dataclass(frozen=True)
class AgentConfig:
    """One agent's tools, and where to write them back."""

    agent_id: str
    llm_id: str
    tools: list[dict]


@dataclass(frozen=True)
class Rewritten:
    """What the rewrite did to one agent.

    `published` is reported separately from `repointed` because they answer
    different questions, and the second is the one that decides whether a call
    is safe: tools can be repointed on the live rows and still be dialled at
    their real URLs until the draft is published.
    """

    repointed: list[str]
    published: bool


def reachable(
    api_base: str,
    api_key: str,
    agent_id: str,
    *,
    client: httpx.Client | None = None,
) -> list[AgentConfig]:
    """The agent, and every agent it can hand the call to, transitively.

    Following `agent_swap` is not thoroughness for its own sake. Clara is five
    agents: a call that starts at the check-in and asks what a medication costs
    finishes inside the pharmacy specialist, running the pharmacy's tools —
    `medication_price_lookup`, `send_coupon_sms`. Checking only the agent the
    call *starts* with would clear a run that ends up texting a real coupon to
    a real phone, and the check would look like it had done its job.

    Cycles are ordinary here rather than exceptional: every specialist carries
    `hand_back_to_checkin`, so the graph is a ring and a naive walk never ends.
    """
    found: dict[str, AgentConfig] = {}
    queue = [agent_id]
    while queue:
        current = queue.pop()
        if current in found:
            continue
        agent = get_agent(api_base, api_key, current, client=client)
        engine = agent.get("response_engine") or {}
        llm_id = engine.get("llm_id")
        if not llm_id:
            # A conversation-flow agent keeps its tools somewhere this does not
            # know how to read. Saying so beats reporting it as checked.
            raise SinkError(f"{current} has no llm_id — {engine.get('type')!r} agent")
        tools = get_llm(api_base, api_key, llm_id, client=client).get("general_tools") or []
        found[current] = AgentConfig(agent_id=current, llm_id=str(llm_id), tools=tools)
        queue.extend(
            str(tool["agent_id"])
            for tool in tools
            if isinstance(tool, dict) and tool.get("type") == "agent_swap" and tool.get("agent_id")
        )
    return list(found.values())


def _dialled_config(
    api_base: str,
    api_key: str,
    agent_id: str,
    *,
    version: int | str,
    client: httpx.Client | None,
) -> AgentConfig:
    """One agent's config as a call pinned to `version` would run it."""
    agent = get_agent(api_base, api_key, agent_id, version=version, client=client)
    engine = agent.get("response_engine") or {}
    if not engine.get("llm_id"):
        raise SinkError(f"{agent_id} has no llm_id — {engine.get('type')!r} agent")
    number = agent.get("version")
    if not isinstance(number, int):
        raise SinkError(
            f"{agent_id} at {version!r} reports no version number to read a snapshot at"
        )
    snapshot = get_agent_version(api_base, api_key, agent_id, number, client=client)
    config = snapshot.get("response_engine_config") or {}
    return AgentConfig(
        agent_id=agent_id,
        llm_id=str(engine["llm_id"]),
        tools=config.get("general_tools") or [],
    )


def published_tools(
    api_base: str, api_key: str, agent_id: str, *, client: httpx.Client | None = None
) -> list[dict]:
    """The tools a call to this agent would send to, right now."""
    return _dialled_config(
        api_base, api_key, agent_id, version="latest_published", client=client
    ).tools


def as_dialled(
    api_base: str,
    api_key: str,
    agent_id: str,
    *,
    version: int | str = "latest_published",
    client: httpx.Client | None = None,
) -> list[AgentConfig]:
    """The tools a call would actually send to, for the agent and its swaps.

    This is `reachable` read through the version rule instead of off the live
    rows, and the difference is not academic — it is a hole this harness
    already fell through. A published version is an immutable snapshot;
    `PATCH /update-retell-llm` opens a *draft* and edits that. So repointing
    every tool and then reading the live rows back reports a workspace that is
    completely sunk while every call still runs the published snapshot, with
    the real endpoints in it. The first Clara audio call did exactly that and
    POSTed a medication log to production Supabase.

    `agent_swap` on a live call lands on the destination's published version
    too (`docs/AGENT_VERSIONING.md`), so the walk stays on the same rule the
    whole way down.
    """
    found: dict[str, AgentConfig] = {}
    queue = [(agent_id, version)]
    while queue:
        current, ref = queue.pop()
        if current in found:
            continue
        config = _dialled_config(api_base, api_key, current, version=ref, client=client)
        found[current] = config
        # A destination is resolved at ITS published version, not at the
        # version the caller was pinned to.
        queue.extend(
            (str(tool["agent_id"]), "latest_published")
            for tool in config.tools
            if isinstance(tool, dict) and tool.get("type") == "agent_swap" and tool.get("agent_id")
        )
    return list(found.values())


def _is_loopback(api_base: str) -> bool:
    host = urlparse(api_base).hostname or ""
    if host in ("localhost", "host.docker.internal"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def rewrite(
    *,
    api_base: str,
    api_key: str,
    agent_ids: dict[str, str],
    sink_base: str,
    client: httpx.Client | None = None,
) -> dict[str, Rewritten]:
    """Repoint every agent's custom tools at the sink. Loopback APIs only.

    The refusal is not paranoia about a hypothetical. These agents are seeded
    copies for testing, but the same API and the same key shape reach
    production's Clara, where this would silently break every tool call the
    real morning dispatcher makes — and it would not fail loudly, because a
    call to an unreachable URL is a tool error the agent talks its way past.
    """
    if not _is_loopback(api_base):
        raise SinkError(
            f"refusing to rewrite tools on {api_base}: this edits agent configuration and is "
            "only ever meant for a local stack"
        )
    changes: dict[str, Rewritten] = {}
    # Names are what the operator reads; ids are what the swap graph speaks. An
    # agent reached only through a swap has no name in the map and is listed by
    # id — better than leaving it out, since the whole reason to follow the
    # graph is that it holds agents nobody remembered to list.
    names = {agent_id: name for name, agent_id in agent_ids.items()}
    for agent_id in agent_ids.values():
        for config in reachable(api_base, api_key, agent_id, client=client):
            label = names.get(config.agent_id, config.agent_id)
            if label in changes:
                continue
            rewritten, changed = sink_tools(config.tools, sink_base=sink_base)
            if changed:
                update_llm_tools(api_base, api_key, config.llm_id, rewritten, client=client)
            # Publishing is decided by what a CALL would dial, not by whether
            # this run edited anything. Both halves of that matter: the edit
            # above went to a draft, and an earlier run that patched without
            # publishing leaves live rows reading as sunk while every call
            # still dials the real endpoints. Deciding from `changed` reports
            # "already sunk" over exactly that state — which is what it did,
            # right up until a call POSTed a medication log to production.
            dialled = published_tools(api_base, api_key, config.agent_id, client=client)
            publish = bool(live_tools(dialled, sink_base=sink_base))
            if publish:
                publish_agent(api_base, api_key, config.agent_id, client=client)
            changes[label] = Rewritten(repointed=changed, published=publish)
    return changes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="audio.toolsink", description=__doc__)
    parser.add_argument(
        "--rewrite", action="store_true", help="repoint the agents' tools at the sink"
    )
    parser.add_argument("--agents", required=True, help="JSON map of agent name → agent id")
    parser.add_argument("--api-base", default=os.getenv("ARHITEQ_API_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--api-key", default=os.getenv("ARHITEQ_WORKSPACE_API_KEY", ""))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--sink-host",
        default="127.0.0.1",
        help="how the WORKER reaches this machine — host.docker.internal from a container",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.rewrite:
        parser.error("nothing to do — pass --rewrite (the server is started by run_case)")
    if not args.api_key:
        parser.error("no workspace API key (--api-key or ARHITEQ_WORKSPACE_API_KEY)")

    with open(args.agents) as handle:
        agent_ids = json.load(handle)
    try:
        changes = rewrite(
            api_base=args.api_base,
            api_key=args.api_key,
            agent_ids=agent_ids,
            sink_base=base_url(args.port, host=args.sink_host),
        )
    except (SinkError, ControlPlaneError, OSError) as err:
        print(f"tool rewrite failed: {err}", file=sys.stderr)
        return 2

    for name, result in sorted(changes.items()):
        state = "published" if result.published else "already live"
        print(
            f"{name}: {len(result.repointed)} tool(s) repointed, {state}"
            + (f" — {', '.join(result.repointed)}" if result.repointed else "")
        )
    print(
        "\nThe worker needs ARHITEQ_ALLOW_PRIVATE_WEBHOOKS=1 to call "
        f"{base_url(args.port, host=args.sink_host)}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
