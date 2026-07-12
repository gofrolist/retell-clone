"""Surface 1 §2b get-call fields + finalize semantics feeding them."""

from tests.conftest import AUTH_HEADERS, FROM_NUMBER, INTERNAL_HEADERS


async def _create_call(client) -> str:
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": "+18155141544"},
    )
    return resp.json()["call_id"]


async def test_get_call_exposes_consumer_read_fields(client):
    call_id = await _create_call(client)
    await client.post(
        f"/internal/calls/{call_id}/events",
        headers=INTERNAL_HEADERS,
        json={"event": "call_started", "start_timestamp": 1750000000000},
    )
    resp = await client.post(
        f"/internal/calls/{call_id}/finalize",
        headers=INTERNAL_HEADERS,
        json={
            "end_timestamp": 1750000134000,
            "duration_ms": 134000,
            "disconnection_reason": "user_hangup",
            "call_status": "ended",
            "transcript": "Agent: Hi John.\nUser: Hello.",
        },
    )
    assert resp.status_code == 200

    body = (await client.get(f"/v2/get-call/{call_id}", headers=AUTH_HEADERS)).json()
    # the exact fields the 4 consumer functions read:
    assert body["direction"] == "outbound"
    assert body["from_number"] == FROM_NUMBER
    assert body["to_number"] == "+18155141544"
    assert body["transcript"] == "Agent: Hi John.\nUser: Hello."
    assert body["duration_ms"] == 134000
    assert body["call_status"] == "ended"


async def test_finalize_is_idempotent(client):
    call_id = await _create_call(client)
    payload = {"duration_ms": 5000, "call_status": "ended", "disconnection_reason": "user_hangup"}
    first = await client.post(
        f"/internal/calls/{call_id}/finalize", headers=INTERNAL_HEADERS, json=payload
    )
    second = await client.post(
        f"/internal/calls/{call_id}/finalize",
        headers=INTERNAL_HEADERS,
        json={**payload, "duration_ms": 99999},
    )
    assert first.status_code == 200
    assert second.json().get("idempotent") is True
    body = (await client.get(f"/v2/get-call/{call_id}", headers=AUTH_HEADERS)).json()
    assert body["duration_ms"] == 5000


async def test_finalize_without_transcript_preserves_accumulated_transcript(client):
    """A crash-path finalize with no transcript must not wipe what
    transcript_update events already stored."""
    call_id = await _create_call(client)
    await client.post(
        f"/internal/calls/{call_id}/events",
        headers=INTERNAL_HEADERS,
        json={"event": "call_started", "start_timestamp": 1750000000000},
    )
    await client.post(
        f"/internal/calls/{call_id}/events",
        headers=INTERNAL_HEADERS,
        json={"event": "transcript_update", "transcript": "Agent: Hi.\nUser: Hello."},
    )
    resp = await client.post(
        f"/internal/calls/{call_id}/finalize",
        headers=INTERNAL_HEADERS,
        json={"duration_ms": 5000, "call_status": "error", "disconnection_reason": "error_unknown"},
    )
    assert resp.status_code == 200
    body = (await client.get(f"/v2/get-call/{call_id}", headers=AUTH_HEADERS)).json()
    assert body["transcript"] == "Agent: Hi.\nUser: Hello."


async def test_analysis_fallback_marks_machine_detected_as_voicemail(client):
    """No Gemini key in tests → fallback analysis still keeps consumer's
    determineStatus working: machine_detected must imply in_voicemail."""
    call_id = await _create_call(client)
    await client.post(
        f"/internal/calls/{call_id}/finalize",
        headers=INTERNAL_HEADERS,
        json={
            "duration_ms": 22000,
            "call_status": "ended",
            "disconnection_reason": "machine_detected",
        },
    )
    import asyncio

    await asyncio.sleep(0.2)  # analysis pipeline runs as a background task
    body = (await client.get(f"/v2/get-call/{call_id}", headers=AUTH_HEADERS)).json()
    analysis = body["call_analysis"]
    assert analysis["in_voicemail"] is True
    # both spellings present and in sync (consumer reads `summary`)
    assert "summary" in analysis and "call_summary" in analysis
    assert analysis["summary"] == analysis["call_summary"]
    assert analysis["user_sentiment"] in ("Positive", "Negative", "Neutral", "Unknown")


async def test_list_calls_filters_by_agent(client):
    call_id = await _create_call(client)
    resp = await client.post(
        "/v2/list-calls",
        headers=AUTH_HEADERS,
        json={"filter_criteria": {"direction": ["outbound"]}, "limit": 10},
    )
    assert resp.status_code == 200
    assert any(c["call_id"] == call_id for c in resp.json())


async def test_list_calls_filters_by_user_sentiment(client):
    import asyncio

    call_id = await _create_call(client)
    other_id = await _create_call(client)
    await client.post(
        f"/internal/calls/{call_id}/finalize",
        headers=INTERNAL_HEADERS,
        json={
            "duration_ms": 30000,
            "call_status": "ended",
            "disconnection_reason": "user_hangup",
            "transcript": "Agent: Hi.\nUser: Bye.",
        },
    )
    await asyncio.sleep(0.2)  # analysis pipeline runs as a background task

    # no Gemini key in tests → fallback analysis yields sentiment "Unknown"
    matching = await client.post(
        "/v2/list-calls",
        headers=AUTH_HEADERS,
        json={"filter_criteria": {"user_sentiment": ["Unknown"]}, "limit": 10},
    )
    ids = [c["call_id"] for c in matching.json()]
    assert call_id in ids
    assert other_id not in ids  # never analyzed → no sentiment

    none = await client.post(
        "/v2/list-calls",
        headers=AUTH_HEADERS,
        json={"filter_criteria": {"user_sentiment": ["Positive"]}, "limit": 10},
    )
    assert none.json() == []
