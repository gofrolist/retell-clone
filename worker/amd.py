"""Answering-machine / voicemail detection.

Two signals, in priority order:
1. Telnyx AMD result surfaced as SIP participant attributes (LiveKit SIP
   exposes provider SIP headers as ``sip.h.*`` participant attributes when
   header pass-through is enabled on the trunk).
2. Heuristic: the first ~5s of callee speech is classified by Gemini as
   answering-machine greeting vs. live human.

On a machine verdict the caller of this module sets in_voicemail=true and
uses disconnection_reason="machine_detected" (docs/ARCHITECTURE.md).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger("architeq-worker.amd")

# TODO: verify exact attribute keys against the livekit-sip header-mapping
# config used in infra/ (headers must be whitelisted on the inbound trunk /
# outbound SIPParticipant for these to appear).
_AMD_ATTRIBUTE_KEYS = (
    "sip.h.x-telnyx-amd-result",
    "sip.h.x-amd-result",
    "sip.telnyx.amd_result",
    "sip.amd.result",
)

_MACHINE_VALUES = {"machine", "answering_machine", "machine_greeting", "fax", "voicemail"}
_HUMAN_VALUES = {"human", "human_residence", "human_business", "person"}

_CLASSIFIER_PROMPT = (
    "You classify the opening speech of a phone callee. Decide whether it is "
    "an answering-machine / voicemail greeting (recorded message, carrier "
    "voicemail, 'leave a message after the tone', business after-hours "
    "recording) or a live human answering. Reply with exactly one word: "
    "YES if it is an answering machine or voicemail greeting, NO otherwise."
)


def read_sip_amd_result(attributes: Mapping[str, str]) -> str | None:
    """Return "machine" | "human" from Telnyx AMD SIP attributes, else None."""
    for key in _AMD_ATTRIBUTE_KEYS:
        value = attributes.get(key)
        if not value:
            continue
        normalized = value.strip().lower()
        if normalized in _MACHINE_VALUES:
            return "machine"
        if normalized in _HUMAN_VALUES:
            return "human"
        logger.info("unrecognized AMD attribute %s=%r", key, value)
    return None


async def classify_greeting_is_voicemail(llm: Any, greeting: str) -> bool:
    """Ask Gemini whether *greeting* is an answering-machine greeting.

    *llm* is a livekit-agents LLM instance (google.LLM). Fails open (False):
    a wrong "human" verdict just means the agent talks to a machine, while a
    wrong "machine" verdict would hang up on a person.
    """
    if not greeting.strip():
        return False
    try:
        from livekit.agents.llm import ChatContext

        chat_ctx = ChatContext()
        chat_ctx.add_message(role="system", content=_CLASSIFIER_PROMPT)
        chat_ctx.add_message(role="user", content=greeting)
        answer = ""
        async with llm.chat(chat_ctx=chat_ctx) as stream:
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                if delta is not None and getattr(delta, "content", None):
                    answer += delta.content
        return answer.strip().upper().startswith("YES")
    except Exception as exc:  # noqa: BLE001 - AMD must never take down a call
        logger.warning("greeting classification failed: %s", exc)
        return False


def voicemail_message(voicemail_option: Mapping[str, Any] | None) -> str | None:
    """Extract the message to leave from agent.voicemail_option, if any.

    Retell shape: {"action": {"type": "static_text", "text": "..."}}.
    None → hang up without leaving a message.
    """
    if not voicemail_option:
        return None
    action = voicemail_option.get("action") or {}
    if action.get("type") == "static_text":
        text = action.get("text")
        return text if isinstance(text, str) and text.strip() else None
    return None
