"""Surface 1 — POST /v2/create-phone-call (spec §2)."""

from tests.conftest import AGENT_ID, AUTH_HEADERS, FROM_NUMBER


async def test_returns_call_id_and_applies_override_agent(client):
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "to_number": "+18155141544",
            "override_agent_id": AGENT_ID,
            "retell_llm_dynamic_variables": {
                "phone": "+18155141544",
                "first_name": "John",
                "time_of_day": "morning",
            },
            "metadata": {"lead_id": "42", "is_test_call": True},
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["call_id"].startswith("call_")
    # override_agent_id must be applied, not the number's default agent
    assert body["agent_id"] == AGENT_ID
    assert body["direction"] == "outbound"
    assert body["call_status"] == "registered"
    assert body["call_type"] == "phone_call"
    # dynamic variables stored verbatim — no renames, no drops
    assert body["retell_llm_dynamic_variables"] == {
        "phone": "+18155141544",
        "first_name": "John",
        "time_of_day": "morning",
    }
    assert body["metadata"] == {"lead_id": "42", "is_test_call": True}


async def test_defaults_to_number_outbound_agent(client):
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": "+18155141544"},
    )
    assert resp.status_code == 201
    from tests.conftest import COMPANION_AGENT_ID

    assert resp.json()["agent_id"] == COMPANION_AGENT_ID


async def test_call_id_stable_between_create_and_get(client):
    created = (
        await client.post(
            "/v2/create-phone-call",
            headers=AUTH_HEADERS,
            json={"from_number": FROM_NUMBER, "to_number": "+18155141544"},
        )
    ).json()
    got = await client.get(f"/v2/get-call/{created['call_id']}", headers=AUTH_HEADERS)
    assert got.status_code == 200
    assert got.json()["call_id"] == created["call_id"]


async def test_unknown_from_number_is_non_2xx(client):
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": "+10000000000", "to_number": "+18155141544"},
    )
    assert resp.status_code >= 400  # consumer marks lead retell_error on non-2xx


async def test_extra_unknown_fields_are_tolerated(client):
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "to_number": "+18155141544",
            "some_future_field": {"nested": True},
        },
    )
    assert resp.status_code == 201


async def test_requires_bearer_auth(client):
    resp = await client.post(
        "/v2/create-phone-call",
        json={"from_number": FROM_NUMBER, "to_number": "+18155141544"},
    )
    assert resp.status_code == 401
