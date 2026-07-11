"""POST /create-batch-call."""

from tests.conftest import AUTH_HEADERS, COMPANION_AGENT_ID, FROM_NUMBER


async def test_create_batch_call_dials_each_task(client):
    resp = await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "name": "First batch call",
            "tasks": [
                {
                    "to_number": "+12137774445",
                    "retell_llm_dynamic_variables": {"first_name": "Ann"},
                },
                {"to_number": "+12137774446"},
            ],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["batch_call_id"].startswith("bc_")

    calls = (await client.post("/v2/list-calls", headers=AUTH_HEADERS, json={})).json()
    assert len(calls) == 2
    by_to = {c["to_number"]: c for c in calls}
    assert set(by_to) == {"+12137774445", "+12137774446"}
    for call in calls:
        assert call["direction"] == "outbound"
        assert call["call_status"] == "registered"
        assert call["from_number"] == FROM_NUMBER
        assert call["agent_id"] == COMPANION_AGENT_ID
    assert by_to["+12137774445"]["retell_llm_dynamic_variables"] == {"first_name": "Ann"}


async def test_scheduled_batch_call_is_stored_without_dialing(client, monkeypatch):
    async def _boom(call):
        raise AssertionError("scheduled batch must not dial immediately")

    monkeypatch.setattr("architeq_api.services.telephony.start_outbound_call", _boom)
    resp = await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "trigger_timestamp": 99735718400000,
            "tasks": [{"to_number": "+12137774445"}],
        },
    )
    assert resp.status_code == 201
    calls = (await client.post("/v2/list-calls", headers=AUTH_HEADERS, json={})).json()
    assert [c["call_status"] for c in calls] == ["registered"]


async def test_batch_call_tolerates_per_task_telephony_failure(client, monkeypatch):
    async def _flaky(call):
        if call.to_number == "+12137774445":
            raise RuntimeError("trunk rejected")

    monkeypatch.setattr("architeq_api.services.telephony.start_outbound_call", _flaky)
    resp = await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "tasks": [{"to_number": "+12137774445"}, {"to_number": "+12137774446"}],
        },
    )
    assert resp.status_code == 201  # one bad dial must not sink the batch
    calls = (await client.post("/v2/list-calls", headers=AUTH_HEADERS, json={})).json()
    by_to = {c["to_number"]: c for c in calls}
    assert by_to["+12137774445"]["call_status"] == "error"
    assert by_to["+12137774446"]["call_status"] == "registered"


async def test_batch_call_unknown_from_number_is_non_2xx(client):
    resp = await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={"from_number": "+10000000000", "tasks": [{"to_number": "+12137774445"}]},
    )
    assert resp.status_code == 422


async def test_batch_call_requires_auth(client):
    resp = await client.post(
        "/create-batch-call",
        json={"from_number": FROM_NUMBER, "tasks": [{"to_number": "+12137774445"}]},
    )
    assert resp.status_code == 401
