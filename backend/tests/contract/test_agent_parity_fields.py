"""Retell parity fields round-trip: pii_config, DTMF, fallback voices, signed
URLs, IVR/call-screening options on agents; mcps on Retell LLMs; batch-call
reserved_concurrency/call_time_window; contact custom_fields."""

from tests.conftest import AGENT_ID, AUTH_HEADERS, FROM_NUMBER, LLM_ID

PII = {"mode": "post_call", "categories": ["person_name", "phone_number"]}
DTMF = {"digit_limit": 4, "termination_key": "#", "timeout_ms": 3000}
MCPS = [
    {
        "name": "crm",
        "url": "https://mcp.example.com/sse",
        "headers": {"Authorization": "Bearer tok"},
        "timeout_ms": 5000,
    }
]


async def test_agent_parity_fields_round_trip(client):
    resp = await client.patch(
        f"/update-agent/{AGENT_ID}",
        headers=AUTH_HEADERS,
        json={
            "pii_config": PII,
            "fallback_voice_ids": ["11labs-Cimo"],
            "allow_user_dtmf": False,
            "allow_dtmf_interruption": True,
            "user_dtmf_options": DTMF,
            "opt_in_signed_url": True,
            "ivr_option": {"action": {"type": "hangup"}},
            "call_screening_option": {"action": {"type": "hangup"}},
        },
    )
    assert resp.status_code == 200
    got = (await client.get(f"/get-agent/{AGENT_ID}", headers=AUTH_HEADERS)).json()
    assert got["pii_config"] == PII
    assert got["fallback_voice_ids"] == ["11labs-Cimo"]
    assert got["allow_user_dtmf"] is False
    assert got["allow_dtmf_interruption"] is True
    assert got["user_dtmf_options"] == DTMF
    assert got["opt_in_signed_url"] is True
    assert got["ivr_option"] == {"action": {"type": "hangup"}}
    assert got["call_screening_option"] == {"action": {"type": "hangup"}}


async def test_agent_parity_field_defaults(client):
    got = (await client.get(f"/get-agent/{AGENT_ID}", headers=AUTH_HEADERS)).json()
    assert got["pii_config"] is None
    assert got["allow_user_dtmf"] is True
    assert got["allow_dtmf_interruption"] is False
    assert got["opt_in_signed_url"] is False


async def test_llm_mcps_round_trip(client):
    resp = await client.patch(
        f"/update-retell-llm/{LLM_ID}", headers=AUTH_HEADERS, json={"mcps": MCPS}
    )
    assert resp.status_code == 200
    got = (await client.get(f"/get-retell-llm/{LLM_ID}", headers=AUTH_HEADERS)).json()
    assert got["mcps"] == MCPS


async def test_batch_call_stores_concurrency_and_window(client, monkeypatch):
    async def _no_dial(call):
        return None

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _no_dial)
    window = {"start": "09:00", "end": "17:00", "days": ["mon", "tue"]}
    resp = await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "tasks": [{"to_number": "+12137774445"}],
            "reserved_concurrency": 3,
            "call_time_window": window,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["reserved_concurrency"] == 3
    assert body["call_time_window"] == window
    assert body["total_task_count"] == 1


async def test_batch_call_draft_lifecycle(client):
    created = await client.post(
        "/save-batch-call-draft",
        headers=AUTH_HEADERS,
        json={"name": "Wave 2", "from_number": FROM_NUMBER, "tasks": []},
    )
    assert created.status_code == 201
    draft_id = created.json()["batch_call_id"]

    listed = (await client.get("/list-batch-call-drafts", headers=AUTH_HEADERS)).json()
    assert [d["batch_call_id"] for d in listed] == [draft_id]

    assert (
        await client.delete(f"/delete-batch-call-draft/{draft_id}", headers=AUTH_HEADERS)
    ).status_code == 204
    assert (await client.get("/list-batch-call-drafts", headers=AUTH_HEADERS)).json() == []


async def test_contact_custom_fields_round_trip(client):
    await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={
            "settings": {
                "contact_field_definitions": [{"key": "plan", "label": "Plan", "type": "string"}]
            }
        },
    )
    created = await client.post(
        "/create-contact",
        headers=AUTH_HEADERS,
        json={"phone_number": "+15551230000", "custom_fields": {"plan": "gold"}},
    )
    assert created.status_code == 201
    contact_id = created.json()["contact_id"]
    assert created.json()["custom_fields"] == {"plan": "gold"}

    updated = await client.patch(
        f"/update-contact/{contact_id}",
        headers=AUTH_HEADERS,
        json={"custom_fields": {"plan": "silver"}},
    )
    assert updated.json()["custom_fields"] == {"plan": "silver"}


async def test_contact_field_definition_validation(client):
    for bad in (
        [{"key": "Bad Key", "label": "x", "type": "string"}],
        [{"key": "ok", "label": "", "type": "string"}],
        [{"key": "ok", "label": "x", "type": "money"}],
        [
            {"key": "dup", "label": "a", "type": "string"},
            {"key": "dup", "label": "b", "type": "string"},
        ],
    ):
        resp = await client.patch(
            "/workspace",
            headers=AUTH_HEADERS,
            json={"settings": {"contact_field_definitions": bad}},
        )
        assert resp.status_code == 422, bad
