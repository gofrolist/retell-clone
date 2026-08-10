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


def _patch(
    api_base: str, api_key: str, path: str, body: dict, client: httpx.Client | None
) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=TIMEOUT_S)
    try:
        response = http.patch(
            f"{api_base.rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
    finally:
        if owned:
            http.close()
    if response.status_code >= 400:
        raise ControlPlaneError(f"PATCH {path} → HTTP {response.status_code} {response.text[:400]}")
    return response.json()


def get_agent(
    api_base: str,
    api_key: str,
    agent_id: str,
    *,
    version: int | str | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """One agent, for the `response_engine` that says where its tools live.

    `version` takes a number, `"latest"` or `"latest_published"`. It matters
    which: the endpoint defaults to **latest**, the version the editor shows,
    while a call runs **latest_published**. Reading the default and calling it
    "what this call will do" is how a check passes on a draft nobody dials.
    """
    path = f"/get-agent/{agent_id}"
    if version is not None:
        path += f"?version={version}"
    return _get(api_base, api_key, path, client)


def get_agent_version(
    api_base: str, api_key: str, agent_id: str, version: int, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """One version, with `response_engine_config` — the frozen prompt and tools.

    A published version is an immutable snapshot: editing the live LLM row does
    not touch it, and a call runs the snapshot. This is the only way to see
    what a call would actually send.
    """
    return _get(api_base, api_key, f"/get-agent-version/{agent_id}/{version}", client)


def get_llm(
    api_base: str, api_key: str, llm_id: str, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """The response engine behind an agent, whose `general_tools` carry the URLs."""
    return _get(api_base, api_key, f"/get-retell-llm/{llm_id}", client)


def update_llm_tools(
    api_base: str,
    api_key: str,
    llm_id: str,
    tools: list[dict[str, Any]],
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Replace an engine's tool list, and nothing else about it.

    The only write this harness makes. It exists so `toolsink` can repoint tool
    URLs at a local sink; the guard that keeps it away from anything but a
    local stack lives there, with the reasoning.
    """
    return _patch(
        api_base, api_key, f"/update-retell-llm/{llm_id}", {"general_tools": tools}, client
    )


def publish_agent(
    api_base: str, api_key: str, agent_id: str, *, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Publish the open draft, so calls run what was just edited.

    Paired with `update_llm_tools`: that opens a draft, and a draft nobody
    publishes is a config change no call ever sees.
    """
    return _post(api_base, api_key, f"/publish-agent/{agent_id}", {}, client)


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
