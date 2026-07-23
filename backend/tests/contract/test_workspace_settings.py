"""Workspace settings, system status, workspace webhook test, and deletion
(dashboard-only surface backing Settings → Limits / Reliability / Workspace)."""

import respx
from httpx import Response

from tests.conftest import AGENT_ID, AUTH_HEADERS, FROM_NUMBER, WORKSPACE_ID

WEBHOOK_URL = "https://consumer.example/webhooks/arhiteq"


# ── settings ────────────────────────────────────────────────────────────────


async def test_workspace_settings_defaults(client):
    resp = await client.get("/workspace", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert settings["purchased_concurrency"] == 0
    assert settings["reserved_inbound_concurrency"] == 0
    assert settings["concurrency_burst_enabled"] is False
    assert settings["llm_token_limit"] == 32768
    assert settings["cps_limits"] == {"telnyx": 1, "twilio": 1, "custom_telephony": 1}


async def test_patch_settings_merges_and_persists(client):
    resp = await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={"settings": {"purchased_concurrency": 10, "concurrency_burst_enabled": True}},
    )
    assert resp.status_code == 200
    resp = await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={"settings": {"cps_limits": {"twilio": 5}}},
    )
    settings = resp.json()["settings"]
    # Earlier writes survive later partial patches.
    assert settings["purchased_concurrency"] == 10
    assert settings["concurrency_burst_enabled"] is True
    assert settings["cps_limits"]["twilio"] == 5
    assert settings["cps_limits"]["telnyx"] == 1


async def test_patch_settings_rejects_bad_values(client):
    for patch in (
        {"purchased_concurrency": 101},
        {"purchased_concurrency": "10"},
        {"llm_token_limit": 512},
        {"cps_limits": {"nope": 1}},
        {"unknown_setting": True},
        {"reserved_inbound_concurrency": 999},
    ):
        resp = await client.patch("/workspace", headers=AUTH_HEADERS, json={"settings": patch})
        assert resp.status_code == 422, patch


async def test_billing_email_validated_and_normalized(client):
    resp = await client.patch(
        "/workspace", headers=AUTH_HEADERS, json={"settings": {"billing_email": "not-an-email"}}
    )
    assert resp.status_code == 422
    resp = await client.patch(
        "/workspace", headers=AUTH_HEADERS, json={"settings": {"billing_email": "Ops@Example.COM"}}
    )
    assert resp.json()["settings"]["billing_email"] == "ops@example.com"


# ── concurrency wiring ──────────────────────────────────────────────────────


async def test_get_concurrency_reflects_settings(client):
    await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={
            "settings": {
                "purchased_concurrency": 5,
                "reserved_inbound_concurrency": 3,
                "concurrency_burst_enabled": True,
            }
        },
    )
    resp = await client.get("/get-concurrency", headers=AUTH_HEADERS)
    body = resp.json()
    assert body["concurrency_limit"] == 25
    assert body["purchased_concurrency"] == 5
    assert body["reserved_inbound_concurrency"] == 3
    assert body["concurrency_burst_enabled"] is True
    assert body["concurrency_burst_limit"] == 75  # min(3x, +300)


async def test_reserved_inbound_capacity_blocks_outbound_web_calls(client, monkeypatch):
    from arhiteq_api.api import concurrency

    monkeypatch.setattr(concurrency, "BASE_CONCURRENCY", 1)
    await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={"settings": {"reserved_inbound_concurrency": 1}},
    )
    # The single slot is reserved for inbound: outbound-ish web calls get 429.
    resp = await client.post(
        "/v2/create-web-call", headers=AUTH_HEADERS, json={"agent_id": AGENT_ID}
    )
    assert resp.status_code == 429
    assert "Concurrency limit" in resp.json()["detail"]


# ── system status ───────────────────────────────────────────────────────────


async def test_system_status_reports_components(client):
    resp = await client.get("/system-status", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    keys = {c["key"] for c in body["components"]}
    assert {"api", "database", "livekit", "telephony", "llm", "webhooks"} <= keys
    by_key = {c["key"]: c for c in body["components"]}
    assert by_key["database"]["status"] == "operational"
    # No Gemini creds in tests.
    assert by_key["llm"]["status"] == "not_configured"


# ── workspace webhook test button ───────────────────────────────────────────


@respx.mock
async def test_workspace_webhook_test_sends_signed_event(client):
    route = respx.post(WEBHOOK_URL).mock(return_value=Response(200))
    resp = await client.post(
        "/test-workspace-webhook",
        headers=AUTH_HEADERS,
        json={"webhook_url": WEBHOOK_URL, "event": "call_ended"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    request = route.calls[0].request
    assert request.headers["x-retell-signature"].startswith("v=")
    assert b'"event":"call_ended"' in request.content


async def test_workspace_webhook_test_requires_a_url(client):
    resp = await client.post("/test-workspace-webhook", headers=AUTH_HEADERS, json={})
    assert resp.status_code == 422


# ── delete workspace ────────────────────────────────────────────────────────


async def test_delete_workspace_removes_everything(client):
    resp = await client.delete("/workspace", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    # The API key died with the workspace, so the next call is unauthorized.
    resp = await client.get("/workspace", headers=AUTH_HEADERS)
    assert resp.status_code == 401


async def test_reserving_the_full_limit_is_rejected(client):
    # Allowing reserved == limit would silently zero the outbound budget.
    resp = await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={"settings": {"reserved_inbound_concurrency": 20}},
    )
    assert resp.status_code == 422


async def test_live_inbound_calls_do_not_starve_outbound(client, monkeypatch):
    from arhiteq_api.api import concurrency
    import arhiteq_api.db as db_module
    from arhiteq_api.models import Call

    monkeypatch.setattr(concurrency, "BASE_CONCURRENCY", 2)
    await client.patch(
        "/workspace",
        headers=AUTH_HEADERS,
        json={"settings": {"reserved_inbound_concurrency": 1}},
    )
    # Two live inbound calls: over their 1-slot reservation.
    async with db_module.session_factory()() as session:
        for i in range(2):
            session.add(
                Call(
                    call_id=f"call_inbound_live_{i:016d}",
                    workspace_id=WORKSPACE_ID,
                    agent_id=AGENT_ID,
                    direction="inbound",
                    call_status="ongoing",
                )
            )
        await session.commit()

    # Outbound budget is limit(2) - reserved(1) = 1 and no outbound call is
    # live, so dialing must succeed even though total live (2) exceeds it.
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": "+12137774445"},
    )
    assert resp.status_code == 201
    # The single outbound slot is now taken: the next outbound call 429s.
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": "+12137774446"},
    )
    assert resp.status_code == 429
