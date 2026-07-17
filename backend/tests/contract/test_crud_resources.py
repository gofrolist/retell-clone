"""CRUD coverage for retell-llm, phone-number, and agent resources,
including cross-workspace isolation."""

from tests.conftest import AGENT_ID, AUTH_HEADERS, LLM_ID, OTHER_AUTH_HEADERS


class TestRetellLLM:
    async def test_create_get_update_delete_roundtrip(self, client):
        created = await client.post(
            "/create-retell-llm",
            headers=AUTH_HEADERS,
            json={
                "model": "gemini-2.5-flash",
                "general_prompt": "You are {{name}}.",
                "begin_message": "Hi!",
                "general_tools": [{"type": "end_call", "name": "end_call"}],
            },
        )
        assert created.status_code == 201
        body = created.json()
        llm_id = body["llm_id"]
        assert llm_id.startswith("llm_")
        assert body["general_prompt"] == "You are {{name}}."

        got = await client.get(f"/get-retell-llm/{llm_id}", headers=AUTH_HEADERS)
        assert got.status_code == 200
        assert got.json()["begin_message"] == "Hi!"

        listed = await client.get("/list-retell-llms", headers=AUTH_HEADERS)
        assert any(x["llm_id"] == llm_id for x in listed.json())

        updated = await client.patch(
            f"/update-retell-llm/{llm_id}",
            headers=AUTH_HEADERS,
            json={"model_temperature": 0.7, "llm_id": "ignored", "bogus": 1},
        )
        assert updated.status_code == 200
        assert updated.json()["model_temperature"] == 0.7
        assert updated.json()["llm_id"] == llm_id  # immutable fields ignored
        assert updated.json()["version"] == 1

        deleted = await client.delete(f"/delete-retell-llm/{llm_id}", headers=AUTH_HEADERS)
        assert deleted.status_code == 204
        assert (
            await client.get(f"/get-retell-llm/{llm_id}", headers=AUTH_HEADERS)
        ).status_code == 404

    async def test_other_workspace_cannot_see_llm(self, client, other_workspace):
        resp = await client.get(f"/get-retell-llm/{LLM_ID}", headers=OTHER_AUTH_HEADERS)
        assert resp.status_code == 404
        resp = await client.patch(
            f"/update-retell-llm/{LLM_ID}", headers=OTHER_AUTH_HEADERS, json={"model": "x"}
        )
        assert resp.status_code == 404
        resp = await client.delete(f"/delete-retell-llm/{LLM_ID}", headers=OTHER_AUTH_HEADERS)
        assert resp.status_code == 404


