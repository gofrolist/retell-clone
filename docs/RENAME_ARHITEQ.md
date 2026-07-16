# Rename: `architeq` → `arhiteq`

`architeq` was a misspelling. Everything — brand, Python packages
(`arhiteq_api`, `arhiteq_worker`), Helm chart, Prometheus metrics, the
`ARHITEQ_` env prefix, and all GCP resource names — is now `arhiteq`.

The **code** rename is a pure text/dir change and is safe to merge: backend,
worker, and frontend build and test green. The **infra** rename changes
physical GCP resource names (all immutable), so it is applied as a **clean
rebuild** — valid here because there is no live prod data/traffic to preserve.

If prod ever holds real data before this is applied, STOP and switch to a
dump/restore migration (Cloud SQL export, tfstate `-migrate-state`, image
re-push) instead of the destroy/recreate below.

## What changed (identifier map)

| Old | New |
| --- | --- |
| GKE cluster `architeq` | `arhiteq` (`var.cluster_name` default) |
| tfstate bucket `usan-retirement-architeq-tfstate` (prefix `architeq`) | `usan-retirement-arhiteq-tfstate` (prefix `arhiteq`) |
| Cloud SQL instance `architeq-pg`, db+role `architeq` | `arhiteq-pg`, db+role `arhiteq` |
| Redis `architeq-redis` | `arhiteq-redis` |
| Recordings bucket `usan-retirement-architeq-recordings` | `usan-retirement-arhiteq-recordings` |
| Artifact Registry repo `architeq` | `arhiteq` |
| Service accounts `architeq-{api,worker,livekit-egress,deployer}` | `arhiteq-*` |
| Secret Manager `architeq-database-url` | `arhiteq-database-url` |
| Static IPs `architeq-{web,livekit,sip}-ip` | `arhiteq-*-ip` |
| Prometheus metrics `architeq_*` | `arhiteq_*` (dashboards renamed to match; old series are not carried over) |
| Env prefix `ARCHITEQ_` | `ARHITEQ_` (config still accepts bare, unprefixed keys via `AliasChoices`) |
| Helm release/namespace/chart `architeq` | `arhiteq` |

Operator-local files under `infra/private/` (gitignored) were renamed too:
`arhiteq-prod.yaml`, `gen-arhiteq-prod.sh`, and all `ARHITEQ_*` keys in
`prod.env`.

## Clean-rebuild runbook (infra)

Run from `infra/terraform/` unless noted.

1. **Tear down the old stack** (skip if nothing was ever applied):
   ```
   terraform destroy    # uses the current architeq state/backend
   ```
2. **Create the new tfstate bucket** (out of band, matches `backend.tf`):
   ```
   gcloud storage buckets create gs://usan-retirement-arhiteq-tfstate \
     --location=us-central1 --uniform-bucket-level-access
   ```
3. **Fresh init + apply** under the new names:
   ```
   terraform init      # picks up the new backend bucket/prefix
   terraform apply      # creates arhiteq-* cluster, SQL, SAs, IPs, registry
   ```
4. **Deploy images + chart** — the release pipeline pushes to
   `us-east1-docker.pkg.dev/usan-retirement/arhiteq/*` and runs
   `helm upgrade arhiteq infra/helm/arhiteq -n arhiteq -f infra/private/arhiteq-prod.yaml`.
   The GKE credentials step targets cluster `arhiteq`.
5. **Re-key nothing special** — regenerate the prod secret from `prod.env`:
   ```
   infra/private/gen-arhiteq-prod.sh
   helm upgrade arhiteq infra/helm/arhiteq -n arhiteq -f infra/private/arhiteq-prod.yaml
   ```
6. **Delete leftover old resources** not caught by `destroy` (old registry repo
   `architeq`, old recordings bucket, old tfstate bucket, orphaned SAs).

The GitHub WIF deployer is recreated as `arhiteq-deployer`; confirm the repo's
`deploy.yml` OIDC binding resolves (keyless auth, no key to rotate).

## Domain migration → arhiteq.com

Replaces `usanretirement.com`. arhiteq.com is registered at Cloudflare
(status Active — nameservers already authoritative there, no NS change needed).
DNS is managed by Terraform against the Cloudflare zone (`dns_zone_create=false`);
records are written `proxied=false` (WebRTC UDP / SIP / GCP HTTP-01 can't pass
the CF proxy).

New records (A, → the Terraform-created static IPs): `api`, `dashboard`,
`livekit` → web/livekit global IPs; `sip` → regional SIP IP.

Operator-local values (gitignored) already updated on disk:
- `terraform.tfvars`: `domain = "arhiteq.com"`, `cloudflare_zone_id = 1cba2f52cfd926e699e2875a3c58cdde`
- `infra/private/arhiteq-prod.yaml` (`domain`, `corsOrigins`), `livekit-managed-cert.yaml`, `livekit-server-values.yaml`

Committed: `deploy.yml` `NEXT_PUBLIC_API_URL=https://api.arhiteq.com` (baked into
the dashboard image at build), plus doc/comment examples.

### Cutover steps

1. **Cloudflare API token** — done: the existing "usan custom access" token was
   re-scoped to cover the arhiteq.com zone (DNS:Edit), so `terraform.tfvars`
   `cloudflare_api_token` already authorizes writes to this zone.
2. `terraform apply` — writes the api/dashboard/livekit/sip A records to the
   Cloudflare zone and provisions the GCP managed certs for `*.arhiteq.com`.
3. Wait for the GCE managed certificates to go `ACTIVE` (HTTP-01, can take
   10–60 min after the A records resolve).
4. Release/deploy so the dashboard image carries `NEXT_PUBLIC_API_URL=https://api.arhiteq.com`.
5. Verify `https://api.arhiteq.com/health`, dashboard login, and a test call
   (SIP/LiveKit) end to end.
6. Google OAuth: add `https://dashboard.arhiteq.com` to Authorized JS origins /
   redirect URIs. Update the workspace webhook/consumer base URLs to arhiteq.com.
7. Decommission the old usanretirement.com records once traffic has moved.
