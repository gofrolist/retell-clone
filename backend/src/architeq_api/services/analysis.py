"""Post-call analysis via Gemini.

Produces the `call_analysis` object. `user_sentiment` must be one of
Positive | Negative | Neutral | Unknown — the consumer substring-matches these
(case-insensitively) into its own categories. `summary` and `call_summary`
are kept in sync (consumer reads `summary`; Retell canonical is
`call_summary`).
"""

import json
import logging
from typing import Any

from ..config import get_settings
from .metrics import ANALYSIS_RUNS

log = logging.getLogger(__name__)

_PROMPT = """\
Analyze this phone call transcript between an AI agent and a user.

Transcript:
{transcript}

Call context: direction={direction}, duration_ms={duration_ms}, \
disconnection_reason={disconnection_reason}

Return STRICT JSON (no markdown) with exactly these keys:
- "call_summary": 2-4 sentence factual summary of the call.
- "user_sentiment": one of "Positive", "Negative", "Neutral", "Unknown".
- "call_successful": boolean — did the agent accomplish its purpose.
- "in_voicemail": boolean — true if the call reached an answering machine or
  voicemail greeting instead of a live person.
"""


def _fallback(in_voicemail_hint: bool | None) -> dict[str, Any]:
    return {
        "call_summary": None,
        "summary": None,
        "user_sentiment": "Unknown",
        "call_successful": False,
        "in_voicemail": bool(in_voicemail_hint),
        "custom_analysis_data": {},
    }


async def analyze_call(
    transcript: str | None,
    direction: str | None,
    duration_ms: int | None,
    disconnection_reason: str | None,
    in_voicemail_hint: bool | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not transcript or not settings.google_api_key:
        result = _fallback(in_voicemail_hint)
        if disconnection_reason == "machine_detected":
            result["in_voicemail"] = True
        ANALYSIS_RUNS.labels(outcome="skipped").inc()
        return result

    try:
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        prompt = _PROMPT.format(
            transcript=transcript[:30000],
            direction=direction,
            duration_ms=duration_ms,
            disconnection_reason=disconnection_reason,
        )
        resp = await client.aio.models.generate_content(
            model=settings.analysis_model,
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0.0},
        )
        data = json.loads(resp.text)
        sentiment = str(data.get("user_sentiment", "Unknown")).capitalize()
        if sentiment not in ("Positive", "Negative", "Neutral", "Unknown"):
            sentiment = "Unknown"
        in_voicemail = bool(data.get("in_voicemail", False))
        # A worker-side AMD verdict (Telnyx AMD / greeting classifier) wins
        # over the transcript-only guess.
        if in_voicemail_hint is not None:
            in_voicemail = in_voicemail_hint or in_voicemail
        summary = data.get("call_summary")
        ANALYSIS_RUNS.labels(outcome="ok").inc()
        return {
            "call_summary": summary,
            "summary": summary,
            "user_sentiment": sentiment,
            "call_successful": bool(data.get("call_successful", False)),
            "in_voicemail": in_voicemail,
            "custom_analysis_data": {},
        }
    except Exception:  # noqa: BLE001
        log.exception("post-call analysis failed")
        ANALYSIS_RUNS.labels(outcome="error").inc()
        return _fallback(in_voicemail_hint)
