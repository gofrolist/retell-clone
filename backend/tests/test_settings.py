"""Shared-credential env names.

The livekit-agents worker and its plugins hard-read the bare names
(LIVEKIT_API_KEY, GOOGLE_API_KEY, ...), so those are the canonical spelling
for credentials shared across services; the backend accepts them as
fallbacks to its ARHITEQ_-prefixed names. One secret, one env key.
"""

from arhiteq_api.config import Settings


def _settings(**env):
    import os

    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return Settings(_env_file=None)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_bare_shared_credential_names_are_accepted():
    s = _settings(
        LIVEKIT_URL="ws://lk.internal:80",
        LIVEKIT_API_KEY="lk_key",
        LIVEKIT_API_SECRET="lk_secret",
        GOOGLE_API_KEY="g_key",
        RECORDINGS_GCS_BUCKET="bucket-1",
    )
    assert s.livekit_url == "ws://lk.internal:80"
    assert s.livekit_api_key == "lk_key"
    assert s.livekit_api_secret == "lk_secret"
    assert s.google_api_key == "g_key"
    assert s.recordings_gcs_bucket == "bucket-1"


def test_arhiteq_prefixed_names_win_over_bare():
    s = _settings(
        ARHITEQ_LIVEKIT_API_KEY="prefixed",
        LIVEKIT_API_KEY="bare",
    )
    assert s.livekit_api_key == "prefixed"
