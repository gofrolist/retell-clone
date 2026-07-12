"""Client for the private control-plane API (docs/INTERNAL_API.md).

Every request carries ``X-Internal-Token: <ARCHITEQ_INTERNAL_TOKEN>``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("architeq-worker.internal-api")

_DEFAULT_TIMEOUT = 10.0
# Inbound resolve waits for the customer's inbound webhook (≤9.5s on the
# control-plane side), so give it more headroom than the default.
_RESOLVE_TIMEOUT = 15.0


class InternalAPIError(Exception):
    pass


class InternalAPI:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ["ARCHITEQ_API_URL"]).rstrip("/")
        self._token = token or os.environ["ARCHITEQ_INTERNAL_TOKEN"]
        self._http = http or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Token": self._token}

    async def get_call_config(self, call_id: str) -> dict[str, Any]:
        resp = await self._http.get(
            f"{self._base_url}/internal/calls/{call_id}/config", headers=self._headers
        )
        if resp.status_code != 200:
            raise InternalAPIError(f"config fetch for {call_id} -> {resp.status_code}")
        return resp.json()

    async def get_agent_config(self, agent_id: str, *, call_id: str) -> dict[str, Any]:
        """{agent, llm} for an agent_swap destination (docs/INTERNAL_API.md).

        call_id scopes the lookup: the control plane only returns agents in
        the calling call's workspace.
        """
        resp = await self._http.get(
            f"{self._base_url}/internal/agents/{agent_id}/config",
            params={"call_id": call_id},
            headers=self._headers,
        )
        if resp.status_code != 200:
            raise InternalAPIError(f"agent config fetch for {agent_id} -> {resp.status_code}")
        return resp.json()

    async def resolve_inbound(self, from_number: str, to_number: str, room: str) -> dict[str, Any]:
        resp = await self._http.post(
            f"{self._base_url}/internal/inbound/resolve",
            json={"from_number": from_number, "to_number": to_number, "room": room},
            headers=self._headers,
            timeout=_RESOLVE_TIMEOUT,
        )
        if resp.status_code != 200:
            raise InternalAPIError(f"inbound resolve for {to_number} -> {resp.status_code}")
        return resp.json()

    async def post_event(self, call_id: str, payload: dict[str, Any]) -> None:
        """Lifecycle/streaming event; failures are logged, never fatal."""
        try:
            resp = await self._http.post(
                f"{self._base_url}/internal/calls/{call_id}/events",
                json=payload,
                headers=self._headers,
            )
            if resp.status_code >= 300:
                logger.warning(
                    "event %s for %s -> %d", payload.get("event"), call_id, resp.status_code
                )
        except httpx.HTTPError as exc:
            logger.warning("event %s for %s failed: %s", payload.get("event"), call_id, exc)

    async def finalize(self, call_id: str, payload: dict[str, Any]) -> None:
        """Terminal call update; idempotent server-side. One retry."""
        url = f"{self._base_url}/internal/calls/{call_id}/finalize"
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._http.post(url, json=payload, headers=self._headers)
                if resp.status_code < 300:
                    return
                last_exc = InternalAPIError(f"finalize {call_id} -> {resp.status_code}")
            except httpx.HTTPError as exc:
                last_exc = exc
            logger.warning("finalize attempt %d for %s failed: %s", attempt + 1, call_id, last_exc)
        raise InternalAPIError(f"finalize failed for {call_id}: {last_exc}")

    async def aclose(self) -> None:
        await self._http.aclose()
