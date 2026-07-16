"""Dashboard Google auth: allowlist, session issuance, session-as-credential."""

import time

import jwt
import pytest

from arhiteq_api.api import auth_google
from arhiteq_api.sessions import decode_session, issue_session
from tests.conftest import WORKSPACE_ID

GOOGLE_CLAIMS = {
    "iss": "https://accounts.google.com",
    "email": "admin@example.com",
    "email_verified": True,
    "name": "Admin",
    "picture": "https://lh3.example/p.jpg",
}


@pytest.fixture
def fake_google(monkeypatch):
    monkeypatch.setattr(auth_google, "verify_google_id_token", lambda token: dict(GOOGLE_CLAIMS))


async def test_login_issues_session_for_allowed_email(client, fake_google):
    resp = await client.post("/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "admin@example.com"
    assert body["workspace_id"] == WORKSPACE_ID
    claims = decode_session(body["token"])
    assert claims["sub"] == "admin@example.com"
    assert claims["ws"] == WORKSPACE_ID
    assert body["expires_at"] > time.time()


async def test_login_rejects_email_not_on_allowlist(client, monkeypatch):
    monkeypatch.setattr(
        auth_google,
        "verify_google_id_token",
        lambda token: {**GOOGLE_CLAIMS, "email": "intruder@evil.com"},
    )
    resp = await client.post("/auth/google", json={"id_token": "fake"})
    assert resp.status_code == 403


async def test_session_token_works_as_api_credential(client, fake_google):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["token"]
    resp = await client.get("/list-agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


async def test_auth_me_roundtrip_and_rejects_garbage(client, fake_google):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@example.com"

    bad = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert bad.status_code == 401


async def test_expired_session_rejected(client):
    token = jwt.encode(
        {
            "iss": "arhiteq",
            "sub": "admin@example.com",
            "ws": WORKSPACE_ID,
            "iat": int(time.time()) - 7200,
            "exp": int(time.time()) - 3600,
        },
        "test-session-secret",
        algorithm="HS256",
    )
    resp = await client.get("/list-agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


async def test_session_signed_with_wrong_secret_rejected(client):
    token = jwt.encode(
        {
            "iss": "arhiteq",
            "sub": "admin@example.com",
            "ws": WORKSPACE_ID,
            "exp": int(time.time()) + 3600,
        },
        "attacker-secret",
        algorithm="HS256",
    )
    resp = await client.get("/list-agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_email_allowlist_logic(monkeypatch):
    from arhiteq_api.config import Settings

    monkeypatch.setattr(
        auth_google,
        "get_settings",
        lambda: Settings(
            dashboard_allowed_emails=["a@x.com"], dashboard_allowed_domains=["corp.io"]
        ),
    )
    assert auth_google._email_allowed("a@x.com")
    assert auth_google._email_allowed("A@X.COM")
    assert auth_google._email_allowed("anyone@corp.io")
    assert not auth_google._email_allowed("b@x.com")
    assert not auth_google._email_allowed("a@x.com.evil.com")

    monkeypatch.setattr(auth_google, "get_settings", lambda: Settings())
    # fail closed with no allowlist configured
    assert not auth_google._email_allowed("a@x.com")


def test_issue_session_requires_secret(monkeypatch):
    from arhiteq_api import sessions
    from arhiteq_api.config import Settings

    monkeypatch.setattr(sessions, "get_settings", lambda: Settings(session_secret=""))
    with pytest.raises(RuntimeError):
        issue_session("a@x.com", "ws_1")
