# Arhiteq infrastructure — bootstrap runbook

GCP + GKE stack: Terraform provisions cloud resources; Helm deploys
monitoring, LiveKit, and the Arhiteq services.

```
infra/
  terraform/            VPC, GKE, Cloud SQL, Redis, GCS, AR, DNS, IAM
  helm/
    arhiteq/           umbrella chart: api, worker, dashboard
    livekit/            values for livekit-server + livekit-sip charts
    monitoring/         kube-prometheus-stack values + Grafana dashboards
  private/              operator-only prod config & secrets — GITIGNORED
```

## `infra/private/` — operator secrets (gitignored, never committed)

Real deployment values live in `infra/private/`, which `.gitignore` excludes
entirely. `prod.env` is the single operator secrets file — every credential
lives there and nowhere else. Alongside it: the generated `arhiteq-prod.yaml`
(Helm secrets override), LiveKit server/SIP/egress values, SIP trunk JSONs
(Telnyx credentials), the `CUTOVER.md` runbook, and `gen-arhiteq-prod.sh`,
which regenerates `arhiteq-prod.yaml` from `prod.env` + terraform outputs +
Secret Manager after a `terraform apply`.

Because these files are gitignored they are NOT versioned or backed up by
git: `git clean -fdx` will delete them, and a fresh clone will not have them.
Keep an off-machine backup of `prod.env` — the values the generator derives
from Secret Manager and terraform state are recoverable, the ones in
`prod.env` are not. `.dockerignore` in each app excludes `.env*` so env files
can never leak into a docker build context.

Prereqs: `gcloud`, `terraform >= 1.7`, `helm`, `kubectl`,
`lk` (livekit-cli), a GCP project with billing, and a registrable domain.

## 1. Terraform

```bash
cd infra/terraform

# one-time: state bucket, then uncomment backend.tf
gcloud storage buckets create gs://<PROJECT_ID>-arhiteq-tfstate \
  --location=us-central1 --uniform-bucket-level-access

terraform init
terraform apply \
  -var project_id=<PROJECT_ID> \
  -var domain=<DOMAIN>            # e.g. arhiteq.example.com
```

If the zone is new, delegate `<DOMAIN>` at your registrar to the name
servers from `terraform output dns_name_servers`.

Capture outputs you will need below:

```bash
terraform output   # redis_host, db_private_ip, sip_ip, web_ip_name,
                   # artifact_registry, recordings_bucket, ...
```

## 2. Cluster credentials

```bash
gcloud container clusters get-credentials arhiteq \
  --region us-central1 --project <PROJECT_ID>
```

## 3. Monitoring (kube-prometheus-stack)

Install monitoring first so the LiveKit/Arhiteq ServiceMonitor CRDs exist.

```bash
infra/helm/monitoring/gen-values.sh
```

That is the whole install, and it is re-runnable. It reads
`infra/private/prod.env`, applies the Alertmanager bot-token Secret and the
dashboards ConfigMap, renders `infra/private/monitoring-values.yaml` from
`infra/helm/monitoring/values.yaml` (filling the `CHANGE_ME_*` placeholders
and `TELEGRAM_CHAT_ID`), `helm upgrade --install`s the pinned chart version,
then applies the alert rules and the egress PodMonitor. Keys it needs in
`prod.env`:

| key | what |
| --- | --- |
| `GRAFANA_ADMIN_PASSWORD` | owns the provisioned dashboards; no longer logs in (see § Grafana access) |
| `GRAFANA_HOST` | public hostname, `grafana.<domain>` |
| `GRAFANA_OAUTH_CLIENT_ID` | Google OAuth web client — see § Grafana access |
| `GRAFANA_OAUTH_CLIENT_SECRET` | same client's secret; never enters a values file |
| `GRAFANA_ALLOWED_EMAILS` | space/comma separated; exactly who may sign in |
| `TELEGRAM_BOT_TOKEN` | from @BotFather; never enters a values file |
| `TELEGRAM_CHAT_ID` | negative for groups/channels, positive for a DM |

Nothing about monitoring is deployed by CI — `deploy.yml` only upgrades the
`arhiteq` release. This script is how the monitoring stack changes.

### Grafana access

Grafana is published at `https://grafana.<domain>` behind Google sign-in. It
has its own GCE Ingress, static IP (`<cluster>-grafana-ip`) and
ManagedCertificate rather than a rule on the arhiteq Ingress: GCE Ingress
backends are namespace-local and Grafana lives in `monitoring`, and two GCE
Ingresses cannot share one address. That is a second HTTPS load balancer's
worth of cost.

