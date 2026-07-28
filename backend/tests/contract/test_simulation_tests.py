"""Simulation testing endpoints: test case CRUD, batch runs, generation.

Runs are stubbed at `simulation._run_one` wherever a verdict matters — the
engine itself is exercised in tests/unit/test_simulation.py.
"""

import asyncio

import pytest

import arhiteq_api.db as db_module
from arhiteq_api.services import simulation
from tests.conftest import (
    AGENT_ID,
    AUTH_HEADERS,
    COMPANION_AGENT_ID,
    LLM_ID,
    OTHER_AUTH_HEADERS,
)

ENGINE = {"type": "retell-llm", "llm_id": LLM_ID}


async def _create_case(client, **overrides):
    body = {
        "name": "Books a callback",
        "response_engine": ENGINE,
        "user_prompt": "You want a callback tomorrow morning.",
        "metrics": ["The agent calls schedule_callback"],
        **overrides,
    }
    res = await client.post("/create-test-case-definition", json=body, headers=AUTH_HEADERS)
    assert res.status_code == 201, res.text
    return res.json()


async def _drain_batches():
    """Wait for the background batch tasks spawned by create-batch-test."""
    while simulation._batch_tasks:
        await asyncio.gather(*list(simulation._batch_tasks), return_exceptions=True)


