"""Per-call mutable state and the finalize payload builder
(shape: docs/INTERNAL_API.md — POST /internal/calls/{call_id}/finalize).
"""

from __future__ import annotations

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
    # End-to-end latency samples (ms): llm ttft + tts ttfb per agent turn.
    e2e_latency_ms: list[float] = field(default_factory=list)
    # Variables captured by extract_dynamic_variable tools during the call
    # (Retell surfaces these as call.collected_dynamic_variables).
    collected_dynamic_variables: dict[str, str] = field(default_factory=dict)
    # Monotone counter behind generated tool_call_id values.
    tool_seq: int = field(default=0, init=False)

    def _stamp(self, item: dict[str, Any]) -> dict[str, Any]:
        # time_ms = offset from answer ≈ offset into the recording; items
        # logged before answer (none today) simply carry no timestamp.
        if self.answered_at_ms is not None:
            item["time_ms"] = max(0, now_ms() - self.answered_at_ms)
        return item

    def add_message(self, role: str, content: str) -> None:
        if content:
            self.items.append(self._stamp({"role": role, "content": content}))

    def add_tool_invocation(self, name: str, arguments: str) -> None:
        self.tool_seq += 1
        self.items.append(
            self._stamp(
                {
                    "role": "tool_call_invocation",
                    "name": name,
                    "arguments": arguments,
                    "tool_call_id": f"tool_call_{self.tool_seq}",
                }
            )
        )

    def add_tool_result(self, name: str, content: str) -> None:
        item: dict[str, Any] = {"role": "tool_call_result", "name": name, "content": content}
        tool_call_id = self._pending_tool_call_id(name)
        if tool_call_id:
            item["tool_call_id"] = tool_call_id
        self.items.append(self._stamp(item))

    def _pending_tool_call_id(self, name: str) -> str | None:
        """tool_call_id of the newest same-name invocation with no result yet."""
        matched = {i.get("tool_call_id") for i in self.items if i.get("role") == "tool_call_result"}
        for item in reversed(self.items):
            if item.get("role") == "tool_call_invocation" and item.get("name") == name:
                tool_call_id = item.get("tool_call_id")
                if tool_call_id not in matched:
                    return tool_call_id
        return None

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
            latency = {
                "e2e": {
                    "p50": round(_percentile(self.e2e_latency_ms, 50), 1),
                    "p95": round(_percentile(self.e2e_latency_ms, 95), 1),
                }
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
