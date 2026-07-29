"""Real Retell flows must survive a create/read round-trip byte-for-byte.

The fixtures are sanitized captures from a live Retell account (see
docs/superpowers/specs/2026-07-29-conversation-flow-agents-design.md). They are
the schema authority: if the API drops a field they carry, that is a contract
break, because the migration script copies flows verbatim.
"""

import json
from pathlib import Path

import pytest

from tests.conftest import AUTH_HEADERS

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "retell_flows"

# The API mints or bumps these, so a fixture's values can never match.
SERVER_MANAGED = frozenset({"conversation_flow_id", "version", "last_modification_timestamp"})

FIXTURE_NAMES = sorted(p.name for p in FIXTURE_DIR.glob("*.json"))


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())


def test_fixtures_are_present():
    """Guard against an empty glob silently parametrizing zero tests."""
    assert FIXTURE_NAMES, f"no fixtures found in {FIXTURE_DIR}"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
async def test_retell_flow_round_trips_unchanged(client, name):
    source = load_fixture(name)
    payload = {k: v for k, v in source.items() if k not in SERVER_MANAGED}

    created = await client.post("/create-conversation-flow", headers=AUTH_HEADERS, json=payload)
    assert created.status_code == 201, created.text

    got = await client.get(
        f"/get-conversation-flow/{created.json()['conversation_flow_id']}",
        headers=AUTH_HEADERS,
    )
    assert got.status_code == 200, got.text
    body = got.json()

    dropped = sorted(k for k in payload if k not in body)
    assert not dropped, f"{name}: fields dropped by the API: {dropped}"

    altered = sorted(k for k, v in payload.items() if body[k] != v)
    assert not altered, f"{name}: fields altered by the API: {altered}"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
async def test_patch_accepts_every_writable_field(client, name):
    """A PATCH carrying a whole flow must write every field, not a subset.

    This is how the editor saves: it PATCHes the flow object it holds.
    """
    source = load_fixture(name)
    payload = {k: v for k, v in source.items() if k not in SERVER_MANAGED}

    created = await client.post(
        "/create-conversation-flow", headers=AUTH_HEADERS, json={"nodes": []}
    )
    assert created.status_code == 201, created.text
    flow_id = created.json()["conversation_flow_id"]

    patched = await client.patch(
        f"/update-conversation-flow/{flow_id}", headers=AUTH_HEADERS, json=payload
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()

    ignored = sorted(k for k, v in payload.items() if body[k] != v)
    assert not ignored, f"{name}: fields ignored by PATCH: {ignored}"
