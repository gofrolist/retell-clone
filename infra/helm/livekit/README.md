# LiveKit server + SIP + egress on GKE

Self-hosted LiveKit server (media/rooms), livekit-sip (Telnyx PSTN
bridge) and livekit-egress (call recording), all pinned to the `voice`
node pool.

Placeholders to replace before installing (from `terraform output`):

| Placeholder | Source |
|---|---|
| `REDIS_HOST` | `terraform output redis_host` |
| `SIP_STATIC_IP` | `terraform output sip_ip` |
| `LIVEKIT_DOMAIN` | `livekit.<domain>` (values + livekit-managed-cert.yaml) |
| `LIVEKIT_API_SECRET_CHANGE_ME` | generate: `openssl rand -hex 32` |
| `PROJECT_ID` | GCP project id (egress-values.yaml WI annotation) |

The API key name is `APIArchiteqKey`; key + secret must be identical in
`livekit-server-values.yaml`, `livekit-sip-values.yaml`, and the architeq
chart's `secrets.values.LIVEKIT_API_KEY/SECRET`.

## Install

```bash
helm repo add livekit https://helm.livekit.io
helm repo update

kubectl create namespace livekit

# TLS for the signalling Ingress (chart references it, does not create it)
kubectl -n livekit apply -f livekit-managed-cert.yaml

helm install livekit-server livekit/livekit-server \
  -n livekit -f livekit-server-values.yaml

# LiveKit publishes no SIP chart (helm.livekit.io has only server/egress/
# ingress) — ./sip is this repo's own minimal chart.
helm install livekit-sip ./sip \
  -n livekit -f livekit-sip-values.yaml

# Call recording. The KSA name/namespace (livekit/livekit-egress) must
# match terraform's Workload Identity binding — recordings upload to the
# GCS bucket via WI, no key file.
helm install egress livekit/egress \
  -n livekit -f egress-values.yaml
```

Verify: `kubectl -n livekit get pods,svc` — the sip Service must show the
reserved static IP as EXTERNAL-IP.

Recording smoke test: place a call, then
`gsutil ls gs://<project>-architeq-recordings/calls/` should show a fresh
`call_<id>.ogg` and the call's `recording_url` should play from the
dashboard Call History drawer.

## SIP trunks + dispatch rule (lk CLI)

These are LiveKit *objects*, created against the running server. Install
the CLI (`brew install livekit-cli`) and export:

```bash
export LIVEKIT_URL=wss://livekit.<domain>      # or port-forward ws://localhost:7880
export LIVEKIT_API_KEY=APIArchiteqKey
export LIVEKIT_API_SECRET=<secret>
```

### Outbound trunk (Architeq -> Telnyx -> PSTN)

`outbound-trunk.json`:

```json
{
  "trunk": {
    "name": "telnyx-outbound",
    "address": "sip.telnyx.com",
    "numbers": ["+15550001234"],
    "auth_username": "TELNYX_SIP_USERNAME",
    "auth_password": "TELNYX_SIP_PASSWORD"
  }
}
```

```bash
lk sip outbound create outbound-trunk.json
# note the returned trunk id (ST_...) — architeq-api needs it as
# ARCHITEQ_SIP_OUTBOUND_TRUNK_ID to place calls
```

### Inbound trunk (Telnyx DID -> livekit-sip)

`inbound-trunk.json`:

```json
{
  "trunk": {
    "name": "telnyx-inbound",
    "numbers": ["+15550001234"],
    "krisp_enabled": false
  }
}
```

```bash
lk sip inbound create inbound-trunk.json
```

### Dispatch rule (inbound call -> architeq worker)

`dispatch-rule.json` — one room per call, dispatched to the agent the
worker registers as (`architeq-agent`):

```json
{
  "dispatch_rule": {
    "name": "architeq-inbound",
    "trunk_ids": ["<INBOUND_TRUNK_ID>"],
    "rule": {
      "dispatchRuleIndividual": { "roomPrefix": "call_" }
    },
    "room_config": {
      "agents": [{ "agent_name": "architeq-agent" }]
    }
  }
}
```

```bash
lk sip dispatch create dispatch-rule.json
lk sip dispatch list
```

## Telnyx setup checklist

1. **Buy or port numbers** — Telnyx portal > Numbers. Note each DID in
   E.164 (`+1...`).
2. **Create a SIP Connection** (Voice > SIP Trunking > SIP Connections):
   - Type: **FQDN** — add FQDN `sip.<domain>` (A record -> `terraform
     output sip_ip`), port 5060, UDP.
   - Inbound: SIP transport UDP, codecs G.711U/G.711A (Opus optional).
   - Outbound: create an **Outbound Voice Profile** and attach it.
3. **Credentials**: on the SIP connection enable outbound auth
   (username/password) and put them in `outbound-trunk.json`
   (`auth_username`/`auth_password`).
4. **Assign numbers**: Numbers > select DID > Routing > set the SIP
   connection created above, so inbound calls route to livekit-sip.
5. **Enable AMD**: on the SIP connection / call settings enable Answering
   Machine Detection ("Premium" AMD) with result forwarding, so AMD
   outcomes reach the worker via SIP headers / LiveKit participant
   attributes (worker combines this with its Gemini greeting classifier).
6. Optional hardening: restrict inbound IP ACL to Telnyx media/signalling
   ranges; enable SIP Instance ID if using multiple environments.