Google is the *only* way in: `disable_login_form = true` takes the
username/password form off the login page, and `[auth.basic] enabled = false`
closes the half that hiding the form leaves open — with basic auth on,
`curl -u admin:<password> https://grafana.<domain>/api/...` keeps working from
the internet however the login page looks. The admin password stays configured
because the provisioned dashboards are owned by that user, but nothing accepts
it any more.

That leaves no password to fall back on if Google is what breaks — an expired
client, a revoked secret, a bad allowlist edit. Recovery is to put one of the
two switches back in `values.yaml` and re-run `gen-values.sh`, so it needs
cluster access rather than a password:

```yaml
    auth:
      disable_login_form: false
```

Who gets in is `GRAFANA_ALLOWED_EMAILS`, rendered into a JMESPath
`role_attribute_path` that maps a listed address to `Admin` and everything
else to no role at all; `role_attribute_strict = true` turns "no role" into a
refused login. The usual `allowed_domains`/`hosted_domain` filter is not used
on purpose — it is only meaningful for a Workspace domain, and against
`@gmail.com` it would admit every Google account there is.

One-time setup of the OAuth client (console only — gcloud cannot create a
plain web client):

1. APIs & Services → Credentials → Create credentials → OAuth client ID →
   Web application.
2. Authorized JavaScript origin: `https://grafana.<domain>`.
   Authorized redirect URI: `https://grafana.<domain>/login/google` — Grafana
   builds it from `root_url`, and a mismatch is a `redirect_uri_mismatch`
   error at login.
3. If the consent screen is External and still in Testing, add every address
   in `GRAFANA_ALLOWED_EMAILS` as a test user or Google blocks them before
   Grafana ever sees the login.
4. Put the id and secret in `prod.env`, then re-run `gen-values.sh`.

The managed certificate needs `grafana.<domain>` already resolving to the
static IP, and takes 15–60 minutes to go Active on first provision:

```bash
kubectl -n monitoring get managedcertificate grafana-cert \
  -o jsonpath='{.status.certificateStatus}{"\n"}'
```

The port-forward still reaches Grafana while the certificate provisions, but
it lands on the same Google-only login page — and Google will refuse to
redirect back to `localhost:3001`, since the OAuth client only authorizes
`https://grafana.<domain>/login/google`. It is useful for `/api/health` and
for reading logs, not for signing in.

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3001:80
```

Dashboards land in the "Arhiteq" folder.

### Alerting

Alert rules live in `infra/helm/monitoring/rules/`; Alertmanager delivers them
to Telegram. Two severities, one destination, different urgency: `critical`
groups after 10s and repeats hourly, everything else groups after 30s and
repeats every 12h. A critical alert inhibits the warnings sharing its name and
namespace, so one broken thing sends one message.

The `Watchdog` alert — the chart's always-firing heartbeat — is routed to the
null receiver. It is there to prove the pipeline works to an *external*
watcher; sending it to the same Telegram chat would prove nothing and notify
forever.

Setting up the bot:

```bash
# 1. Create the bot: message @BotFather, /newbot, keep the token.
# 2. Send the bot any message (or add it to a group and post there) so a
#    chat exists — a bot cannot open a conversation itself.
# 3. Find the chat id:
source infra/private/prod.env
curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getUpdates" \
  | jq '.result[].message.chat | {id, type, title, username}'
