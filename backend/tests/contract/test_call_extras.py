"""register-phone-call, create-web-call, update-call, delete-call,
rerun-call-analysis."""

from arhiteq_api.config import get_settings
from tests.conftest import AGENT_ID, AUTH_HEADERS, OTHER_AUTH_HEADERS


async def _register_call(client, **overrides):
    payload = {
        "agent_id": AGENT_ID,
        "from_number": "+14157774444",
        "to_number": "+12137774445",
        "direction": "outbound",
        **overrides,
    }
    resp = await client.post("/v2/register-phone-call", headers=AUTH_HEADERS, json=payload)
    assert resp.status_code == 201
    return resp.json()


# ── register-phone-call ─────────────────────────────────────────────────────


async def test_register_phone_call_creates_registered_call(client):
    body = await _register_call(
        client,
        metadata={"crm_id": "7"},
        retell_llm_dynamic_variables={"first_name": "Ada"},
    )
    assert body["call_id"].startswith("call_")
    assert body["call_type"] == "phone_call"
    assert body["call_status"] == "registered"
    assert body["direction"] == "outbound"
    assert body["agent_id"] == AGENT_ID
    assert body["from_number"] == "+14157774444"
    assert body["to_number"] == "+12137774445"
    assert body["metadata"] == {"crm_id": "7"}
    assert body["retell_llm_dynamic_variables"] == {"first_name": "Ada"}


async def test_register_phone_call_does_not_dial(client, monkeypatch):
    async def _boom(call):  # custom telephony: our SIP stack must not dial
        raise AssertionError("register-phone-call must not start telephony")

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _boom)
    await _register_call(client)


async def test_register_phone_call_requires_known_agent(client):
    resp = await client.post(
        "/v2/register-phone-call",
        headers=AUTH_HEADERS,
        json={"agent_id": "agent_does_not_exist"},
    )
    assert resp.status_code == 422


async def test_register_phone_call_requires_auth(client):
    resp = await client.post("/v2/register-phone-call", json={"agent_id": AGENT_ID})
    assert resp.status_code == 401


# ── create-web-call ─────────────────────────────────────────────────────────


