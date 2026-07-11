from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARCHITEQ_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://architeq:architeq@localhost:5432/architeq"
    redis_url: str = "redis://localhost:6379/0"

    # LiveKit control plane
    livekit_url: str = "http://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "devsecret"
    # Outbound SIP trunk id (LiveKit SIP -> Telnyx); created by infra bootstrap
    # (env: ARCHITEQ_SIP_OUTBOUND_TRUNK_ID, matching the Helm chart).
    sip_outbound_trunk_id: str = ""

    # Google GenAI (Gemini) for post-call analysis
    google_api_key: str = ""
    analysis_model: str = "gemini-2.5-flash"

    # Webhook delivery
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 3
    inbound_webhook_timeout_seconds: float = 9.5

    # Recordings
    recordings_gcs_bucket: str = ""
    recording_url_ttl_seconds: int = 60 * 60 * 24 * 30

    metrics_enabled: bool = True

    # ── Security ────────────────────────────────────────────────────────────
    # Browser origins allowed to call the API (the dashboard).
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:3100"]
    # Outbound webhook / inbound-router URLs must resolve to public addresses
    # unless this is set (local development against localhost consumers).
    allow_private_webhooks: bool = False
    # Per-API-key rate limit for the public API (requests per minute; 0 = off).
    rate_limit_rpm: int = 300

    # ── Dashboard auth (Google Sign-In) ────────────────────────────────────
    # OAuth client id from Google Cloud Console (Web application type).
    google_oauth_client_id: str = ""
    # HS256 key for Architeq session JWTs. MUST be set in production.
    session_secret: str = ""
    session_ttl_seconds: int = 12 * 3600
    # Who may log into the dashboard: exact emails and/or whole domains.
    dashboard_allowed_emails: list[str] = []
    dashboard_allowed_domains: list[str] = []


@lru_cache
def get_settings() -> Settings:
    return Settings()
