"""Knowledge base CRUD (storage only; retrieval/embedding out of scope)."""

import json

from tests.conftest import AUTH_HEADERS, OTHER_AUTH_HEADERS
from arhiteq_api.api import knowledge_bases as kb_module


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


async def test_uploaded_file_roundtrip(client):
    payload = b"%PDF-1.4 round trip content"
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Files KB"},
        files={"knowledge_base_files": ("guide.pdf", payload, "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")
    assert doc["filename"] == "guide.pdf"
    assert doc["file_size"] == len(payload)
    assert doc["file_url"].endswith(
        f"/get-knowledge-base-file/{body['knowledge_base_id']}/source/{doc['source_id']}"
    )

    dl = await client.get(
        f"/get-knowledge-base-file/{body['knowledge_base_id']}/source/{doc['source_id']}",
        headers=AUTH_HEADERS,
    )
    assert dl.status_code == 200
    assert dl.content == payload
    assert dl.headers["content-type"].startswith("application/pdf")
    assert "attachment" in dl.headers["content-disposition"]
    assert "guide.pdf" in dl.headers["content-disposition"]


async def test_add_sources_uploads_file(client):
    kb = await _create_kb(client)
    payload = b"# notes\nadded later"
    resp = await client.post(
        f"/add-knowledge-base-sources/{kb['knowledge_base_id']}",
        headers=AUTH_HEADERS,
        files={"knowledge_base_files": ("notes.md", payload, "text/markdown")},
    )
    assert resp.status_code == 201
    doc = next(s for s in resp.json()["knowledge_base_sources"] if s["type"] == "document")
    assert doc["file_size"] == len(payload)

    dl = await client.get(
        f"/get-knowledge-base-file/{kb['knowledge_base_id']}/source/{doc['source_id']}",
        headers=AUTH_HEADERS,
    )
    assert dl.status_code == 200
    assert dl.content == payload


async def test_download_missing_file_404(client):
    kb = await _create_kb(client)
    resp = await client.get(
        f"/get-knowledge-base-file/{kb['knowledge_base_id']}/source/src_missing",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 404


async def test_upload_over_size_cap_413(client, monkeypatch):
    monkeypatch.setattr(kb_module, "MAX_FILE_BYTES", 10)
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Too big"},
        files={"knowledge_base_files": ("big.pdf", b"x" * 11, "application/pdf")},
    )
    assert resp.status_code == 413


async def test_mixed_batch_over_cap_413_closes_all(client, monkeypatch):
    monkeypatch.setattr(kb_module, "MAX_FILE_BYTES", 10)
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Mixed"},
        files=[
            ("knowledge_base_files", ("ok1.txt", b"tiny", "text/plain")),
            ("knowledge_base_files", ("big.txt", b"x" * 11, "text/plain")),
            ("knowledge_base_files", ("ok2.txt", b"tiny", "text/plain")),
        ],
    )
    assert resp.status_code == 413


async def test_texts_must_be_objects_json(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        json={"knowledge_base_name": "Bad texts", "knowledge_base_texts": ["hello"]},
    )
    assert resp.status_code == 422


async def test_texts_must_be_objects_multipart(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        # httpx only emits multipart/form-data when a `files=` part is present
        # (plain `data=` urlencodes, which would 500 on the JSON-parse
        # fallback). Filename=None keeps these as plain form fields rather
        # than UploadFiles, so there's nothing left unclosed when this 422s.
        files={
            "knowledge_base_name": (None, "Bad texts"),
            "knowledge_base_texts": (None, json.dumps("hello")),
        },
    )
    assert resp.status_code == 422


async def test_invalid_texts_with_files_closes_uploads(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Bad", "knowledge_base_texts": json.dumps("hello")},
        files={"knowledge_base_files": ("a.txt", b"tiny", "text/plain")},
    )
    assert resp.status_code == 422


async def test_upload_clamps_long_filename(client):
    long_name = "a" * 300 + ".pdf"
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Long name KB"},
        files={"knowledge_base_files": (long_name, b"x", "application/pdf")},
    )
    assert resp.status_code == 201
    doc = next(s for s in resp.json()["knowledge_base_sources"] if s["type"] == "document")
    assert len(doc["filename"]) == 255


async def test_download_scoped_to_workspace(client, other_workspace):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Scoped"},
        files={"knowledge_base_files": ("a.txt", b"secret", "text/plain")},
    )
    body = resp.json()
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")
    dl = await client.get(
        f"/get-knowledge-base-file/{body['knowledge_base_id']}/source/{doc['source_id']}",
        headers=OTHER_AUTH_HEADERS,
    )
    assert dl.status_code == 404


async def test_delete_source_removes_blob(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Cleanup"},
        files={"knowledge_base_files": ("a.txt", b"bye", "text/plain")},
    )
    body = resp.json()
    kb_id = body["knowledge_base_id"]
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")

    deleted = await client.delete(
        f"/delete-knowledge-base-source/{kb_id}/source/{doc['source_id']}",
        headers=AUTH_HEADERS,
    )
    assert deleted.status_code == 204
    dl = await client.get(
        f"/get-knowledge-base-file/{kb_id}/source/{doc['source_id']}", headers=AUTH_HEADERS
    )
    assert dl.status_code == 404


async def test_delete_kb_removes_blobs(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Cleanup KB"},
        files={"knowledge_base_files": ("a.txt", b"bye", "text/plain")},
    )
    body = resp.json()
    kb_id = body["knowledge_base_id"]
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")

    await client.delete(f"/delete-knowledge-base/{kb_id}", headers=AUTH_HEADERS)
    # The download route checks the blob row directly, so a 404 here proves
    # the row went away with the KB (not merely that the KB is gone).
    dl = await client.get(
        f"/get-knowledge-base-file/{kb_id}/source/{doc['source_id']}", headers=AUTH_HEADERS
    )
    assert dl.status_code == 404
