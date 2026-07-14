"""Workspace members & invites: CRUD, isolation, and the Google-login accept flow."""

import time


from architeq_api.api import auth_google
from tests.conftest import AUTH_HEADERS, OTHER_AUTH_HEADERS, WORKSPACE_ID

INVITED_CLAIMS = {
    "iss": "https://accounts.google.com",
    "email": "invitee@example.com",
    "email_verified": True,
    "name": "Invitee",
}


def _google_as(monkeypatch, email: str, name: str = "Someone"):
    monkeypatch.setattr(
        auth_google,
        "verify_google_id_token",
        lambda token: {**INVITED_CLAIMS, "email": email, "name": name},
    )


async def _create_invite(client, email="invitee@example.com", role="member"):
    resp = await client.post(
        "/create-invite", json={"email": email, "role": role}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------- invite CRUD


async def test_create_and_list_invite(client):
    invite = await _create_invite(client)
    assert invite["email"] == "invitee@example.com"
    assert invite["role"] == "member"
    assert invite["status"] == "pending"
    assert invite["token"]
    assert invite["expires_at_ms"] > int(time.time() * 1000)

    listed = (await client.get("/list-invites", headers=AUTH_HEADERS)).json()
    assert [i["invite_id"] for i in listed] == [invite["invite_id"]]


async def test_reinvite_regenerates_pending_invite(client):
    first = await _create_invite(client)
    second = await _create_invite(client, role="admin")
    # Same row, fresh token + role — never two live invites for one email.
    assert second["invite_id"] == first["invite_id"]
    assert second["token"] != first["token"]
    assert second["role"] == "admin"
    listed = (await client.get("/list-invites", headers=AUTH_HEADERS)).json()
    assert len(listed) == 1


async def test_invite_email_normalized_and_validated(client):
    invite = await _create_invite(client, email="  MiXeD@Example.COM ")
    assert invite["email"] == "mixed@example.com"

    bad = await client.post("/create-invite", json={"email": "not-an-email"}, headers=AUTH_HEADERS)
    assert bad.status_code == 422


async def test_revoke_invite(client):
    invite = await _create_invite(client)
    resp = await client.post(f"/revoke-invite/{invite['invite_id']}", headers=AUTH_HEADERS)
    assert resp.status_code == 204
    assert (await client.get("/list-invites", headers=AUTH_HEADERS)).json() == []
    # Revoking twice conflicts.
    resp = await client.post(f"/revoke-invite/{invite['invite_id']}", headers=AUTH_HEADERS)
    assert resp.status_code == 409


async def test_invites_are_workspace_scoped(client, other_workspace):
    invite = await _create_invite(client)
    listed = (await client.get("/list-invites", headers=OTHER_AUTH_HEADERS)).json()
    assert listed == []
    resp = await client.post(f"/revoke-invite/{invite['invite_id']}", headers=OTHER_AUTH_HEADERS)
    assert resp.status_code == 404


# ------------------------------------------------------------- accept flow


async def test_invite_accept_creates_member_and_session(client, monkeypatch):
    invite = await _create_invite(client)
    _google_as(monkeypatch, "invitee@example.com", "Invitee")

    resp = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["workspace_id"] == WORKSPACE_ID

    members = (await client.get("/list-members", headers=AUTH_HEADERS)).json()
    assert [(m["email"], m["role"]) for m in members] == [("invitee@example.com", "member")]
    # Consumed: no longer pending.
    assert (await client.get("/list-invites", headers=AUTH_HEADERS)).json() == []


async def test_member_can_login_again_without_invite_or_allowlist(client, monkeypatch):
    invite = await _create_invite(client)
    _google_as(monkeypatch, "invitee@example.com")
    await client.post("/auth/google", json={"id_token": "fake", "invite_token": invite["token"]})

    # Not on the allowlist (conftest allows only admin@example.com), but a member now.
    resp = await client.post("/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 200
    assert resp.json()["workspace_id"] == WORKSPACE_ID


async def test_invite_accept_is_idempotent(client, monkeypatch):
    invite = await _create_invite(client)
    _google_as(monkeypatch, "invitee@example.com")
    first = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert first.status_code == 200
    # Re-clicking the consumed link still signs the member in.
    again = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert again.status_code == 200
    members = (await client.get("/list-members", headers=AUTH_HEADERS)).json()
    assert len(members) == 1


async def test_invite_rejects_mismatched_email(client, monkeypatch):
    invite = await _create_invite(client)
    _google_as(monkeypatch, "someone-else@example.com")
    resp = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert resp.status_code == 403
    assert (await client.get("/list-members", headers=AUTH_HEADERS)).json() == []


async def test_invite_rejects_bad_revoked_and_expired_tokens(client, monkeypatch):
    _google_as(monkeypatch, "invitee@example.com")

    resp = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": "no-such-token"}
    )
    assert resp.status_code == 403

    invite = await _create_invite(client)
    await client.post(f"/revoke-invite/{invite['invite_id']}", headers=AUTH_HEADERS)
    resp = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert resp.status_code == 403

    expired = await _create_invite(client)
    from architeq_api import db as db_module
    from architeq_api.models import WorkspaceInvite

    async with db_module.session_factory()() as session:
        row = await session.get(WorkspaceInvite, expired["invite_id"])
        row.expires_at_ms = 1
        await session.commit()
    resp = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": expired["token"]}
    )
    assert resp.status_code == 403


# ------------------------------------------------------------- members list


