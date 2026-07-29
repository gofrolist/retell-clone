# Arhiteq security model

## Authentication surfaces

| Surface | Mechanism |
|---|---|
| Public API (Retell-compatible) | `Authorization: Bearer <api_key>` — keys stored as SHA-256 hashes for lookup; plaintext copy kept only for webhook HMAC signing (Retell semantics: the API key IS the signing key). Encrypt at rest via Cloud KMS/Secret Manager in production. |
| Dashboard | Google Sign-In (Google Identity Services). `POST /auth/google` verifies the Google ID token (signature, expiry, audience = `ARHITEQ_GOOGLE_OAUTH_CLIENT_ID`, issuer, `email_verified`), enforces the allowlist, and issues an HS256 session JWT (`ARHITEQ_SESSION_SECRET`, 12h TTL). Sessions are accepted anywhere an API key is, resolving to the workspace's API key. |
| Worker ⇄ API | `X-Internal-Token` shared secret, constant-time compare; `/internal/*` never exposed on the public ingress. |
| Agent tool calls | `X-Caller-Secret: <ARHITEQ_FUNCTION_SECRET>` header on every custom-function call (consumer verifies constant-time). |
| Outbound webhooks | `x-retell-signature: v={ms},d={hex hmac_sha256(rawBody+ts, api_key)}`, re-signed per retry; consumers enforce a 5-minute replay window. |

## Dashboard login allowlist

Fail-closed: with no `ARHITEQ_DASHBOARD_ALLOWED_EMAILS` /
`ARHITEQ_DASHBOARD_ALLOWED_DOMAINS` configured, nobody can log in. Exact
email match or exact domain match only (no suffix tricks).

## Workspaces and roles

A dashboard user (identified by their Google-verified email) can belong to
several workspaces. The active one is the session JWT's `ws` claim, so
`POST /switch-workspace` and `POST /create-workspace` work by re-issuing the
token — there is no client-supplied workspace header to forge, and switching
re-checks the `workspace_members` row rather than trusting the old token, so a
removed member can't hop back in with a stale session. Those two endpoints plus
`/list-workspaces` authenticate on identity alone (not `require_api_key`), so
they still work when the active workspace has no key or was just deleted.

Roles are `owner | admin | member`, enforced at three gates: API-key
management and member/invite management and workspace deletion require
owner/admin; only an owner grants or revokes `owner`; and a workspace always
keeps at least one owner (the last one can't be demoted or removed). Nobody
can change their own role — otherwise an admin self-promotes in one call.
`GET /list-roles` returns the catalog the dashboard renders; keep its wording
in step with what the code actually enforces.

## SSRF protection

Customer-supplied URLs (agent/workspace webhook URLs, phone-number inbound
webhooks) are only fetched after `arhiteq_api/security.py:assert_url_safe` confirms
they are http(s) and resolve exclusively to public addresses — blocking
loopback, RFC1918, link-local, and the GCP metadata server (169.254.169.254).
Dev escape hatch: `ARHITEQ_ALLOW_PRIVATE_WEBHOOKS=true`.

## Rate limiting & headers

- Per-credential sliding-window rate limit on the public API
  (`ARHITEQ_RATE_LIMIT_RPM`, default 300/min; `/internal`, `/healthz`,
  `/metrics` exempt). In-memory per pod — switch to Redis if exact global
  limits are required.
- Security headers on every response: `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, HSTS on https.
- CORS is an allowlist (`ARHITEQ_CORS_ORIGINS`), not `*`.

## Secrets inventory

| Secret | Where | Notes |
|---|---|---|
| Workspace API keys | Postgres (`api_keys`) | hash + signing copy; workspace-scoped. `/list-api-keys` (masked prefix only) is open to any member; `/create-api-key` (secret returned exactly once) and `/revoke-api-key/{id}` require **owner/admin** — a raw key skips every role check, so minting one is an operator action |
| `ARHITEQ_SESSION_SECRET` | K8s Secret | rotate to invalidate all dashboard sessions |
| `ARHITEQ_INTERNAL_TOKEN` | K8s Secret | api + worker |
| `ARHITEQ_FUNCTION_SECRET` | K8s Secret | = consumer's `RETELL_FUNCTION_SECRET` |
| LiveKit / Cartesia / Google keys | K8s Secret | via Secret Manager + ESO in prod |

`/metrics` and `/healthz` expose no tenant data. Logs never include API keys
or session tokens.

## Public static assets

`GET /static/voice_previews/*.mp3` is the platform's first unauthenticated
public content mount: committed, non-tenant voice preview audio served
without auth by design so the dashboard's `<audio>` element and any API
consumer can play previews. It still passes through the per-IP rate-limit
and security-headers middleware like every other route.
