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


async def test_list_calls_pagination_does_not_skip_same_millisecond_rows(client):
    # A batch inserts many calls in the same millisecond; keyset pagination
    # anchored only on created_at_ms would skip the anchor's siblings.
    await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "tasks": [{"to_number": f"+1213777{i:04d}"} for i in range(5)],
        },
    )
    seen: list[str] = []
    page_key = None
    for _ in range(10):  # safety bound against an infinite loop
        payload: dict = {"limit": 2}
        if page_key:
            payload["pagination_key"] = page_key
        rows = (await client.post("/v2/list-calls", headers=AUTH_HEADERS, json=payload)).json()
        if not rows:
            break
        seen.extend(c["call_id"] for c in rows)
        page_key = rows[-1]["call_id"]
        if len(rows) < 2:
            break
    assert len(seen) == len(set(seen)) == 5


async def test_scheduled_batch_call_is_stored_without_dialing(client, monkeypatch):
    async def _boom(call):
        raise AssertionError("scheduled batch must not dial immediately")

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _boom)
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

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _flaky)
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


async def test_batch_first_wave_respects_concurrency_budget(client, monkeypatch):
    import asyncio

    from arhiteq_api.api import batch_calls, concurrency

    monkeypatch.setattr(concurrency, "BASE_CONCURRENCY", 2)
    dialed: list[str] = []

    async def _record(call):
        dialed.append(call.to_number)

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _record)
    resp = await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "tasks": [{"to_number": f"+1213777{i:04d}"} for i in range(5)],
        },
    )
    assert resp.status_code == 201
    # Only the outbound budget (2 slots) dials immediately; the rest is handed
    # to the background drainer instead of blowing past the workspace limit.
    assert len(dialed) == 2
    assert len(batch_calls._drain_tasks) == 1
    # Don't leak the drainer (and its monkeypatched telephony) into other tests.
    for task in list(batch_calls._drain_tasks):
        task.cancel()
    await asyncio.gather(*list(batch_calls._drain_tasks), return_exceptions=True)


async def test_batch_reserved_concurrency_holds_back_slots(client, monkeypatch):
    import asyncio

    from arhiteq_api.api import batch_calls, concurrency

    monkeypatch.setattr(concurrency, "BASE_CONCURRENCY", 3)
    dialed: list[str] = []

    async def _record(call):
        dialed.append(call.to_number)

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _record)
    resp = await client.post(
        "/create-batch-call",
        headers=AUTH_HEADERS,
        json={
            "from_number": FROM_NUMBER,
            "reserved_concurrency": 2,
            "tasks": [{"to_number": f"+1213777{i:04d}"} for i in range(3)],
        },
    )
    assert resp.status_code == 201
    # limit(3) - reserved for non-batch calls(2) = 1 immediate dial.
    assert len(dialed) == 1
    for task in list(batch_calls._drain_tasks):
        task.cancel()
    await asyncio.gather(*list(batch_calls._drain_tasks), return_exceptions=True)