async def test_create_web_call_returns_access_token(client):
    resp = await client.post(
        "/v2/create-web-call",
        headers=AUTH_HEADERS,
        json={"agent_id": AGENT_ID, "metadata": {"user_id": "u1"}},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["call_type"] == "web_call"
    assert body["call_status"] == "registered"
    assert body["agent_id"] == AGENT_ID
    assert isinstance(body["access_token"], str) and body["access_token"]
    # Web calls carry no phone fields.
    assert "from_number" not in body
    assert "to_number" not in body
    assert "direction" not in body


async def test_get_call_keeps_web_call_shape(client):
    created = (
        await client.post("/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID})
    ).json()
    got = await client.get(f"/v2/get-call/{created['call_id']}", headers=AUTH_HEADERS)
    assert got.status_code == 200
    body = got.json()
    assert body["call_type"] == "web_call"
    assert body["access_token"] == created["access_token"]
    assert "from_number" not in body


async def test_create_web_call_unknown_agent_is_non_2xx(client):
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": "agent_nope"}
    )
    assert resp.status_code == 422


async def test_create_web_call_dispatches_agent(client, monkeypatch):
    dispatched: list[str] = []

    async def _fake_dispatch(call):
        dispatched.append(call.call_id)

    monkeypatch.setattr("arhiteq_api.services.telephony.dispatch_agent", _fake_dispatch)
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 201
    assert dispatched == [resp.json()["call_id"]]


async def test_create_web_call_returns_livekit_server_url(client):
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 201
    # public_livekit_url is unset in tests, so the field falls back to livekit_url.
    assert resp.json()["livekit_server_url"] == get_settings().livekit_url


async def test_create_web_call_dispatch_failure_is_500(client, monkeypatch):
    async def _boom(call):
        raise RuntimeError("livekit down")

    monkeypatch.setattr("arhiteq_api.services.telephony.dispatch_agent", _boom)
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 500

    # The 500 doesn't carry a call_id (the call was created then degraded), so
    # look the row up via list-calls and confirm it was marked failed rather
    # than left silently "registered".
    rows = (
        await client.post(
            "/v2/list-calls",
            headers=AUTH_HEADERS,
            json={"filter_criteria": {"agent_id": [AGENT_ID]}, "limit": 10},
        )
    ).json()
    assert len(rows) == 1
    got = await client.get(f"/v2/get-call/{rows[0]['call_id']}", headers=AUTH_HEADERS)
    assert got.status_code == 200
    body = got.json()
    assert body["call_status"] == "error"
    assert body["disconnection_reason"] == "error_telephony"


async def test_create_web_call_429_at_concurrency_limit(client, monkeypatch):
    from arhiteq_api.api import concurrency

    monkeypatch.setattr(concurrency, "BASE_CONCURRENCY", 1)
    first = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert first.status_code == 201  # this registered call fills the only slot
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 429
    # Consumers match /concurrency limit|429/i — keep the wording.
    assert "Concurrency limit" in resp.json()["detail"]


async def test_stale_registered_web_call_is_swept_and_frees_the_slot(client, monkeypatch):
    from sqlalchemy import update

    import arhiteq_api.db as db_module
    from arhiteq_api.api import concurrency
    from arhiteq_api.models import Call, now_ms

    monkeypatch.setattr(concurrency, "BASE_CONCURRENCY", 1)
    stale = (
        await client.post("/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID})
    ).json()
    # Backdate below the TTL cutoff: a dead worker never answered this call.
    async with db_module.session_factory()() as session:
        await session.execute(
            update(Call)
            .where(Call.call_id == stale["call_id"])
            .values(created_at_ms=now_ms() - concurrency.WEB_CALL_REGISTERED_TTL_MS - 1)
        )
        await session.commit()

    # Would 429 if the stale registered row still occupied the only slot.
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 201

    got = await client.get(f"/v2/get-call/{stale['call_id']}", headers=AUTH_HEADERS)
    assert got.status_code == 200
    body = got.json()
    assert body["call_status"] == "not_connected"
    assert body["disconnection_reason"] == "dial_no_answer"


async def test_create_web_call_uses_public_livekit_url_when_set(client, monkeypatch):
    from arhiteq_api.config import Settings

    monkeypatch.setattr(
        "arhiteq_api.api.calls.get_settings",
        lambda: Settings(public_livekit_url="wss://livekit.example.com"),
    )
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 201
    assert resp.json()["livekit_server_url"] == "wss://livekit.example.com"


# ── update-call ─────────────────────────────────────────────────────────────


async def test_update_call_updates_only_metadata_and_dynamic_variables(client):
    call = await _register_call(client, metadata={"a": "1"})
    resp = await client.patch(
        f"/v2/update-call/{call['call_id']}",
        headers=AUTH_HEADERS,
        json={
            "metadata": {"a": "2"},
            "retell_llm_dynamic_variables": {"first_name": "Grace"},
            "call_status": "ended",  # immutable — must be ignored
            "agent_id": "agent_hijack",  # immutable — must be ignored
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["metadata"] == {"a": "2"}
    assert body["retell_llm_dynamic_variables"] == {"first_name": "Grace"}
    assert body["call_status"] == "registered"
    assert body["agent_id"] == AGENT_ID


async def test_update_call_404_for_unknown_call(client):
    resp = await client.patch(
        "/v2/update-call/call_missing", headers=AUTH_HEADERS, json={"metadata": {}}
    )
    assert resp.status_code == 404


# ── delete-call ─────────────────────────────────────────────────────────────


async def test_delete_call_returns_204_and_removes_call(client):
    call = await _register_call(client)
    resp = await client.delete(f"/v2/delete-call/{call['call_id']}", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    got = await client.get(f"/v2/get-call/{call['call_id']}", headers=AUTH_HEADERS)
    assert got.status_code == 404


async def test_delete_call_scoped_to_workspace(client, other_workspace):
    call = await _register_call(client)
    resp = await client.delete(f"/v2/delete-call/{call['call_id']}", headers=OTHER_AUTH_HEADERS)
    assert resp.status_code == 404


# ── rerun-call-analysis ─────────────────────────────────────────────────────


async def test_rerun_call_analysis_returns_201_with_analysis(client):
    call = await _register_call(client)
    resp = await client.put(f"/rerun-call-analysis/{call['call_id']}", headers=AUTH_HEADERS)
    assert resp.status_code == 201
    body = resp.json()
    assert body["call_id"] == call["call_id"]
    analysis = body["call_analysis"]
    # summary and call_summary are always emitted in sync (contract hot spot).
    assert analysis["summary"] == analysis["call_summary"]
    assert analysis["user_sentiment"] in ("Positive", "Negative", "Neutral", "Unknown")
    assert isinstance(analysis["in_voicemail"], bool)


async def test_rerun_call_analysis_404_for_unknown_call(client):
    resp = await client.put("/rerun-call-analysis/call_missing", headers=AUTH_HEADERS)
    assert resp.status_code == 404
