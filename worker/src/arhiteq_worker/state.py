"""Per-call mutable state and the finalize payload builder
(shape: docs/INTERNAL_API.md — POST /internal/calls/{call_id}/finalize).
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from typing import Any


def now_ms() -> int:
    return int(time.time() * 1000)


def _percentile(samples: list[float], pct: float) -> float:
    xs = sorted(samples)
    idx = max(0, min(len(xs) - 1, math.ceil(pct / 100.0 * len(xs)) - 1))
    return xs[idx]


@dataclass(slots=True)
class SwapGuard:
    """Refuses the swap that bounces a subject back unanswered.

    A specialist that cannot answer hands the call back; the agent it lands on
    reads the same unanswered request and hands it straight over again. Both
    prompts forbid this, and neither can obey: a swap rebuilds the Gemini Live
    socket, and the replayed history carries the spoken turns without the tool
    calls, so the hand-back is invisible to the agent receiving it (see
    `live_handoff_instructions`). Call call_6ed66e6ae63f4a95f6f9294e42dd641f
    spent four socket rebuilds and half its length in that loop, and answered
    the caller's question from neither agent's knowledge.

    Two things clear the guard, because either one means the call moved rather
    than bounced:

    - the caller speaking. A swap back to the agent that just handed off, with
      nothing said in between, is the loop closing rather than a conversation
      that moved on, so anything the caller says clears it — including a
      request that legitimately belongs to the agent handed back to.
    - the agent working the subject before handing it back, which shows as a
      tool call of its own between the two swaps. The round trip Clara's split
      is built on — check-in routes a price question to pharmacy, pharmacy
      looks it up, answers and hands back, all inside one caller turn — is that
      shape, and refusing it would strand the caller on the specialist. The
      loop legs have no such call: the agent bounces the subject on arrival.

    What stays refused is a hand-back where the receiving agent neither heard
    anything new nor did anything — including one that would have answered from
    its prompt alone, which is the price of reading tool calls rather than
    speech (an agent's spoken turn is not committed to the transcript until its
    generation ends, and the swap runs inside that generation).
    """

    # The last swap made: (source, destination, caller turns, tool_seq).
    last: tuple[str, str, int, int] | None = None

    def is_ping_pong(
        self, *, current: str, destination: str, user_turns: int, tool_seq: int
    ) -> bool:
        if self.last is None:
            return False
        source, dest, turns, seq = self.last
        return (
            destination == source
            and current == dest
            and user_turns == turns
            # Both swaps count themselves: `_run_tool` records an invocation
            # before running the tool, so seq + 1 is "nothing ran in between".
            and tool_seq <= seq + 1
        )

    def record(self, *, source: str, destination: str, user_turns: int, tool_seq: int) -> None:
        self.last = (source, destination, user_turns, tool_seq)


@dataclass(slots=True)
class CallState:
    call_id: str = ""
    answered_at_ms: int | None = None
    ended_at_ms: int | None = None
    disconnection_reason: str | None = None
    in_voicemail: bool | None = None
    recording_url: str | None = None
    finalized: bool = False
    # Chronological items; role in {"agent","user"} for utterances, plus
    # tool_call_invocation / tool_call_result entries.
    items: list[dict[str, Any]] = field(default_factory=list)
    # End-to-end latency samples (ms) per agent turn: llm ttft + tts ttfb on a
    # pipeline session, ttft alone on a realtime one (no synthesis step).
    e2e_latency_ms: list[float] = field(default_factory=list)
    # Input tokens per generation. The prompt is most of it, so this is what
    # says whether a call got slower because the prompt got bigger.
    input_tokens: list[int] = field(default_factory=list)
    # Variables captured by extract_dynamic_variable tools during the call
    # (Retell surfaces these as call.collected_dynamic_variables).
    collected_dynamic_variables: dict[str, str] = field(default_factory=dict)
    # Monotone counter behind generated tool_call_id values.
    tool_seq: int = field(default=0, init=False)
    # Caller turns committed so far. `SwapGuard` reads it to tell a hand-back
    # the conversation has moved past from one it is about to bounce back.
    user_turns: int = field(default=0, init=False)
    # Replay guard for issue #250, keyed by (tool name, endpoint, resolved args).
    # Successful write-tool results only, and only for this call.
    tool_replay_cache: dict[tuple[str, str, str], tuple[float, str]] = field(
        default_factory=dict, init=False
    )
    tool_replay_locks: dict[tuple[str, str, str], asyncio.Lock] = field(
        default_factory=dict, init=False
    )

    def tool_replay_lock(self, key: tuple[str, str, str]) -> asyncio.Lock:
        """Serialise identical tool calls so the guard also covers the retry
        that arrives while the first request is still in flight.

        Claiming only after the round trip would leave the whole request open:
        livekit runs every function call of a generation as its own task, and
        the doubled generation can re-emit the call before the first one
        returns — the same reasoning `_do_agent_swap` stakes its claim before
        the first await for. Holding this across the request makes the second
        caller wait and then find the first result, instead of dialling in
        parallel with it.
        """
        lock = self.tool_replay_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self.tool_replay_locks[key] = lock
        return lock

    def replayable_tool_result(self, key: tuple[str, str, str], *, window_s: float) -> str | None:
        """The result of an identical call made within *window_s*.

        A Gemini Live turn that is abandoned after its tool ran loses the tool's
        result before it reaches the model (issue #250): the request is sent,
        the endpoint writes, and the reply is dropped. The model, still holding
        an unanswered call, issues the identical call again — so a second dose
        lands in a member's medication record while the transcript shows one
        answer. Replaying the first result satisfies the retry without a second
        write.

        Only successful results are cached (see ``remember_tool_result``): when
        the first attempt failed, a retry is the model recovering, and it has to
        reach the endpoint.
        """
        cached = self.tool_replay_cache.get(key)
        if cached is None:
            return None
        at, result = cached
        if time.monotonic() - at > window_s:
            return None
        return result

    def remember_tool_result(self, key: tuple[str, str, str], result: str) -> None:
        self.tool_replay_cache[key] = (time.monotonic(), result)

    def _stamp(self, item: dict[str, Any]) -> dict[str, Any]:
        # time_ms = offset from answer ≈ offset into the recording; items
        # logged before answer (none today) simply carry no timestamp.
        if self.answered_at_ms is not None:
            item["time_ms"] = max(0, now_ms() - self.answered_at_ms)
        return item

    def add_message(self, role: str, content: str) -> None:
        if content:
            self.items.append(self._stamp({"role": role, "content": content}))
            if role == "user":
                self.user_turns += 1

    def add_tool_invocation(self, name: str, arguments: str) -> str:
        self.tool_seq += 1
        tool_call_id = f"tool_call_{self.tool_seq}"
        self.items.append(
            self._stamp(
                {
                    "role": "tool_call_invocation",
                    "name": name,
                    "arguments": arguments,
                    "tool_call_id": tool_call_id,
                }
            )
        )
        return tool_call_id

    def add_tool_result(self, name: str, content: str, tool_call_id: str | None) -> None:
        # tool_call_id is required (pass None only when there is genuinely no
        # invocation to pair with): a silent name-matching fallback here would
        # mispair concurrent same-name tool calls, so callers must thread the
        # id returned by add_tool_invocation.
        item: dict[str, Any] = {"role": "tool_call_result", "name": name, "content": content}
        if tool_call_id:
            item["tool_call_id"] = tool_call_id
        self.items.append(self._stamp(item))

    def set_reason_once(self, reason: str) -> None:
        """First terminal reason wins (e.g. machine_detected beats the
        agent_hangup that follows when the worker hangs up on voicemail)."""
        if self.disconnection_reason is None:
            self.disconnection_reason = reason

    def transcript_object(self) -> list[dict[str, Any]]:
        return [i for i in self.items if i.get("role") in ("agent", "user")]

    def transcript_text(self) -> str:
        # CONTRACT: "Agent: …" / "User: …" lines joined with "\n" — consumers
        # parse this exact shape (docs/INTERNAL_API.md).
        lines = []
        for item in self.transcript_object():
            speaker = "Agent" if item["role"] == "agent" else "User"
            lines.append(f"{speaker}: {item['content']}")
        return "\n".join(lines)

    def _final_reason(self) -> str:
        if self.disconnection_reason:
            return self.disconnection_reason
        # Answered calls that end without an explicit worker-side reason were
        # ended by the remote party; unanswered outbound dials are no-answer.
        return "user_hangup" if self.answered_at_ms else "dial_no_answer"

    def build_finalize_payload(self) -> dict[str, Any]:
        end_ms = self.ended_at_ms or now_ms()
        reason = self._final_reason()
        if reason.startswith("error"):
            call_status = "error"
        elif self.answered_at_ms:
            call_status = "ended"
        else:
            call_status = "not_connected"
        latency: dict[str, Any] | None = None
        if self.e2e_latency_ms:
            # p50/p95 are the shape consumers already read; max and num are
            # additive. One slow turn is what a listener remembers, and a p95
            # over three turns hides it.
            latency = {
                "e2e": {
                    "p50": round(_percentile(self.e2e_latency_ms, 50), 1),
                    "p95": round(_percentile(self.e2e_latency_ms, 95), 1),
                    "max": round(max(self.e2e_latency_ms), 1),
                    "num": len(self.e2e_latency_ms),
                }
            }
            if self.input_tokens:
                latency["input_tokens"] = {
                    "p50": round(_percentile([float(t) for t in self.input_tokens], 50)),
                    "max": max(self.input_tokens),
                }
        return {
            "end_timestamp": end_ms,
            # CONTRACT: duration_ms = answer→hangup talk time, NOT dial time.
            "duration_ms": (end_ms - self.answered_at_ms) if self.answered_at_ms else 0,
            "disconnection_reason": reason,
            "call_status": call_status,
            "transcript": self.transcript_text(),
            "transcript_object": self.transcript_object(),
            "transcript_with_tool_calls": list(self.items),
            "recording_url": self.recording_url,
            "in_voicemail": self.in_voicemail,
            "latency": latency,
            "collected_dynamic_variables": self.collected_dynamic_variables or None,
        }
