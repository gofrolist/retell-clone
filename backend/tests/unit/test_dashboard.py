"""Dashboard-only endpoints: analytics, contacts, alerts, QA cohorts,
API-key management, webhook delivery log, workspace settings."""

from tests.conftest import AGENT_ID, AUTH_HEADERS, FROM_NUMBER, OTHER_AUTH_HEADERS


async def _place_call(client, to_number: str = "+15550001111") -> str:
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": to_number},
    )
    assert resp.status_code == 201
    return resp.json()["call_id"]


async def test_analytics_counts_real_calls(client):
    await _place_call(client)
    await _place_call(client)
    resp = await client.get("/analytics/calls", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["call_counts"] == 2
    assert data["phone_direction"] == [{"name": "outbound", "value": 2}]
    # Dense series: one point per day in the window, today included.
    assert len(data["call_counts_series"]) == 30
    assert sum(p["value"] for p in data["call_counts_series"]) == 2


async def test_analytics_is_workspace_scoped(client, other_workspace):
    await _place_call(client)
    resp = await client.get("/analytics/calls", headers=OTHER_AUTH_HEADERS)
    assert resp.json()["call_counts"] == 0


async def test_contact_crud_and_call_stats(client):
    await _place_call(client, to_number="+15550002222")
    created = await client.post(
        "/create-contact",
        headers=AUTH_HEADERS,
        json={"phone_number": "+15550002222", "first_name": "Ada", "last_name": "L"},
    )
    assert created.status_code == 201
    contact_id = created.json()["contact_id"]

    listed = (await client.get("/list-contacts", headers=AUTH_HEADERS)).json()
    assert len(listed) == 1
    assert listed[0]["related_conversations"] == 1
    assert listed[0]["latest_conversation"] is not None

    updated = await client.patch(
        f"/update-contact/{contact_id}", headers=AUTH_HEADERS, json={"do_not_call": True}
    )
    assert updated.json()["do_not_call"] is True

    assert (
        await client.delete(f"/delete-contact/{contact_id}", headers=AUTH_HEADERS)
    ).status_code == 204
    assert (await client.get("/list-contacts", headers=AUTH_HEADERS)).json() == []


async def test_agent_folder_crud_and_assignment(client):
    created = await client.post(
        "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "  Template Agents  "}
    )
    assert created.status_code == 201
    folder = created.json()
    assert folder["folder_name"] == "Template Agents"
    folder_id = folder["folder_id"]
    assert folder_id.startswith("folder_")

    listed = (await client.get("/list-agent-folders", headers=AUTH_HEADERS)).json()
    assert [f["folder_id"] for f in listed] == [folder_id]

    # Agents move into a folder through the normal update-agent PATCH. A move
    # is a dashboard regrouping, not a config change: the version must not
    # bump (call records stamp agent_version).
    before = (await client.get(f"/get-agent/{AGENT_ID}", headers=AUTH_HEADERS)).json()
    moved = await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"folder_id": folder_id}
    )
    assert moved.status_code == 200
    assert moved.json()["folder_id"] == folder_id
    assert moved.json()["version"] == before["version"]

    # A config field alongside folder_id still bumps.
    reconfigured = await client.patch(
        f"/update-agent/{AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"folder_id": folder_id, "agent_name": "Sales v2"},
    )
    assert reconfigured.json()["version"] == before["version"] + 1

    renamed = await client.patch(
        f"/update-agent-folder/{folder_id}", headers=AUTH_HEADERS, json={"folder_name": "Prod"}
    )
    assert renamed.json()["folder_name"] == "Prod"

    # Deleting the folder unassigns its agents but keeps them.
    assert (
        await client.delete(f"/delete-agent-folder/{folder_id}", headers=AUTH_HEADERS)
    ).status_code == 204
    assert (await client.get("/list-agent-folders", headers=AUTH_HEADERS)).json() == []
    agent = (await client.get(f"/get-agent/{AGENT_ID}", headers=AUTH_HEADERS)).json()
    assert agent["folder_id"] is None


async def test_agent_folder_rejects_blank_name(client):
    resp = await client.post(
        "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "  "}
    )
    assert resp.status_code == 422


async def test_agent_folder_rejects_duplicate_name(client):
    created = await client.post(
        "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "Prod"}
    )
    assert created.status_code == 201
    dupe = await client.post(
        "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "prod"}
    )
    assert dupe.status_code == 409

    other = await client.post(
        "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "Staging"}
    )
    rename_collision = await client.patch(
        f"/update-agent-folder/{other.json()['folder_id']}",
        headers=AUTH_HEADERS,
        json={"folder_name": "Prod"},
    )
    assert rename_collision.status_code == 409
    # Renaming a folder to its own name (case change) is allowed.
    self_rename = await client.patch(
        f"/update-agent-folder/{created.json()['folder_id']}",
        headers=AUTH_HEADERS,
        json={"folder_name": "PROD"},
    )
    assert self_rename.status_code == 200


