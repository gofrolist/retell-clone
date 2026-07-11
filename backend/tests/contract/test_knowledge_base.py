"""Knowledge base CRUD (storage only; retrieval/embedding out of scope)."""

import json

from tests.conftest import AUTH_HEADERS, OTHER_AUTH_HEADERS


async def _create_kb(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        json={
            "knowledge_base_name": "Sample KB",
            "knowledge_base_texts": [{"title": "Intro", "text": "Hello world."}],
            "knowledge_base_urls": ["https://www.example.com"],
        },
    )
    assert resp.status_code == 201
    return resp.json()


async def test_create_knowledge_base_json(client):
    body = await _create_kb(client)
    assert body["knowledge_base_id"].startswith("know_")
    assert body["knowledge_base_name"] == "Sample KB"
    assert body["status"] == "complete"
    sources = body["knowledge_base_sources"]
    assert len(sources) == 2
    types = {s["type"] for s in sources}
    assert types == {"text", "url"}
    for source in sources:
        assert source["source_id"].startswith("src_")
    url_source = next(s for s in sources if s["type"] == "url")
    assert url_source["url"] == "https://www.example.com"
    text_source = next(s for s in sources if s["type"] == "text")
    assert text_source["title"] == "Intro"


async def test_create_knowledge_base_multipart(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={
            "knowledge_base_name": "Multipart KB",
            "knowledge_base_texts": json.dumps({"title": "T", "text": "Body"}),
            "knowledge_base_urls": "https://www.example.com/a",
        },
        files={"knowledge_base_files": ("guide.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["knowledge_base_name"] == "Multipart KB"
    types = sorted(s["type"] for s in body["knowledge_base_sources"])
    assert types == ["document", "text", "url"]


async def test_create_knowledge_base_requires_name(client):
    resp = await client.post("/create-knowledge-base", headers=AUTH_HEADERS, json={})
    assert resp.status_code == 422


async def test_get_and_list_knowledge_bases(client):
    kb = await _create_kb(client)
    got = await client.get(f"/get-knowledge-base/{kb['knowledge_base_id']}", headers=AUTH_HEADERS)
    assert got.status_code == 200
    assert got.json()["knowledge_base_id"] == kb["knowledge_base_id"]

    listed = await client.get("/list-knowledge-bases", headers=AUTH_HEADERS)
    assert listed.status_code == 200
    assert [k["knowledge_base_id"] for k in listed.json()] == [kb["knowledge_base_id"]]


async def test_get_knowledge_base_scoped_to_workspace(client, other_workspace):
    kb = await _create_kb(client)
    resp = await client.get(
        f"/get-knowledge-base/{kb['knowledge_base_id']}", headers=OTHER_AUTH_HEADERS
    )
    assert resp.status_code == 404


async def test_add_and_delete_knowledge_base_sources(client):
    kb = await _create_kb(client)
    kb_id = kb["knowledge_base_id"]

    added = await client.post(
        f"/add-knowledge-base-sources/{kb_id}",
        headers=AUTH_HEADERS,
        json={"knowledge_base_urls": ["https://www.example.com/faq"]},
    )
    assert added.status_code == 201
    sources = added.json()["knowledge_base_sources"]
    assert len(sources) == 3

    victim = sources[0]["source_id"]
    deleted = await client.delete(
        f"/delete-knowledge-base-source/{kb_id}/source/{victim}", headers=AUTH_HEADERS
    )
    assert deleted.status_code == 204
    remaining = (await client.get(f"/get-knowledge-base/{kb_id}", headers=AUTH_HEADERS)).json()[
        "knowledge_base_sources"
    ]
    assert victim not in {s["source_id"] for s in remaining}
    assert len(remaining) == 2

    missing = await client.delete(
        f"/delete-knowledge-base-source/{kb_id}/source/src_missing", headers=AUTH_HEADERS
    )
    assert missing.status_code == 404


async def test_delete_knowledge_base(client):
    kb = await _create_kb(client)
    resp = await client.delete(
        f"/delete-knowledge-base/{kb['knowledge_base_id']}", headers=AUTH_HEADERS
    )
    assert resp.status_code == 204
    got = await client.get(f"/get-knowledge-base/{kb['knowledge_base_id']}", headers=AUTH_HEADERS)
    assert got.status_code == 404


async def test_knowledge_base_requires_auth(client):
    resp = await client.post("/create-knowledge-base", json={"knowledge_base_name": "KB"})
    assert resp.status_code == 401
