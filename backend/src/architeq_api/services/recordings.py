"""Convert recording object URLs into V4 signed URLs.

The recordings bucket is private (`public_access_prevention = enforced`),
so the plain `https://storage.googleapis.com/<bucket>/<object>` URL the
worker reports at finalize would 403. Signing uses the pod's Workload
Identity via the IAM signBlob API (terraform grants the api SA
`roles/iam.serviceAccountTokenCreator` on itself) — no key file.

V4 signed URLs cap at 7 days; consumers that need recordings longer
archive them at cutover (docs/MIGRATION.md Phase 0.2).
"""

import asyncio
import logging
import os
import re
from datetime import timedelta

logger = logging.getLogger("architeq.recordings")

_GCS_URL_RE = re.compile(r"^https://storage\.googleapis\.com/([^/]+)/(.+)$")
_V4_MAX_TTL_S = 7 * 24 * 3600


def _ttl() -> timedelta:
    raw = int(os.environ.get("ARCHITEQ_RECORDING_URL_TTL_SECONDS", _V4_MAX_TTL_S))
    return timedelta(seconds=min(raw, _V4_MAX_TTL_S))


def _sign(bucket: str, obj: str) -> str:
    """Blocking; run via asyncio.to_thread."""
    from google import auth
    from google.auth.transport import requests as ga_requests
    from google.cloud import storage

    credentials, _ = auth.default()
    # refresh() populates token and, on GKE/GCE, the real SA email.
    credentials.refresh(ga_requests.Request())
    blob = storage.Client(credentials=credentials).bucket(bucket).blob(obj)
    return blob.generate_signed_url(
        version="v4",
        expiration=_ttl(),
        service_account_email=credentials.service_account_email,
        access_token=credentials.token,
    )


async def sign_recording_url(url: str | None) -> str | None:
    """Sign GCS object URLs; anything else (or a signing failure) passes through."""
    if not url:
        return url
    m = _GCS_URL_RE.match(url)
    if m is None or "?" in url:  # not GCS, or already signed
        return url
    try:
        return await asyncio.to_thread(_sign, m.group(1), m.group(2))
    except Exception:
        logger.warning("could not sign recording url %s; storing unsigned", url, exc_info=True)
        return url
