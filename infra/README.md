# Architeq infrastructure — bootstrap runbook

GCP + GKE stack: Terraform provisions cloud resources; Helm deploys
monitoring, LiveKit, and the Architeq services.

```
infra/
  terraform/            VPC, GKE, Cloud SQL, Redis, GCS, AR, DNS, IAM
  helm/
    architeq/           umbrella chart: api, worker, dashboard
    livekit/            values for livekit-server + livekit-sip charts
    monitoring/         kube-prometheus-stack values + Grafana dashboards
```

Prereqs: `gcloud`, `terraform >= 1.7`, `helm`, `kubectl`,
`lk` (livekit-cli), a GCP project with billing, and a registrable domain.

## 1. Terraform

```bash
cd infra/terraform

# one-time: state bucket, then uncomment backend.tf
gcloud storage buckets create gs://<PROJECT_ID>-architeq-tfstate \
  --location=us-central1 --uniform-bucket-level-access

terraform init
terraform apply \
  -var project_id=<PROJECT_ID> \
  -var domain=<DOMAIN>            # e.g. architeq.example.com
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
gcloud container clusters get-credentials architeq \
  --region us-central1 --project <PROJECT_ID>
```

## 3. Monitoring (kube-prometheus-stack)

Install monitoring first so the LiveKit/Architeq ServiceMonitor CRDs exist.

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl create namespace monitoring
kubectl -n monitoring create configmap architeq-dashboards \
  --from-file=infra/helm/monitoring/dashboards/ \
  --dry-run=client -o yaml | kubectl apply -f -

helm install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring -f infra/helm/monitoring/values.yaml \
  --set-string grafana.adminPassword='<STRONG_PASSWORD>'
```

Grafana: `kubectl -n monitoring port-forward svc/monitoring-grafana 3001:80`
→ http://localhost:3001 (dashboards land in the "Architeq" folder).

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

## 5. SIP trunks + dispatch rule (lk CLI)

Full detail and JSON payloads: `infra/helm/livekit/README.md`. Summary:

```bash
export LIVEKIT_URL=wss://livekit.<DOMAIN>
export LIVEKIT_API_KEY=APIArchiteqKey
export LIVEKIT_API_SECRET=<secret>

lk sip outbound create outbound-trunk.json   # Telnyx creds + numbers -> ST_...
lk sip inbound  create inbound-trunk.json    # DIDs -> ST_...
lk sip dispatch create dispatch-rule.json    # inbound -> agent_name architeq-agent
lk sip dispatch list
```

Keep the outbound trunk id — architeq-api uses it to place calls
(`ARCHITEQ_SIP_OUTBOUND_TRUNK_ID`, set via `api.env` in the chart values).

### Telnyx checklist

- [ ] Buy/port numbers (E.164)
- [ ] SIP Connection, type FQDN → `sip.<DOMAIN>`:5060 UDP
      (A record → `terraform output sip_ip`)
- [ ] Outbound Voice Profile attached; outbound auth credentials created
      (→ `outbound-trunk.json`)
- [ ] Each number assigned to that SIP connection (inbound routing)
- [ ] AMD (Answering Machine Detection) enabled on the connection with
      result passthrough

## 6. Build & push images

```bash
REGISTRY=$(terraform -chdir=infra/terraform output -raw artifact_registry)
gcloud auth configure-docker us-central1-docker.pkg.dev

docker build -t $REGISTRY/architeq-api:v0.1.0 backend/
docker build -t $REGISTRY/architeq-worker:v0.1.0 worker/
docker build -t $REGISTRY/architeq-dashboard:v0.1.0 \
  --build-arg NEXT_PUBLIC_API_URL=https://api.<DOMAIN> \
  --build-arg NEXT_PUBLIC_GOOGLE_CLIENT_ID=<oauth client id> \
  frontend/   # NEXT_PUBLIC_* are baked in at build time
docker push $REGISTRY/architeq-api:v0.1.0
docker push $REGISTRY/architeq-worker:v0.1.0
docker push $REGISTRY/architeq-dashboard:v0.1.0
```

## 7. Deploy Architeq

Create a private values override (never commit real secrets):

```yaml
# architeq-prod.yaml
global:
  domain: <DOMAIN>
  imageRegistry: <artifact_registry output>
  gcpProjectId: <PROJECT_ID>
config:
  redisUrl: redis://<redis_host>:6379/0
  recordingsGcsBucket: <recordings_bucket>
secrets:
  values:
    ARCHITEQ_DATABASE_URL: <database-url secret value>   # gcloud secrets versions access latest --secret architeq-database-url
    LIVEKIT_API_KEY: APIArchiteqKey
    LIVEKIT_API_SECRET: <secret>
    GOOGLE_API_KEY: <gemini key>
    CARTESIA_API_KEY: <cartesia key>
    ARCHITEQ_INTERNAL_TOKEN: <openssl rand -hex 32>
serviceAccounts:
  api:    { gsaEmail: architeq-api@<PROJECT_ID>.iam.gserviceaccount.com }
  worker: { gsaEmail: architeq-worker@<PROJECT_ID>.iam.gserviceaccount.com }
ingress:
  staticIpName: architeq-web-ip
api:
  image: { tag: v0.1.0 }
  env:
    ARCHITEQ_SIP_OUTBOUND_TRUNK_ID: <ST_... from step 5>
worker:
  image: { tag: v0.1.0 }
dashboard:
  image: { tag: v0.1.0 }
```

```bash
kubectl create namespace architeq   # must match Terraform WI bindings
helm install architeq infra/helm/architeq -n architeq -f architeq-prod.yaml
```

## 8. DNS / TLS

Terraform already created A records (api., dashboard. → global IP; sip. →
SIP LB IP; livekit. → LiveKit LB IP). The GKE ManagedCertificate for
api./dashboard. provisions automatically once DNS resolves (15–60 min);
check with `kubectl -n architeq describe managedcertificate`.

## Smoke test

```bash
curl -s https://api.<DOMAIN>/healthz
open https://dashboard.<DOMAIN>
# outbound test call
curl -s -X POST https://api.<DOMAIN>/v2/create-phone-call \
  -H "Authorization: Bearer <api_key>" -H "Content-Type: application/json" \
  -d '{"from_number":"+1555...","to_number":"+1555...","override_agent_id":"agent_..."}'
```
