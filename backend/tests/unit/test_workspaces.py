"""Multiple workspaces per user: list, create, switch, and the role catalog."""

from arhiteq_api.api import auth_google
from arhiteq_api.sessions import decode_session
from tests.conftest import AUTH_HEADERS, OTHER_AUTH_HEADERS, WORKSPACE_ID

GOOGLE_CLAIMS = {
    "iss": "https://accounts.google.com",
    "email_verified": True,
}


def _google_as(monkeypatch, email: str, name: str = "Someone"):
    monkeypatch.setattr(
        auth_google,
        "verify_google_id_token",
        lambda token: {**GOOGLE_CLAIMS, "email": email, "name": name},
    )


async def _login(client, monkeypatch, email="admin@example.com", name="Admin") -> dict:
    _google_as(monkeypatch, email, name)
    resp = await client.post("/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# ------------------------------------------------------------------- listing


async def test_list_workspaces_returns_memberships(client, monkeypatch, other_workspace):
    headers = await _login(client, monkeypatch)
    # Accept an invite into the second workspace so there are two memberships.
    invite = (
        await client.post(
            "/create-invite",
            json={"email": "admin@example.com", "role": "admin"},
            headers=OTHER_AUTH_HEADERS,
        )
    ).json()
    await client.post("/auth/google", json={"id_token": "fake", "invite_token": invite["token"]})

    listed = (await client.get("/list-workspaces", headers=headers)).json()
    assert [(w["workspace_id"], w["role"]) for w in listed] == [
        (WORKSPACE_ID, "owner"),
        (other_workspace, "admin"),
    ]
    assert [w["is_current"] for w in listed] == [True, False]


async def test_list_workspaces_includes_current_without_member_row(client):
    """An API key has no membership, and an allowlisted session is issued
    before its owner row is written — both must still see where they are."""
    listed = (await client.get("/list-workspaces", headers=AUTH_HEADERS)).json()
    assert [w["workspace_id"] for w in listed] == [WORKSPACE_ID]
    assert listed[0]["is_current"] is True


async def test_list_workspaces_requires_auth(client):
    assert (await client.get("/list-workspaces")).status_code == 401
    bad = {"Authorization": "Bearer key_nope"}
    assert (await client.get("/list-workspaces", headers=bad)).status_code == 401


# ------------------------------------------------------------------ creating


async def test_create_workspace_switches_the_session(client, monkeypatch):
    headers = await _login(client, monkeypatch)
    resp = await client.post(
        "/create-workspace",
        json={"name": "  Acme Company  ", "workspace_type": "agency"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["name"] == "Acme Company"
    assert created["role"] == "owner"
    assert created["workspace_id"] != WORKSPACE_ID
    # Session callers get a token, never a raw key.
    assert "api_key" not in created
    assert decode_session(created["token"])["ws"] == created["workspace_id"]

    new_headers = {"Authorization": f"Bearer {created['token']}"}
    ws = (await client.get("/workspace", headers=new_headers)).json()
    assert ws["workspace_id"] == created["workspace_id"]
    assert ws["settings"]["workspace_type"] == "agency"
    # The creator is its owner, and it starts empty.
    members = (await client.get("/list-members", headers=new_headers)).json()
    assert [(m["email"], m["role"]) for m in members] == [("admin@example.com", "owner")]
    assert (await client.get("/list-agents", headers=new_headers)).json() == []


async def test_created_workspace_gets_a_working_api_key(client, monkeypatch):
    """`require_api_key` resolves a session to the workspace's oldest live
    key, so a keyless workspace would 403 on every request after the switch."""
    headers = await _login(client, monkeypatch)
    created = (
        await client.post("/create-workspace", json={"name": "Keyed"}, headers=headers)
    ).json()
    new_headers = {"Authorization": f"Bearer {created['token']}"}
    keys = (await client.get("/list-api-keys", headers=new_headers)).json()
    assert len(keys) == 1 and keys[0]["revoked"] is False


async def test_create_workspace_with_api_key_returns_the_new_key(client):
    """API-key callers have no session to switch with, so the key they get
    back is the only way into the workspace they just made."""
    resp = await client.post("/create-workspace", json={"name": "Ops"}, headers=AUTH_HEADERS)
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert "token" not in created
    ws = (
        await client.get("/workspace", headers={"Authorization": f"Bearer {created['api_key']}"})
    ).json()
    assert ws["workspace_id"] == created["workspace_id"]


async def test_create_workspace_validates_name_and_type(client, monkeypatch):
    headers = await _login(client, monkeypatch)
    blank = await client.post("/create-workspace", json={"name": "   "}, headers=headers)
    assert blank.status_code == 422
    missing = await client.post("/create-workspace", json={}, headers=headers)
    assert missing.status_code == 422
    bad_type = await client.post(
        "/create-workspace", json={"name": "X", "workspace_type": "startup"}, headers=headers
    )
    assert bad_type.status_code == 422


# ----------------------------------------------------------------- switching


async def test_switch_workspace_reissues_the_session(client, monkeypatch):
    headers = await _login(client, monkeypatch)
    created = (
        await client.post("/create-workspace", json={"name": "Second"}, headers=headers)
    ).json()

    # Switch back to the original with the *first* session token.
    resp = await client.post(
        "/switch-workspace", json={"workspace_id": WORKSPACE_ID}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert decode_session(resp.json()["token"])["ws"] == WORKSPACE_ID

    # ...and forward again into the new one.
    resp = await client.post(
        "/switch-workspace", json={"workspace_id": created["workspace_id"]}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Second"


async def test_cannot_switch_into_a_workspace_you_are_not_in(client, monkeypatch, other_workspace):
    headers = await _login(client, monkeypatch)
    resp = await client.post(
        "/switch-workspace", json={"workspace_id": other_workspace}, headers=headers
    )
    # Same 404 as a missing workspace: no cross-tenant existence oracle.
    assert resp.status_code == 404
    missing = await client.post(
        "/switch-workspace", json={"workspace_id": "ws_nope"}, headers=headers
    )
    assert missing.status_code == 404


async def test_removed_member_cannot_switch_back_with_a_stale_session(client, monkeypatch):
    """Membership is re-checked at switch time, not trusted from the token."""
    invite = (
        await client.post(
            "/create-invite", json={"email": "invitee@example.com"}, headers=AUTH_HEADERS
        )
    ).json()
    _google_as(monkeypatch, "invitee@example.com")
    login = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    stale = {"Authorization": f"Bearer {login.json()['token']}"}

    await client.post("/remove-member", json={"email": "invitee@example.com"}, headers=AUTH_HEADERS)
    resp = await client.post(
        "/switch-workspace", json={"workspace_id": WORKSPACE_ID}, headers=stale
    )
    assert resp.status_code == 404


async def test_api_keys_cannot_switch_workspaces(client):
    resp = await client.post(
        "/switch-workspace", json={"workspace_id": WORKSPACE_ID}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 403


async def test_switching_after_deleting_the_active_workspace(client, monkeypatch):
    """The switch endpoints authenticate on identity, not on the active
    workspace's API key — otherwise the caller would be stranded here."""
    headers = await _login(client, monkeypatch)
    created = (
        await client.post("/create-workspace", json={"name": "Doomed"}, headers=headers)
    ).json()
    doomed = {"Authorization": f"Bearer {created['token']}"}
    assert (await client.delete("/workspace", headers=doomed)).status_code == 204

    listed = (await client.get("/list-workspaces", headers=doomed)).json()
    assert [w["workspace_id"] for w in listed] == [WORKSPACE_ID]
    resp = await client.post(
        "/switch-workspace", json={"workspace_id": WORKSPACE_ID}, headers=doomed
    )
    assert resp.status_code == 200


# --------------------------------------------------------------------- roles


async def test_list_roles(client):
    roles = (await client.get("/list-roles", headers=AUTH_HEADERS)).json()
    assert [r["role"] for r in roles] == ["owner", "admin", "member"]
    assert all(r["type"] == "System" and r["description"] for r in roles)


async def _invite_and_accept(client, monkeypatch, email: str, role: str = "member") -> dict:
    invite = (
        await client.post(
            "/create-invite", json={"email": email, "role": role}, headers=AUTH_HEADERS
        )
    ).json()
    _google_as(monkeypatch, email)
    login = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['token']}"}


async def test_update_member_role(client, monkeypatch):
    await _invite_and_accept(client, monkeypatch, "invitee@example.com")
    resp = await client.post(
        "/update-member-role",
        json={"email": "invitee@example.com", "role": "admin"},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"
    members = (await client.get("/list-members", headers=AUTH_HEADERS)).json()
    assert {m["email"]: m["role"] for m in members}["invitee@example.com"] == "admin"

    unknown = await client.post(
        "/update-member-role",
        json={"email": "ghost@example.com", "role": "member"},
        headers=AUTH_HEADERS,
    )
    assert unknown.status_code == 404
    bad_role = await client.post(
        "/update-member-role",
        json={"email": "invitee@example.com", "role": "superuser"},
        headers=AUTH_HEADERS,
    )
    assert bad_role.status_code == 422


async def test_members_cannot_change_roles(client, monkeypatch):
    headers = await _invite_and_accept(client, monkeypatch, "invitee@example.com")
    await _invite_and_accept(client, monkeypatch, "other@example.com")
    resp = await client.post(
        "/update-member-role", json={"email": "other@example.com", "role": "admin"}, headers=headers
    )
    assert resp.status_code == 403


async def test_cannot_change_your_own_role(client, monkeypatch):
    """Otherwise an admin promotes themselves to owner in one call."""
    headers = await _invite_and_accept(client, monkeypatch, "admin2@example.com", role="admin")
    resp = await client.post(
        "/update-member-role",
        json={"email": "admin2@example.com", "role": "owner"},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_only_owners_grant_or_revoke_the_owner_role(client, monkeypatch):
    owner = await _login(client, monkeypatch)  # allowlisted → owner member row
    admin = await _invite_and_accept(client, monkeypatch, "admin2@example.com", role="admin")
    await _invite_and_accept(client, monkeypatch, "invitee@example.com")

    granting = await client.post(
        "/update-member-role",
        json={"email": "invitee@example.com", "role": "owner"},
        headers=admin,
    )
    assert granting.status_code == 403
    revoking = await client.post(
        "/update-member-role",
        json={"email": "admin@example.com", "role": "member"},
        headers=admin,
    )
    assert revoking.status_code == 403

    # The owner can do both.
    assert (
        await client.post(
            "/update-member-role",
            json={"email": "invitee@example.com", "role": "owner"},
            headers=owner,
        )
    ).status_code == 200


async def test_admins_cannot_evict_an_owner(client, monkeypatch):
    """Removing an owner is revoking the owner role by another name.

    With a second owner present the last-owner guard doesn't fire, so without
    an explicit check an admin blocked from *demoting* an owner could just
    delete them instead.
    """
    owner = await _login(client, monkeypatch)  # admin@example.com, sole owner
    admin = await _invite_and_accept(client, monkeypatch, "admin2@example.com", role="admin")
    await _invite_and_accept(client, monkeypatch, "second@example.com")
    await client.post(
        "/update-member-role",
        json={"email": "second@example.com", "role": "owner"},
        headers=owner,
    )

    resp = await client.post("/remove-member", json={"email": "second@example.com"}, headers=admin)
    assert resp.status_code == 403
    emails = {m["email"] for m in (await client.get("/list-members", headers=AUTH_HEADERS)).json()}
    assert "second@example.com" in emails

    # An owner may still remove a fellow owner.
    assert (
        await client.post("/remove-member", json={"email": "second@example.com"}, headers=owner)
    ).status_code == 204
    # ...and admins keep removing non-owners.
    await _invite_and_accept(client, monkeypatch, "plain@example.com")
    assert (
        await client.post("/remove-member", json={"email": "plain@example.com"}, headers=admin)
    ).status_code == 204


async def test_last_owner_cannot_be_demoted_or_removed(client, monkeypatch):
    await _login(client, monkeypatch)  # admin@example.com becomes the sole owner
    admin = await _invite_and_accept(client, monkeypatch, "admin2@example.com", role="admin")

    demote = await client.post(
        "/update-member-role",
        json={"email": "admin@example.com", "role": "member"},
        headers=AUTH_HEADERS,
    )
    assert demote.status_code == 409
    remove = await client.post(
        "/remove-member", json={"email": "admin@example.com"}, headers=AUTH_HEADERS
    )
    assert remove.status_code == 409

    # Promote a second owner and the first is free to go.
    await client.post(
        "/update-member-role",
        json={"email": "admin2@example.com", "role": "owner"},
        headers=AUTH_HEADERS,
    )
    assert (
        await client.post("/remove-member", json={"email": "admin@example.com"}, headers=admin)
    ).status_code == 204