class TestPhoneNumbers:
    async def test_import_get_update_delete_roundtrip(self, client):
        created = await client.post(
            "/import-phone-number",
            headers=AUTH_HEADERS,
            json={
                "phone_number": "+14155550123",
                "nickname": "Imported",
                "inbound_agent_id": AGENT_ID,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["phone_number"] == "+14155550123"
        assert body["phone_number_pretty"] == "+1(415)555-0123"
        assert body["inbound_agent_id"] == AGENT_ID

        got = await client.get("/get-phone-number/+14155550123", headers=AUTH_HEADERS)
        assert got.status_code == 200

        updated = await client.patch(
            "/update-phone-number/+14155550123",
            headers=AUTH_HEADERS,
            json={
                "nickname": "Renamed",
                "inbound_webhook_url": "https://consumer.example/router",
                "phone_number": "+19999999999",  # immutable, ignored
            },
        )
        assert updated.status_code == 200
        assert updated.json()["nickname"] == "Renamed"
        assert updated.json()["inbound_webhook_url"] == "https://consumer.example/router"
        assert updated.json()["phone_number"] == "+14155550123"

        listed = await client.get("/list-phone-numbers", headers=AUTH_HEADERS)
        assert any(p["phone_number"] == "+14155550123" for p in listed.json())

        deleted = await client.delete("/delete-phone-number/+14155550123", headers=AUTH_HEADERS)
        assert deleted.status_code == 204

    async def test_duplicate_number_conflicts(self, client):
        body = {"phone_number": "+14155550999"}
        first = await client.post("/import-phone-number", headers=AUTH_HEADERS, json=body)
        assert first.status_code == 201
        second = await client.post("/import-phone-number", headers=AUTH_HEADERS, json=body)
        assert second.status_code == 409

    async def test_create_requires_number_without_purchase_flow(self, client):
        resp = await client.post(
            "/create-phone-number", headers=AUTH_HEADERS, json={"area_code": 415}
        )
        assert resp.status_code == 422

    async def test_other_workspace_isolation(self, client, other_workspace):
        from tests.conftest import FROM_NUMBER

        resp = await client.get(f"/get-phone-number/{FROM_NUMBER}", headers=OTHER_AUTH_HEADERS)
        assert resp.status_code == 404


class TestAgents:
    async def test_create_update_delete_roundtrip(self, client):
        created = await client.post(
            "/create-agent",
            headers=AUTH_HEADERS,
            json={
                "response_engine": {"type": "retell-llm", "llm_id": LLM_ID},
                "voice_id": "cartesia-sonic",
                "agent_name": "Crud Agent",
                "interruption_sensitivity": 0.5,
            },
        )
        assert created.status_code == 201
        agent_id = created.json()["agent_id"]
        assert created.json()["interruption_sensitivity"] == 0.5

        updated = await client.patch(
            f"/update-agent/{agent_id}",
            headers=AUTH_HEADERS,
            json={"agent_name": "Renamed", "agent_id": "ignored"},
        )
        assert updated.status_code == 200
        assert updated.json()["agent_name"] == "Renamed"
        assert updated.json()["agent_id"] == agent_id
        assert updated.json()["version"] == 1

        versions = await client.get(f"/get-agent-versions/{agent_id}", headers=AUTH_HEADERS)
        assert versions.status_code == 200
        assert versions.json()[0]["agent_id"] == agent_id

        deleted = await client.delete(f"/delete-agent/{agent_id}", headers=AUTH_HEADERS)
        assert deleted.status_code == 204
        assert (await client.get(f"/get-agent/{agent_id}", headers=AUTH_HEADERS)).status_code == 404

    async def test_agent_id_preserved_on_import_and_conflicts(self, client):
        body = {
            "response_engine": {"type": "retell-llm", "llm_id": LLM_ID},
            "voice_id": "cartesia-sonic",
            "agent_id": "agent_imported_from_retell_000001",
        }
        first = await client.post("/create-agent", headers=AUTH_HEADERS, json=body)
        assert first.status_code == 201
        assert first.json()["agent_id"] == "agent_imported_from_retell_000001"
        second = await client.post("/create-agent", headers=AUTH_HEADERS, json=body)
        assert second.status_code == 409

    async def test_other_workspace_cannot_touch_agent(self, client, other_workspace):
        for method, url in (
            ("get", f"/get-agent/{AGENT_ID}"),
            ("delete", f"/delete-agent/{AGENT_ID}"),
            ("post", f"/publish-agent/{AGENT_ID}"),
        ):
            resp = await getattr(client, method)(url, headers=OTHER_AUTH_HEADERS)
            assert resp.status_code == 404, url

    async def test_delete_agent_bound_to_phone_number_conflicts(self, client):
        created = await client.post(
            "/create-agent",
            headers=AUTH_HEADERS,
            json={
                "response_engine": {"type": "retell-llm", "llm_id": LLM_ID},
                "voice_id": "cartesia-sonic",
            },
        )
        agent_id = created.json()["agent_id"]
        number = "+14155550777"
        imported = await client.post(
            "/import-phone-number",
            headers=AUTH_HEADERS,
            json={"phone_number": number, "outbound_agent_id": agent_id},
        )
        assert imported.status_code == 201

        # A bound DID must yield a clean 409 that names the number, not a 500.
        blocked = await client.delete(f"/delete-agent/{agent_id}", headers=AUTH_HEADERS)
        assert blocked.status_code == 409
        assert number in blocked.json()["detail"]
        assert (await client.get(f"/get-agent/{agent_id}", headers=AUTH_HEADERS)).status_code == 200

        # Release the binding and the delete goes through.
        await client.patch(
            f"/update-phone-number/{number}",
            headers=AUTH_HEADERS,
            json={"outbound_agent_id": None},
        )
        deleted = await client.delete(f"/delete-agent/{agent_id}", headers=AUTH_HEADERS)
        assert deleted.status_code == 204