async def test_allowlist_login_records_owner_member(client, monkeypatch):
    _google_as(monkeypatch, "admin@example.com", "Admin")
    resp = await client.post("/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 200

    members = (await client.get("/list-members", headers=AUTH_HEADERS)).json()
    assert [(m["email"], m["role"]) for m in members] == [("admin@example.com", "owner")]

    # Logging in again doesn't duplicate the row.
    await client.post("/auth/google", json={"id_token": "fake"})
    members = (await client.get("/list-members", headers=AUTH_HEADERS)).json()
    assert len(members) == 1


async def test_cannot_invite_existing_member(client, monkeypatch):
    _google_as(monkeypatch, "admin@example.com")
    await client.post("/auth/google", json={"id_token": "fake"})
    resp = await client.post(
        "/create-invite", json={"email": "Admin@Example.com"}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 409


async def test_invited_by_recorded_from_session(client, monkeypatch):
    _google_as(monkeypatch, "admin@example.com")
    login = await client.post("/auth/google", json={"id_token": "fake"})
    session_token = login.json()["token"]
    resp = await client.post(
        "/create-invite",
        json={"email": "invitee@example.com"},
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["invited_by"] == "admin@example.com"


# ---------------------------------------------------- roles & offboarding


async def _login_as_member(client, monkeypatch, email="invitee@example.com") -> str:
    """Invite + accept + return the member's session token."""
    invite = await _create_invite(client, email=email)
    _google_as(monkeypatch, email)
    resp = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


async def test_member_role_cannot_manage_invites(client, monkeypatch):
    token = await _login_as_member(client, monkeypatch)
    member_headers = {"Authorization": f"Bearer {token}"}

    resp = await client.post(
        "/create-invite", json={"email": "x@example.com", "role": "admin"}, headers=member_headers
    )
    assert resp.status_code == 403
    assert (await client.get("/list-invites", headers=member_headers)).status_code == 403
    other = await _create_invite(client, email="pending@example.com")
    resp = await client.post(f"/revoke-invite/{other['invite_id']}", headers=member_headers)
    assert resp.status_code == 403
    resp = await client.post(
        "/remove-member", json={"email": "someone@example.com"}, headers=member_headers
    )
    assert resp.status_code == 403
    # Viewing members stays open to every member.
    assert (await client.get("/list-members", headers=member_headers)).status_code == 200


async def test_admin_role_can_manage_invites(client, monkeypatch):
    invite = await _create_invite(client, email="admin2@example.com", role="admin")
    _google_as(monkeypatch, "admin2@example.com")
    login = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    admin_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    resp = await client.post(
        "/create-invite", json={"email": "new@example.com"}, headers=admin_headers
    )
    assert resp.status_code == 201


async def test_remove_member_revokes_login(client, monkeypatch):
    await _login_as_member(client, monkeypatch)

    resp = await client.post(
        "/remove-member", json={"email": "invitee@example.com"}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 204
    assert (await client.get("/list-members", headers=AUTH_HEADERS)).json() == []

    # Their invite was consumed and the member row is gone: login now 403s.
    _google_as(monkeypatch, "invitee@example.com")
    resp = await client.post("/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 403

    # Unknown member 404s.
    resp = await client.post(
        "/remove-member", json={"email": "ghost@example.com"}, headers=AUTH_HEADERS
    )
    assert resp.status_code == 404


async def test_cannot_remove_yourself(client, monkeypatch):
    _google_as(monkeypatch, "admin@example.com")
    login = await client.post("/auth/google", json={"id_token": "fake"})
    session_headers = {"Authorization": f"Bearer {login.json()['token']}"}
    resp = await client.post(
        "/remove-member", json={"email": "admin@example.com"}, headers=session_headers
    )
    assert resp.status_code == 403
    assert len((await client.get("/list-members", headers=AUTH_HEADERS)).json()) == 1


async def test_expired_invites_are_not_listed_and_reinvite_regenerates(client):
    invite = await _create_invite(client)
    from architeq_api import db as db_module
    from architeq_api.models import WorkspaceInvite

    async with db_module.session_factory()() as session:
        row = await session.get(WorkspaceInvite, invite["invite_id"])
        row.expires_at_ms = 1
        await session.commit()

    assert (await client.get("/list-invites", headers=AUTH_HEADERS)).json() == []
    # Re-inviting revives the hidden row instead of conflicting with it.
    fresh = await _create_invite(client)
    assert fresh["invite_id"] == invite["invite_id"]
    listed = (await client.get("/list-invites", headers=AUTH_HEADERS)).json()
    assert [i["invite_id"] for i in listed] == [invite["invite_id"]]


async def test_allowlisted_login_prefers_first_workspace_over_invited_one(
    client, other_workspace, monkeypatch
):
    """Allowlist keeps its pre-invites guarantee: plain logins by allowlisted
    emails always land on the first workspace, even after accepting an invite
    elsewhere."""
    from tests.conftest import OTHER_AUTH_HEADERS

    resp = await client.post(
        "/create-invite", json={"email": "admin@example.com"}, headers=OTHER_AUTH_HEADERS
    )
    invite = resp.json()
    _google_as(monkeypatch, "admin@example.com")
    accept = await client.post(
        "/auth/google", json={"id_token": "fake", "invite_token": invite["token"]}
    )
    assert accept.json()["workspace_id"] == other_workspace

    plain = await client.post("/auth/google", json={"id_token": "fake"})
    assert plain.json()["workspace_id"] == WORKSPACE_ID