# 4. Put both in infra/private/prod.env, then re-run gen-values.sh.
```

`getUpdates` only returns recent, undelivered updates, so if it comes back
empty just message the bot again and retry.

The token reaches Alertmanager as `bot_token_file`, pointing at the mounted
Secret — it is never in the values file, the Helm release, or `helm get
values` output. Rotating it means updating `prod.env`, re-running the script,
and deleting the Alertmanager pod: the kubelet refreshes a projected volume
lazily, so the old token can linger for a minute.

Sending a test alert without waiting for something to break:

```bash
kubectl -n monitoring port-forward svc/monitoring-alertmanager 9093:9093
curl -s -XPOST http://localhost:9093/api/v2/alerts -H 'Content-Type: application/json' -d '[{
  "labels": {"alertname":"ArhiteqTestAlert","severity":"critical","namespace":"arhiteq"},
  "annotations": {"summary":"Test alert","description":"Delivery check, ignore."}
}]'
```

An alert posted this way keeps re-firing until it expires (default 5m) or you
resolve it by re-posting with an `endsAt` in the past.

Checking what is actually scraped:

```bash
kubectl -n monitoring port-forward svc/monitoring-prometheus 9090:9090
# http://localhost:9090/targets — every arhiteq-*, livekit-*, egress and
# livekit-sip job should be up. A job missing entirely means its
# ServiceMonitor did not render; see the livekit note in section 4.
```

## 4. LiveKit server + SIP

Edit `infra/helm/livekit/*.yaml`: replace `REDIS_HOST`
(`terraform output redis_host`), `SIP_STATIC_IP`
(`terraform output sip_ip`) and generate the API secret
(`openssl rand -hex 32`). Then:

```bash
helm repo add livekit https://helm.livekit.io
helm repo update

kubectl create namespace livekit
kubectl -n livekit apply -f infra/helm/livekit/livekit-managed-cert.yaml
helm install livekit-server livekit/livekit-server \
  -n livekit -f infra/helm/livekit/livekit-server-values.yaml
helm install livekit-sip infra/helm/livekit/sip \
  -n livekit -f infra/helm/livekit/livekit-sip-values.yaml
```

The upstream server chart gates its ServiceMonitor on `livekit.prometheus_port`
being set, not on `serviceMonitor.create` alone: set `create: true` without the
port and it renders nothing, silently — that is how LiveKit went unscraped from
the first install until 2026-08-20. The in-repo SIP chart gates on
`serviceMonitor.create` alone and fails loudly instead (an unset
`prometheusPort` renders an empty `port:`, which the apply rejects). Confirm
with `kubectl get servicemonitor,podmonitor -n livekit` after installing.

The SIP chart also drops any `livekit_sip_*` series whose `to` label is not one
of our own endpoints — SIP scanners hit 5060 with arbitrary values and an
unbounded label is how a Prometheus falls over. `service.loadBalancerIP` is
allowlisted automatically; anything else goes in `serviceMonitor.knownSipHosts`.
Note `to` is a *host as dialled*, which in practice is an IP, not the
`sip.<domain>` FQDN — Telnyx resolves that before it sends. Getting the list
wrong makes inbound series cease to exist rather than read zero, so
`LiveKitSIPInboundSeriesMissing` watches for exactly that.

## 5. SIP trunks + dispatch rule (lk CLI)

Full detail and JSON payloads: `infra/helm/livekit/README.md`. Summary:

```bash
export LIVEKIT_URL=wss://livekit.<DOMAIN>
export LIVEKIT_API_KEY=APIArhiteqKey
export LIVEKIT_API_SECRET=<secret>

lk sip outbound create outbound-trunk.json   # Telnyx creds + numbers -> ST_...
lk sip inbound  create inbound-trunk.json    # DIDs -> ST_...
lk sip dispatch create dispatch-rule.json    # inbound -> agent_name arhiteq-agent
lk sip dispatch list
```

Keep the outbound trunk id — arhiteq-api uses it to place calls
(`ARHITEQ_SIP_OUTBOUND_TRUNK_ID`, set via `api.env` in the chart values).

### Telnyx checklist

- [ ] Buy/port numbers (E.164)
- [ ] SIP Connection, type FQDN → `sip.<DOMAIN>`:5060 UDP
      (A record → `terraform output sip_ip`)
- [ ] Outbound Voice Profile attached; outbound auth credentials created
      (→ `outbound-trunk.json`)
- [ ] Each number assigned to that SIP connection (inbound routing)
- [ ] AMD (Answering Machine Detection) enabled on the connection with
      result passthrough

## Releasing (normal path)

Releases are automated — never bump image tags by hand:

1. Merge PRs to `main` with conventional-commit titles (`feat: …`, `fix: …`;
   enforced by the `pr-title` check).
2. release-please maintains a release PR that accumulates `CHANGELOG.md`.
   Merging it tags `vX.Y.Z` and publishes a GitHub release.
3. The release triggers `.github/workflows/deploy.yml`: builds + pushes all
   three images at `vX.Y.Z`, then
   `helm upgrade arhiteq --reuse-values --set …image.tag=vX.Y.Z --rollback-on-failure`.

Redeploy/rollback: run the Deploy workflow manually (workflow_dispatch) with
any existing release tag.

Caveat: `--reuse-values` re-renders the chart with the values already in the
cluster and does NOT merge in new defaults from `values.yaml`. A key added in
the same release is therefore undefined when CI deploys it — v0.24.0 failed
exactly this way (`spec.timeoutSec: Invalid value: "null"`, rolled back
automatically). So:
- Non-secret values: give the template a literal default —
  `{{ .Values.api.backendTimeoutSec | default 2400 }}` — and the deploy works
  on the first try. Adding the key to `values.yaml` alone is not enough.
- Values that can't have a default (a NEW secret) must first be applied locally
  once:
  `helm upgrade arhiteq infra/helm/arhiteq -n arhiteq -f infra/private/arhiteq-prod.yaml --reuse-values`.
Image tags are owned by CI: keep `image.tag` pins OUT of
`infra/private/arhiteq-prod.yaml`, or `-f` will override the CI-set tags
with stale ones.

One-time setup (already done, recorded for rebuild-from-scratch):

- `terraform apply` creates the WIF pool/provider + `arhiteq-deployer` SA
  (`infra/terraform/github-deploy.tf`).
- Repo variables: `GCP_WIF_PROVIDER` / `GCP_DEPLOYER_SA` (from the terraform
  outputs `deploy_workload_identity_provider` / `deploy_service_account`) and
  `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (OAuth client id, public by design).
- Repo secret `RELEASE_PLEASE_TOKEN`: fine-grained PAT scoped to this repo,
  permissions Contents: read/write + Pull requests: read/write. Needed
  because events created with the default `GITHUB_TOKEN` don't trigger
  workflows (CI on the release PR, deploy on release publish).
- `pr-title` added to the required status checks on `main`.

## 6. Build & push images (manual / break-glass)

```bash
REGISTRY=$(terraform -chdir=infra/terraform output -raw artifact_registry)
gcloud auth configure-docker us-east1-docker.pkg.dev

docker build -t $REGISTRY/arhiteq-api:v0.1.0 backend/
docker build -t $REGISTRY/arhiteq-worker:v0.1.0 worker/
docker build -t $REGISTRY/arhiteq-dashboard:v0.1.0 \
  --build-arg NEXT_PUBLIC_API_URL=https://api.<DOMAIN> \
  --build-arg NEXT_PUBLIC_GOOGLE_CLIENT_ID=<oauth client id> \
  frontend/   # NEXT_PUBLIC_* are baked in at build time
docker push $REGISTRY/arhiteq-api:v0.1.0
docker push $REGISTRY/arhiteq-worker:v0.1.0
docker push $REGISTRY/arhiteq-dashboard:v0.1.0
```

## 7. Deploy Arhiteq (manual / break-glass)

Create a private values override in `infra/private/` (gitignored — never
commit real secrets), or generate it with `infra/private/gen-arhiteq-prod.sh`:

```yaml
# infra/private/arhiteq-prod.yaml
global:
  domain: <DOMAIN>
  imageRegistry: <artifact_registry output>
  gcpProjectId: <PROJECT_ID>
config:
  redisUrl: redis://<redis_host>:6379/0
  recordingsGcsBucket: <recordings_bucket>
secrets:
  values:
    ARHITEQ_DATABASE_URL: <database-url secret value>   # gcloud secrets versions access latest --secret arhiteq-database-url
    LIVEKIT_API_KEY: APIArhiteqKey
    LIVEKIT_API_SECRET: <secret>
    GOOGLE_API_KEY: <gemini key>
    CARTESIA_API_KEY: <cartesia key>
    ARHITEQ_INTERNAL_TOKEN: <openssl rand -hex 32>
serviceAccounts:
  api:    { gsaEmail: arhiteq-api@<PROJECT_ID>.iam.gserviceaccount.com }
  worker: { gsaEmail: arhiteq-worker@<PROJECT_ID>.iam.gserviceaccount.com }
ingress:
  staticIpName: arhiteq-web-ip
api:
  image: { tag: v0.1.0 }
  env:
    ARHITEQ_SIP_OUTBOUND_TRUNK_ID: <ST_... from step 5>
worker:
  image: { tag: v0.1.0 }
dashboard:
  image: { tag: v0.1.0 }
```

```bash
kubectl create namespace arhiteq   # must match Terraform WI bindings
helm install arhiteq infra/helm/arhiteq -n arhiteq -f infra/private/arhiteq-prod.yaml
```

## 8. DNS / TLS

Terraform already created A records (api., dashboard. → global IP; sip. →
SIP LB IP; livekit. → LiveKit LB IP). The GKE ManagedCertificate for
api./dashboard. provisions automatically once DNS resolves (15–60 min);
check with `kubectl -n arhiteq describe managedcertificate`.

## Smoke test

```bash
curl -s https://api.<DOMAIN>/healthz
open https://dashboard.<DOMAIN>
# outbound test call
curl -s -X POST https://api.<DOMAIN>/v2/create-phone-call \
  -H "Authorization: Bearer <api_key>" -H "Content-Type: application/json" \
  -d '{"from_number":"+1555...","to_number":"+1555...","override_agent_id":"agent_..."}'
```
