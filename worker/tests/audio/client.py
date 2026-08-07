"""The control plane, as far as this harness needs it.

Two calls: start a web call and get one back. `create-web-call` is what makes
this layer different from every other one — it returns a LiveKit room and a
token, so the caller bot can join the same room the worker is dispatched into
and hear the actual synthesized voice rather than a transcript of it.

httpx, sync, and small enough to be covered by the dev-group CI job.
"""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT_S = 30.0


class ControlPlaneError(RuntimeError):
    """The API refused, with the body it refused with."""


def _post(
    api_base: str, api_key: str, path: str, body: dict, client: httpx.Client | None
) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = http.post(
            f"{api_base.rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
    finally:
        if owned:
            http.close()
    if response.status_code >= 400:
        raise ControlPlaneError(f"POST {path} → HTTP {response.status_code} {response.text[:400]}")
    return response.json()


def _get(api_base: str, api_key: str, path: str, client: httpx.Client | None) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = http.get(
            f"{api_base.rstrip('/')}{path}", headers={"Authorization": f"Bearer {api_key}"}
        )
    finally:
        if owned:
            http.close()
    if response.status_code >= 400:
        raise ControlPlaneError(f"GET {path} → HTTP {response.status_code} {response.text[:400]}")
    return response.json()


def create_web_call(
    api_base: str,
    api_key: str,
    agent_id: str,
    *,
    variables: dict[str, str] | None = None,
    metadata: dict[str, Any] | None = None,
    agent_version: int | str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Start a web call and get back everything needed to join its room.

    The variables go in under `retell_llm_dynamic_variables` because that is
    the field name the wire contract froze; the harness does not get to pick a
    friendlier one.

    The response is checked for the three fields the caller bot cannot work
    without. A missing `access_token` fails here, at the request, rather than
    forty lines later as an unhelpful LiveKit authentication error.
    """
    body: dict[str, Any] = {"agent_id": agent_id}
    if variables:
        body["retell_llm_dynamic_variables"] = variables
    if metadata:
        body["metadata"] = metadata
    if agent_version is not None:
        body["agent_version"] = agent_version

    call = _post(api_base, api_key, "/v2/create-web-call", body, client)
    missing = [f for f in ("call_id", "access_token", "livekit_server_url") if not call.get(f)]
    if missing:
        raise ControlPlaneError(f"create-web-call returned no {', '.join(missing)}")
    return call


def get_call(
    api_base: str, api_key: str, call_id: str, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """The platform's own record of the call.

    Kept beside the recording in the artifacts. When the audio and the platform
    transcript disagree — the greeting heard twice and logged once — the
    disagreement is the finding, and it can only be seen with both.
    """
    return _get(api_base, api_key, f"/v2/get-call/{call_id}", client)


def transcript_lines(call: dict[str, Any]) -> list[tuple[str, str]]:
    """The platform transcript as `(role, content)`, tool calls dropped.

    Reads `transcript_object` and falls back to nothing rather than to the flat
    `transcript` string: the flat one is speaker-prefixed prose, and parsing
    roles back out of it invents structure that is not there.
    """
    turns = call.get("transcript_object") or []
    return [
        (str(turn.get("role") or ""), str(turn.get("content") or ""))
        for turn in turns
        if turn.get("content")
    ]
