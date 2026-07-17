from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _shared(name: str) -> AliasChoices:
    """Credentials shared with the worker keep one canonical env name.

    The livekit-agents plugins hard-read the bare names (LIVEKIT_API_KEY,
    GOOGLE_API_KEY, ...), so the backend accepts them as fallbacks to its
    ARHITEQ_-prefixed names — one secret, one env key in deployments.
    """
    return AliasChoices(f"ARHITEQ_{name}", name)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARHITEQ_",
        env_file=".env",
        extra="ignore",
        # Aliased fields must stay constructible by field name
        # (Settings(google_api_key=...) in tests).
        populate_by_name=True,
    )

    database_url: str = "postgresql+asyncpg://arhiteq:arhiteq@localhost:5432/arhiteq"

    # LiveKit control plane
    livekit_url: str = Field("http://localhost:7880", validation_alias=_shared("LIVEKIT_URL"))
    livekit_api_key: str = Field("devkey", validation_alias=_shared("LIVEKIT_API_KEY"))
    livekit_api_secret: str = Field("devsecret", validation_alias=_shared("LIVEKIT_API_SECRET"))
    # Outbound SIP trunk id (LiveKit SIP -> Telnyx); created by infra bootstrap
    # (env: ARHITEQ_SIP_OUTBOUND_TRUNK_ID, matching the Helm chart).
    sip_outbound_trunk_id: str = ""

    # Google GenAI (Gemini) for post-call analysis.
    # In prod the worker authenticates to Vertex via ADC (workload identity),
    # not an API key; the analysis client mirrors that when use_vertexai is set
    # (env GOOGLE_GENAI_USE_VERTEXAI). GOOGLE_CLOUD_{PROJECT,LOCATION} are read
    # by google-genai directly. Gemini 3.x models are global-endpoint only, so
    # the location must be `global`, not a region.
    google_api_key: str = Field("", validation_alias=_shared("GOOGLE_API_KEY"))
    google_genai_use_vertexai: bool = Field(
        False,
        validation_alias=AliasChoices(
            "ARHITEQ_GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_VERTEXAI"
        ),
    )
    analysis_model: str = "gemini-3.1-flash-lite"

    # Webhook delivery
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 3
    inbound_webhook_timeout_seconds: float = 9.5

    # Recordings
    recordings_gcs_bucket: str = Field("", validation_alias=_shared("RECORDINGS_GCS_BUCKET"))

    # Public base URL of this API (e.g. https://api.arhiteq.com), used
    # to build absolute preview_audio_url links; empty = relative /static/...
    public_api_url: str = ""

    # ── Security ────────────────────────────────────────────────────────────
    # Browser origins allowed to call the API (the dashboard).
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3100"]
    # Outbound webhook / inbound-router URLs must resolve to public addresses
    # unless this is set (local development against localhost consumers).
    allow_private_webhooks: bool = False
    # Per-API-key rate limit for the public API (requests per minute; 0 = off).
    rate_limit_rpm: int = 300
    # Number of trusted reverse proxies in front of the API. When >0, the
    # client IP for rate limiting is read from X-Forwarded-For at this depth
    # from the right; 0 means trust only the socket peer (ignore XFF).
    trusted_proxy_count: int = 0

    # ── Dashboard auth (Google Sign-In) ────────────────────────────────────
    # OAuth client id from Google Cloud Console (Web application type).
    google_oauth_client_id: str = ""
    # HS256 key for Arhiteq session JWTs. MUST be set in production.
    session_secret: str = ""
    session_ttl_seconds: int = 12 * 3600
    # Who may log into the dashboard: exact emails and/or whole domains.
    # Workspace members (created via invites) may log in regardless.
    dashboard_allowed_emails: list[str] = []
    dashboard_allowed_domains: list[str] = []
    # How long a workspace invite link stays redeemable.
    invite_ttl_hours: int = 168


@lru_cache
def get_settings() -> Settings:
    return Settings()


def public_base_url() -> str:
    """Public API base for absolute links; empty string → relative URLs."""
    return get_settings().public_api_url.rstrip("/")
