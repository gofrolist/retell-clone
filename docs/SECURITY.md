# Architeq security model

## Authentication surfaces

| Surface | Mechanism |
|---|---|
| Public API (Retell-compatible) | `Authorization: Bearer <api_key>` — keys stored as SHA-256 hashes for lookup; plaintext copy kept only for webhook HMAC signing (Retell semantics: the API key IS the signing key). Encrypt at rest via Cloud KMS/Secret Manager in production. |
| Dashboard | Google Sign-In (Google Identity Services). `POST /auth/google` verifies the Google ID token (signature, expiry, audience = `ARCHITEQ_GOOGLE_OAUTH_CLIENT_ID`, issuer, `email_verified`), enforces the allowlist, and issues an HS256 session JWT (`ARCHITEQ_SESSION_SECRET`, 12h TTL). Sessions are accepted anywhere an API key is, resolving to the workspace's API key. |
| Worker ⇄ API | `X-Internal-Token` shared secret, constant-time compare; `/internal/*` never exposed on the public ingress. |
| Agent tool calls | `X-Caller-Secret: <ARCHITEQ_FUNCTION_SECRET>` header on every custom-function call (consumer verifies constant-time). |
| Outbound webhooks | `x-retell-signature: v={ms},d={hex hmac_sha256(rawBody+ts, api_key)}`, re-signed per retry; consumers enforce a 5-minute replay window. |

## Dashboard login allowlist

Fail-closed: with no `ARCHITEQ_DASHBOARD_ALLOWED_EMAILS` /
`ARCHITEQ_DASHBOARD_ALLOWED_DOMAINS` configured, nobody can log in. Exact
email match or exact domain match only (no suffix tricks).

## SSRF protection

Customer-supplied URLs (agent/workspace webhook URLs, phone-number inbound
webhooks) are only fetched after `architeq_api/security.py:assert_url_safe` confirms
they are http(s) and resolve exclusively to public addresses — blocking
loopback, RFC1918, link-local, and the GCP metadata server (169.254.169.254).
Dev escape hatch: `ARCHITEQ_ALLOW_PRIVATE_WEBHOOKS=true`.

## Rate limiting & headers

- Per-credential sliding-window rate limit on the public API
  (`ARCHITEQ_RATE_LIMIT_RPM`, default 300/min; `/internal`, `/healthz`,
  `/metrics` exempt). In-memory per pod — switch to Redis if exact global
  limits are required.
- Security headers on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, HSTS on https.
- CORS is an allowlist (`ARCHITEQ_CORS_ORIGINS`), not `*`.

## Secrets inventory

| Secret | Where | Notes |
|---|---|---|
| Workspace API keys | Postgres (`api_keys`) | hash + signing copy; managed via `/list-api-keys` (masked prefix only), `/create-api-key` (secret returned exactly once), `/revoke-api-key/{id}` — workspace-scoped, same auth as the public API |
| `ARCHITEQ_SESSION_SECRET` | K8s Secret | rotate to invalidate all dashboard sessions |
| `ARCHITEQ_INTERNAL_TOKEN` | K8s Secret | api + worker |
| `ARCHITEQ_FUNCTION_SECRET` | K8s Secret | = consumer's `RETELL_FUNCTION_SECRET` |
| LiveKit / Cartesia / Google keys | K8s Secret | via Secret Manager + ESO in prod |

`/metrics` and `/healthz` expose no tenant data. Logs never include API keys
or session tokens.

## Public static assets

`GET /static/voice_previews/*.mp3` is the platform's first unauthenticated
public content mount: committed, non-tenant voice preview audio served
without auth by design so the dashboard's `<audio>` element and any API
consumer can play previews. It still passes through the per-IP rate-limit
and security-headers middleware like every other route.