async def test_update_agent_rejects_unknown_folder_id(client):
    resp = await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"folder_id": "folder_nope"}
    )
    assert resp.status_code == 422
    # Clearing the folder is always allowed.
    cleared = await client.patch(
        f"/update-agent/{AGENT_ID}", headers=AUTH_HEADERS, json={"folder_id": None}
    )
    assert cleared.status_code == 200
    assert cleared.json()["folder_id"] is None


async def test_update_agent_rejects_cross_workspace_folder_id(client, other_workspace):
    folder_id = (
        await client.post(
            "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "Mine"}
        )
    ).json()["folder_id"]
    # The other workspace has no agents seeded; create one to patch.
    other_agent = await client.post(
        "/create-agent",
        headers=OTHER_AUTH_HEADERS,
        json={"response_engine": {"type": "retell-llm"}, "voice_id": "cartesia-sonic"},
    )
    resp = await client.patch(
        f"/update-agent/{other_agent.json()['agent_id']}",
        headers=OTHER_AUTH_HEADERS,
        json={"folder_id": folder_id},
    )
    assert resp.status_code == 422


async def test_agent_folders_are_workspace_scoped(client, other_workspace):
    folder_id = (
        await client.post(
            "/create-agent-folder", headers=AUTH_HEADERS, json={"folder_name": "Mine"}
        )
    ).json()["folder_id"]

    assert (await client.get("/list-agent-folders", headers=OTHER_AUTH_HEADERS)).json() == []
    assert (
        await client.patch(
            f"/update-agent-folder/{folder_id}",
            headers=OTHER_AUTH_HEADERS,
            json={"folder_name": "Hijack"},
        )
    ).status_code == 404
    assert (
        await client.delete(f"/delete-agent-folder/{folder_id}", headers=OTHER_AUTH_HEADERS)
    ).status_code == 404


async def test_alert_crud(client):
    created = await client.post(
        "/create-alert",
        headers=AUTH_HEADERS,
        json={
            "name": "High failure rate",
            "metric": "call_success_rate",
            "condition": "below",
            "threshold": 0.8,
            "notify_emails": ["ops@example.com"],
        },
    )
    assert created.status_code == 201
    alert_id = created.json()["alert_id"]
    assert created.json()["enabled"] is True

    toggled = await client.patch(
        f"/update-alert/{alert_id}", headers=AUTH_HEADERS, json={"enabled": False}
    )
    assert toggled.json()["enabled"] is False

    listed = (await client.get("/list-alerts", headers=AUTH_HEADERS)).json()
    assert [a["alert_id"] for a in listed] == [alert_id]

    assert (
        await client.delete(f"/delete-alert/{alert_id}", headers=AUTH_HEADERS)
    ).status_code == 204


async def test_qa_cohort_crud(client):
    created = await client.post(
        "/create-qa-cohort",
        headers=AUTH_HEADERS,
        json={"name": "Sales QA", "agents": [AGENT_ID], "sampling_pct": 25},
    )
    assert created.status_code == 201
    cohort_id = created.json()["cohort_id"]
    assert created.json()["agents"] == [AGENT_ID]

    listed = (await client.get("/list-qa-cohorts", headers=AUTH_HEADERS)).json()
    assert len(listed) == 1

    assert (
        await client.delete(f"/delete-qa-cohort/{cohort_id}", headers=AUTH_HEADERS)
    ).status_code == 204


async def test_api_keys_masked_create_and_revoke(client):
    listed = (await client.get("/list-api-keys", headers=AUTH_HEADERS)).json()
    assert len(listed) == 1
    assert "secret" not in listed[0]
    assert listed[0]["prefix"].endswith("…")

    created = await client.post("/create-api-key", headers=AUTH_HEADERS, json={"name": "ci"})
    secret = created.json()["secret"]
    assert secret.startswith("key_")

    # The new key is a working credential...
    ok = await client.get("/list-agents", headers={"Authorization": f"Bearer {secret}"})
    assert ok.status_code == 200

    # ...until revoked.
    revoked = await client.post(f"/revoke-api-key/{created.json()['key_id']}", headers=AUTH_HEADERS)
    assert revoked.json()["revoked"] is True
    denied = await client.get("/list-agents", headers={"Authorization": f"Bearer {secret}"})
    assert denied.status_code == 401


async def test_webhook_deliveries_empty_and_scoped(client):
    resp = await client.get("/list-webhook-deliveries", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


async def test_workspace_get_and_update(client):
    ws = (await client.get("/workspace", headers=AUTH_HEADERS)).json()
    assert ws["workspace_id"]

    updated = await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={"webhook_url": "https://consumer.example/webhook"},
    )
    assert updated.json()["webhook_url"] == "https://consumer.example/webhook"


async def test_dashboard_endpoints_require_auth(client):
    for path in (
        "/analytics/calls",
        "/list-contacts",
        "/list-alerts",
        "/list-qa-cohorts",
        "/list-api-keys",
        "/workspace",
    ):
        assert (await client.get(path)).status_code == 401
