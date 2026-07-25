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
import threading
from datetime import timedelta

logger = logging.getLogger("arhiteq.recordings")

_GCS_URL_RE = re.compile(r"^https://storage\.googleapis\.com/([^/]+)/(.+)$")
_V4_MAX_TTL_S = 7 * 24 * 3600


def _ttl() -> timedelta:
    raw = int(os.environ.get("ARHITEQ_RECORDING_URL_TTL_SECONDS", _V4_MAX_TTL_S))
    return timedelta(seconds=min(raw, _V4_MAX_TTL_S))


# Cached across calls: auth.default() re-runs credential discovery and
# refresh() is a live round trip to the GKE metadata server, so building both
# per signed URL costs two network hops for a token that is valid ~1 hour.
#
# _sign runs under asyncio.to_thread, so this is genuinely concurrent. The lock
# keeps a half-built cache from being observed: publishing the credentials
# before the client exists would let another thread sign with storage_client
# None, and a failure in storage.Client() would leave that state stuck for the
# life of the process (sign_recording_url swallows the error and the caller
# persists the *unsigned* URL, so the recording link breaks permanently).
_signer_lock = threading.Lock()
_credentials = None
_storage_client = None


def _signer():
    """Blocking; callers must already be off the event loop."""
    global _credentials, _storage_client
    from google import auth
    from google.auth.transport import requests as ga_requests
    from google.cloud import storage

    with _signer_lock:
        if _credentials is None:
            credentials, _ = auth.default()
            storage_client = storage.Client(credentials=credentials)
            # Publish both together, only once both exist.
            _credentials, _storage_client = credentials, storage_client
        if not _credentials.valid:
            # refresh() populates token and, on GKE/GCE, the real SA email.
            _credentials.refresh(ga_requests.Request())
        return _credentials, _storage_client


def _sign(bucket: str, obj: str) -> str:
    """Blocking; run via asyncio.to_thread."""
    credentials, storage_client = _signer()
    blob = storage_client.bucket(bucket).blob(obj)
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