async def test_definition_crud_roundtrip(client):
    created = await _create_case(client)
    case_id = created["test_case_definition_id"]
    assert created["type"] == "simulation"
    assert created["response_engine"] == {"type": "retell-llm", "llm_id": LLM_ID}
    assert created["source"] == "manual"

    got = await client.get(f"/get-test-case-definition/{case_id}", headers=AUTH_HEADERS)
    assert got.status_code == 200
    assert got.json()["user_prompt"] == "You want a callback tomorrow morning."

    updated = await client.put(
        f"/update-test-case-definition/{case_id}",
        json={
            "name": "Books a callback (renamed)",
            "metrics": ["The agent calls schedule_callback", "The agent confirms the time"],
            "tool_mocks": [
                {
                    "tool_name": "schedule_callback",
                    "input_match_rule": {"type": "any"},
                    "output": '{"ok": true}',
                }
            ],
        },
        headers=AUTH_HEADERS,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "Books a callback (renamed)"
    assert len(body["metrics"]) == 2
    assert body["tool_mocks"][0]["tool_name"] == "schedule_callback"
    # An untouched field survives a partial update.
    assert body["user_prompt"] == "You want a callback tomorrow morning."

    listed = await client.get(
        "/v2/list-test-case-definitions",
        params={"type": "retell-llm", "llm_id": LLM_ID},
        headers=AUTH_HEADERS,
    )
    assert listed.status_code == 200
    assert [i["test_case_definition_id"] for i in listed.json()["items"]] == [case_id]
    assert listed.json()["has_more"] is False

    deleted = await client.delete(f"/delete-test-case-definition/{case_id}", headers=AUTH_HEADERS)
    assert deleted.status_code == 204
    gone = await client.get(f"/get-test-case-definition/{case_id}", headers=AUTH_HEADERS)
    assert gone.status_code == 404


async def test_list_pagination_is_keyset(client):
    ids = [
        (await _create_case(client, name=f"Case {i}"))["test_case_definition_id"] for i in range(3)
    ]
    first = await client.get(
        "/v2/list-test-case-definitions",
        params={"type": "retell-llm", "llm_id": LLM_ID, "limit": 2},
        headers=AUTH_HEADERS,
    )
    page = first.json()
    assert page["has_more"] is True
    assert len(page["items"]) == 2
    second = await client.get(
        "/v2/list-test-case-definitions",
        params={
            "type": "retell-llm",
            "llm_id": LLM_ID,
            "limit": 2,
            "pagination_key": page["pagination_key"],
        },
        headers=AUTH_HEADERS,
    )
    rest = second.json()
    assert rest["has_more"] is False
    seen = [i["test_case_definition_id"] for i in page["items"] + rest["items"]]
    assert sorted(seen) == sorted(ids)


@pytest.mark.parametrize(
    ("engine", "detail"),
    [
        ({"type": "custom-llm", "llm_id": "whatever"}, "Custom LLM"),
        ({"type": "retell-llm"}, "llm_id is required"),
        ({"type": "retell-llm", "llm_id": "llm_missing"}, "not found"),
        ({"type": "conversation-flow"}, "conversation_flow_id is required"),
    ],
)
async def test_rejects_unusable_response_engines(client, engine, detail):
    res = await client.post(
        "/create-test-case-definition",
        json={"name": "x", "response_engine": engine},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 422
    assert detail in res.json()["detail"]


async def test_list_requires_the_id_for_the_engine_type(client):
    res = await client.get(
        "/v2/list-test-case-definitions", params={"type": "retell-llm"}, headers=AUTH_HEADERS
    )
    assert res.status_code == 422
    assert "llm_id is required" in res.json()["detail"]


async def test_definitions_are_workspace_scoped(client, other_workspace):
    case_id = (await _create_case(client))["test_case_definition_id"]
    for method, path in (
        ("get", f"/get-test-case-definition/{case_id}"),
        ("delete", f"/delete-test-case-definition/{case_id}"),
    ):
        res = await getattr(client, method)(path, headers=OTHER_AUTH_HEADERS)
        assert res.status_code == 404
    # The other workspace can't borrow this workspace's LLM either.
    res = await client.post(
        "/create-test-case-definition",
        json={"name": "x", "response_engine": ENGINE},
        headers=OTHER_AUTH_HEADERS,
    )
    assert res.status_code == 422


async def test_batch_test_rolls_up_run_verdicts(client, monkeypatch):
    cases = [await _create_case(client, name=f"Case {i}") for i in range(3)]
    verdicts = iter(["pass", "fail", "error"])

    async def fake_run(_factory, _job_id):
        return next(verdicts)

    monkeypatch.setattr(simulation, "_run_one", fake_run)

    res = await client.post(
        "/create-batch-test",
        json={
            "test_case_definition_ids": [c["test_case_definition_id"] for c in cases],
            "response_engine": ENGINE,
        },
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 201, res.text
    batch = res.json()
    assert batch["total_count"] == 3
    assert batch["status"] == "in_progress"

    await _drain_batches()
    done = await client.get(
        f"/get-batch-test/{batch['test_case_batch_job_id']}", headers=AUTH_HEADERS
    )
    body = done.json()
    assert body["status"] == "complete"
    assert (body["pass_count"], body["fail_count"], body["error_count"]) == (1, 1, 1)

    runs = await client.get(
        f"/v2/list-test-runs/{batch['test_case_batch_job_id']}", headers=AUTH_HEADERS
    )
    items = runs.json()["items"]
    assert len(items) == 3
    # Each run froze the definition it was launched from.
    assert {i["test_case_definition_snapshot"]["name"] for i in items} == {
        "Case 0",
        "Case 1",
        "Case 2",
    }
    single = await client.get(f"/get-test-run/{items[0]['test_case_job_id']}", headers=AUTH_HEADERS)
    assert single.status_code == 200
    assert single.json()["test_case_job_id"] == items[0]["test_case_job_id"]


async def test_run_without_credentials_errors_with_a_reason(client):
    """No Gemini creds in the test env: the run must say so, not hang or pass."""
    case = await _create_case(client)
    res = await client.post(
        "/create-batch-test",
        json={
            "test_case_definition_ids": [case["test_case_definition_id"]],
            "response_engine": ENGINE,
        },
        headers=AUTH_HEADERS,
    )
    batch_id = res.json()["test_case_batch_job_id"]
    await _drain_batches()

    runs = await client.get(f"/v2/list-test-runs/{batch_id}", headers=AUTH_HEADERS)
    run = runs.json()["items"][0]
    assert run["status"] == "error"
    assert "credentials" in run["result_explanation"]
    batch = await client.get(f"/get-batch-test/{batch_id}", headers=AUTH_HEADERS)
    assert batch.json()["error_count"] == 1


async def test_batch_test_rejects_foreign_and_empty_inputs(client, other_workspace):
    case = await _create_case(client)
    empty = await client.post(
        "/create-batch-test",
        json={"test_case_definition_ids": [], "response_engine": ENGINE},
        headers=AUTH_HEADERS,
    )
    assert empty.status_code == 422
    foreign = await client.post(
        "/create-batch-test",
        json={
            "test_case_definition_ids": [case["test_case_definition_id"]],
            "response_engine": ENGINE,
        },
        headers=OTHER_AUTH_HEADERS,
    )
    assert foreign.status_code == 422


async def test_list_batch_tests_filters_by_engine(client, monkeypatch):
    async def fake_run(_factory, _job_id):
        return "pass"

    monkeypatch.setattr(simulation, "_run_one", fake_run)
    case = await _create_case(client)
    await client.post(
        "/create-batch-test",
        json={
            "test_case_definition_ids": [case["test_case_definition_id"]],
            "response_engine": ENGINE,
        },
        headers=AUTH_HEADERS,
    )
    await _drain_batches()
    mine = await client.get(
        "/v2/list-batch-tests",
        params={"type": "retell-llm", "llm_id": LLM_ID},
        headers=AUTH_HEADERS,
    )
    assert len(mine.json()["items"]) == 1
    other = await client.get(
        "/v2/list-batch-tests",
        params={"type": "retell-llm", "llm_id": "llm_someone_else"},
        headers=AUTH_HEADERS,
    )
    assert other.json()["items"] == []


async def test_batch_tests_are_capped_per_workspace(client, monkeypatch):
    """Background LLM work is invisible to request rate limiting, so the number
    of batches a workspace can have running at once is bounded."""
    case = await _create_case(client)
    started = asyncio.Event()

    async def hang(_factory, _job_id):
        await started.wait()
        return "pass"

    monkeypatch.setattr(simulation, "_run_one", hang)
    body = {
        "test_case_definition_ids": [case["test_case_definition_id"]],
        "response_engine": ENGINE,
    }
    for _ in range(3):
        assert (
            await client.post("/create-batch-test", json=body, headers=AUTH_HEADERS)
        ).status_code == 201
    refused = await client.post("/create-batch-test", json=body, headers=AUTH_HEADERS)
    assert refused.status_code == 429
    assert "already running" in refused.json()["detail"]

    started.set()
    await _drain_batches()
    # Once they finish, the workspace can start another.
    assert (
        await client.post("/create-batch-test", json=body, headers=AUTH_HEADERS)
    ).status_code == 201
    started.set()
    await _drain_batches()


async def test_list_batch_tests_can_scope_to_one_agent(client, monkeypatch):
    """Several agents can share an LLM; each tab must show only its own runs."""

    async def fake_run(_factory, _job_id):
        return "pass"

    monkeypatch.setattr(simulation, "_run_one", fake_run)
    case = await _create_case(client)
    body = {
        "test_case_definition_ids": [case["test_case_definition_id"]],
        "response_engine": ENGINE,
    }
    mine = await client.post(
        "/create-batch-test", json={**body, "agent_id": AGENT_ID}, headers=AUTH_HEADERS
    )
    await client.post(
        "/create-batch-test",
        json={**body, "agent_id": COMPANION_AGENT_ID},
        headers=AUTH_HEADERS,
    )
    await _drain_batches()

    scoped = await client.get(
        "/v2/list-batch-tests",
        params={"type": "retell-llm", "llm_id": LLM_ID, "agent_id": AGENT_ID},
        headers=AUTH_HEADERS,
    )
    items = scoped.json()["items"]
    assert [i["test_case_batch_job_id"] for i in items] == [mine.json()["test_case_batch_job_id"]]
    # Unfiltered still returns both, as Retell's engine-only filter does.
    both = await client.get(
        "/v2/list-batch-tests",
        params={"type": "retell-llm", "llm_id": LLM_ID},
        headers=AUTH_HEADERS,
    )
    assert len(both.json()["items"]) == 2


async def test_a_case_without_criteria_errors_instead_of_passing(client):
    """Grading nothing is not a pass — it must not show a green badge."""
    case = await _create_case(client, metrics=[])
    res = await client.post(
        "/create-batch-test",
        json={
            "test_case_definition_ids": [case["test_case_definition_id"]],
            "response_engine": ENGINE,
        },
        headers=AUTH_HEADERS,
    )
    batch_id = res.json()["test_case_batch_job_id"]
    await _drain_batches()
    run = (await client.get(f"/v2/list-test-runs/{batch_id}", headers=AUTH_HEADERS)).json()[
        "items"
    ][0]
    assert run["status"] == "error"
    assert "no success criteria" in run["result_explanation"]


async def test_abandoned_batches_are_closed_out_on_shutdown(client, monkeypatch):
    """A restart must not leave a batch `in_progress` for the dashboard to poll
    forever."""
    case = await _create_case(client)
    forever = asyncio.Event()

    async def hang(_factory, _job_id):
        await forever.wait()
        return "pass"

    monkeypatch.setattr(simulation, "_run_one", hang)
    res = await client.post(
        "/create-batch-test",
        json={
            "test_case_definition_ids": [case["test_case_definition_id"]],
            "response_engine": ENGINE,
        },
        headers=AUTH_HEADERS,
    )
    batch_id = res.json()["test_case_batch_job_id"]

    await simulation.shutdown(db_module.session_factory())

    batch = (await client.get(f"/get-batch-test/{batch_id}", headers=AUTH_HEADERS)).json()
    assert batch["status"] == "complete"
    assert batch["error_count"] == 1
    run = (await client.get(f"/v2/list-test-runs/{batch_id}", headers=AUTH_HEADERS)).json()[
        "items"
    ][0]
    assert run["status"] == "error"
    assert "restarted" in run["result_explanation"]


async def test_generate_saves_drafted_cases(client, monkeypatch):
    async def fake_generate(llm, count):
        assert llm.llm_id == LLM_ID
        return [
            {
                "name": f"Generated {i}",
                "user_prompt": "You are a caller who wants a callback.",
                "metrics": ["The agent schedules the callback"],
                "dynamic_variables": {"first_name": f"Caller {i}"},
                "tool_mocks": [],
            }
            for i in range(count)
        ]

    monkeypatch.setattr(simulation, "generate_test_cases", fake_generate)
    res = await client.post(
        "/generate-test-case-definitions",
        json={"agent_id": "agent_sales0000000000000000000001", "count": 2},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["saved"] is True
    assert [i["source"] for i in body["items"]] == ["generated", "generated"]
    # The variables a draft depends on are persisted with it: a saved case that
    # dropped them would run with its branch gated shut.
    assert [i["dynamic_variables"] for i in body["items"]] == [
        {"first_name": "Caller 0"},
        {"first_name": "Caller 1"},
    ]

    listed = await client.get(
        "/v2/list-test-case-definitions",
        params={"type": "retell-llm", "llm_id": LLM_ID},
        headers=AUTH_HEADERS,
    )
    assert len(listed.json()["items"]) == 2


async def test_generate_preview_does_not_persist(client, monkeypatch):
    async def fake_generate(_llm, count):
        return [{"name": "Draft", "user_prompt": "…", "metrics": [], "tool_mocks": []}] * count

    monkeypatch.setattr(simulation, "generate_test_cases", fake_generate)
    res = await client.post(
        "/generate-test-case-definitions",
        json={"llm_id": LLM_ID, "count": 1, "save": False},
        headers=AUTH_HEADERS,
    )
    assert res.json()["saved"] is False
    listed = await client.get(
        "/v2/list-test-case-definitions",
        params={"type": "retell-llm", "llm_id": LLM_ID},
        headers=AUTH_HEADERS,
    )
    assert listed.json()["items"] == []


async def test_generate_surfaces_model_failures(client, monkeypatch):
    async def boom(_llm, _count):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(simulation, "generate_test_cases", boom)
    res = await client.post(
        "/generate-test-case-definitions", json={"llm_id": LLM_ID}, headers=AUTH_HEADERS
    )
    assert res.status_code == 502
    assert "model unavailable" in res.json()["detail"]


async def test_generate_requires_an_engine_and_a_prompt(client):
    missing = await client.post("/generate-test-case-definitions", json={}, headers=AUTH_HEADERS)
    assert missing.status_code == 422

    empty_llm = await client.post(
        "/create-retell-llm", json={"general_prompt": ""}, headers=AUTH_HEADERS
    )
    res = await client.post(
        "/generate-test-case-definitions",
        json={"llm_id": empty_llm.json()["llm_id"]},
        headers=AUTH_HEADERS,
    )
    assert res.status_code == 422
    assert "no prompt" in res.json()["detail"]


async def test_llm_model_can_be_pinned_and_cleared(client):
    """`null` clears the pin; omitting the field leaves it alone.

    Without the distinction there is no way back off a pinned model — the only
    escape would be deleting the case and writing it again.
    """
    case_id = (await _create_case(client, llm_model="gemini-2.5-flash"))["test_case_definition_id"]

    # An unrelated edit must not disturb the pin.
    res = await client.put(
        f"/update-test-case-definition/{case_id}",
        json={"name": "Renamed"},
        headers=AUTH_HEADERS,
    )
    assert res.json()["llm_model"] == "gemini-2.5-flash"

    res = await client.put(
        f"/update-test-case-definition/{case_id}",
        json={"llm_model": "gemini-3.5-flash"},
        headers=AUTH_HEADERS,
    )
    assert res.json()["llm_model"] == "gemini-3.5-flash"

    res = await client.put(
        f"/update-test-case-definition/{case_id}",
        json={"llm_model": None},
        headers=AUTH_HEADERS,
    )
    assert res.json()["llm_model"] is None


async def test_a_failed_config_load_ends_the_run_instead_of_stranding_it(client, monkeypatch):
    """A database error while reading the agent config must reach the guard.

    Loaded outside it, the raise escapes before anything writes a terminal
    status: the run sits at `in_progress` forever under a batch the dashboard
    reports as complete, and only the shutdown sweep ever clears it.
    """

    async def boom(*args, **kwargs):
        raise OSError("connection reset while reading the swap destinations")

    # The credential check sits ahead of the load and would short-circuit the
    # run in this environment before the failure under test could happen.
    monkeypatch.setattr(simulation, "genai_credentials_available", lambda _s: True)
    monkeypatch.setattr(simulation, "_load_swap_destinations", boom)

    case = await _create_case(client)
    res = await client.post(
        "/create-batch-test",
        json={
            "test_case_definition_ids": [case["test_case_definition_id"]],
            "response_engine": ENGINE,
        },
        headers=AUTH_HEADERS,
    )
    batch_id = res.json()["test_case_batch_job_id"]
    await _drain_batches()

    run = (await client.get(f"/v2/list-test-runs/{batch_id}", headers=AUTH_HEADERS)).json()[
        "items"
    ][0]
    assert run["status"] == "error"
    assert "connection reset" in run["result_explanation"]
