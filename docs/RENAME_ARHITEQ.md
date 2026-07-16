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

## Domain migration

The move to **arhiteq.com** is tracked separately — see the domain-migration PR
and `terraform.tfvars` (`domain`, `cloudflare_zone_id`, `cloudflare_api_token`).
