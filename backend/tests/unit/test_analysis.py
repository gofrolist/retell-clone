"""Gemini post-call analysis: happy path (mocked client), normalization,
AMD-hint precedence, and failure fallback."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.services import analysis


@pytest.fixture
def with_api_key(monkeypatch):
    monkeypatch.setattr(analysis, "get_settings", lambda: Settings(google_api_key="fake-key"))


def _gemini_returning(payload: dict):
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(
                generate_content=AsyncMock(return_value=SimpleNamespace(text=json.dumps(payload)))
            )
        )
    )
    return patch("google.genai.Client", return_value=client)


async def test_happy_path_normalizes_and_syncs_summary(with_api_key):
    with _gemini_returning(
        {
            "call_summary": "User confirmed the appointment.",
            "user_sentiment": "positive",  # lowercase from the model
            "call_successful": True,
            "in_voicemail": False,
        }
    ):
        result = await analysis.analyze_call(
            "Agent: Hi\nUser: Hello", "outbound", 60000, "user_hangup"
        )
    assert result["user_sentiment"] == "Positive"
    assert result["summary"] == result["call_summary"] == "User confirmed the appointment."
    assert result["call_successful"] is True
    assert result["in_voicemail"] is False


async def test_invalid_sentiment_becomes_unknown(with_api_key):
    with _gemini_returning(
        {"call_summary": "x", "user_sentiment": "ecstatic", "call_successful": False}
    ):
        result = await analysis.analyze_call("t", "outbound", 1000, None)
    assert result["user_sentiment"] == "Unknown"


async def test_worker_amd_hint_wins_over_model(with_api_key):
    with _gemini_returning(
        {"call_summary": "x", "user_sentiment": "Neutral", "in_voicemail": False}
    ):
        result = await analysis.analyze_call("t", "outbound", 1000, None, in_voicemail_hint=True)
    assert result["in_voicemail"] is True


async def test_model_error_falls_back(with_api_key):
    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=AsyncMock(side_effect=RuntimeError("boom")))
        )
    )
    with patch("google.genai.Client", return_value=client):
        result = await analysis.analyze_call("t", "outbound", 1000, None, in_voicemail_hint=None)
    assert result["user_sentiment"] == "Unknown"
    assert "summary" in result and "call_summary" in result


async def test_no_transcript_skips_model_and_keeps_machine_detected():
    result = await analysis.analyze_call(None, "outbound", 0, "machine_detected")
    assert result["in_voicemail"] is True


async def test_seed_creates_workspace_and_demo(client):
    """seed() against the live test DB (also exercised by ops tooling)."""
    from app.seed import seed

    await seed(api_key="key_seeded_by_test_000000000000", workspace_name="Seeded", demo=True)
    resp = await client.get(
        "/list-agents", headers={"Authorization": "Bearer key_seeded_by_test_000000000000"}
    )
    assert resp.status_code == 200
    assert any(a["agent_name"] == "Demo Agent" for a in resp.json())
    # idempotent second run
    await seed(api_key="key_seeded_by_test_000000000000", workspace_name="Seeded", demo=True)
