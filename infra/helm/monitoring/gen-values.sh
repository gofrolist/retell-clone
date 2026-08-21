#!/usr/bin/env bash
# Deploys the monitoring stack from infra/private/prod.env.
#
#   infra/helm/monitoring/gen-values.sh
#
# Does three things, in this order because each depends on the last:
#
#   1. Applies the Alertmanager Telegram bot token and the Grafana Google
#      OAuth client as k8s Secrets. They have to exist before the release
#      references them — `alertmanagerSpec.secrets` mounts one as a volume,
#      and a pod whose volume is missing never starts.
#   2. Renders infra/private/monitoring-values.yaml: this chart's values.yaml
#      with the placeholders filled from prod.env. Gitignored, because the
#      repo is public and the chat id and the operator emails identify real
#      people.
#   3. helm upgrade --install.
#
# Unlike infra/private/gen-arhiteq-prod.sh this lives in the repo, not in
# infra/private: it holds no secrets (only variable names), and infra/private
# is explicitly not backed up.
set -euo pipefail
umask 077

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
PRIV="$ROOT/infra/private"
OUT="$PRIV/monitoring-values.yaml"

# Pin the chart: an unpinned upgrade silently moves Prometheus, Grafana and
# Alertmanager versions at whatever moment the repo cache was last refreshed.
CHART_VERSION=87.16.1

# `set -a` so the values are exported, not just shell-local: the renderer
# below is a separate process and reads them from the environment.
set -a
source "$PRIV/prod.env"
set +a

: "${GRAFANA_ADMIN_PASSWORD:?set it in infra/private/prod.env}"
: "${TELEGRAM_BOT_TOKEN:?set it in infra/private/prod.env — see infra/README.md § Alerting}"
: "${TELEGRAM_CHAT_ID:?set it in infra/private/prod.env — see infra/README.md § Alerting}"
: "${GRAFANA_HOST:?set it in infra/private/prod.env — see infra/README.md § Grafana access}"
: "${GRAFANA_OAUTH_CLIENT_ID:?set it in infra/private/prod.env — see infra/README.md § Grafana access}"
: "${GRAFANA_OAUTH_CLIENT_SECRET:?set it in infra/private/prod.env — see infra/README.md § Grafana access}"
: "${GRAFANA_ADMIN_EMAILS:?set it in infra/private/prod.env — see infra/README.md § Grafana access}"
: "${GRAFANA_DB_PASSWORD:?set it in infra/private/prod.env — see infra/README.md § Grafana database access}"
# Optional: a deployment with no read-only operators is a normal one.
: "${GRAFANA_VIEWER_EMAILS:=}"

# The old single-list variable granted Admin to everyone on it. Silently
# treating it as the admin list would be a fine guess and a bad default —
# say so instead of handing someone Admin because their prod.env is stale.
if [ -n "${GRAFANA_ALLOWED_EMAILS:-}" ]; then
  echo "ERROR: GRAFANA_ALLOWED_EMAILS is gone — split it into GRAFANA_ADMIN_EMAILS" >&2
  echo "       and GRAFANA_VIEWER_EMAILS in infra/private/prod.env" >&2
  exit 1
fi

kubectl get namespace monitoring >/dev/null 2>&1 || kubectl create namespace monitoring

# `create --dry-run=client | apply` rather than `create`: this has to be safe
# to re-run, and plain `create` fails once the Secret exists.
kubectl -n monitoring create secret generic alertmanager-telegram \
  --from-literal=bot-token="$TELEGRAM_BOT_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

# Same shape for the Grafana OAuth client. It is a Secret rather than two more
# values because the rendered values file is what Helm stores in the release —
# `helm get values monitoring` would print the client secret back out.
kubectl -n monitoring create secret generic grafana-google-oauth \
  --from-literal=client-id="$GRAFANA_OAUTH_CLIENT_ID" \
  --from-literal=client-secret="$GRAFANA_OAUTH_CLIENT_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

# Same pattern for the grafana_ro password the business-metrics datasource
# uses: a Secret rather than a value, so it stays out of the Helm release.
# infra/sql/grafana_ro.sql is what creates the role this authenticates as.
kubectl -n monitoring create secret generic grafana-db \
  --from-literal=password="$GRAFANA_DB_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n monitoring create configmap arhiteq-dashboards \
  --from-file="$HERE/dashboards/" \
  --dry-run=client -o yaml | kubectl apply -f -

# Never clobber hand edits silently: keep the previous file for diffing.
[ -f "$OUT" ] && cp -p "$OUT" "$OUT.bak"

# Python, not sed: both values are operator-chosen and a `/` in the Grafana
# password would end the s command. The two placeholders are *not* substituted
# the same way, because they sit in differently-typed YAML slots:
#
#   - the password becomes a quoted scalar. Spliced in bare, one containing
#     `: ` or a trailing ` #` yields a different value than the operator set,
#     and one starting with `*`, `&`, `{`, `[`, `!`, `%` or a quote fails the
#     parse outright — either way they cannot log into Grafana.
#   - the chat id must stay *unquoted*: Alertmanager's `chat_id` is an int64,
#     and a quoted YAML string does not unmarshal into it. So it is validated
#     as an integer rather than escaped — the only safe way to leave it bare.
#   - the host, the root_url and the role_attribute_path expression are quoted
#     scalars too. The expression is the one that matters: it is JMESPath full
#     of single quotes and `&&`, so it has to arrive as a double-quoted YAML
#     string or the parse ends somewhere in the middle of the allowlist.
python3 - "$HERE/values.yaml" "$OUT" <<'PY'
import hashlib, json, os, re, sys

src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()

chat_id = os.environ["TELEGRAM_CHAT_ID"].strip()
try:
    int(chat_id)
except ValueError:
    raise SystemExit(
        f"TELEGRAM_CHAT_ID must be an integer (negative for groups/channels), got {chat_id!r}"
    ) from None

host = os.environ["GRAFANA_HOST"].strip()
if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", host):
    raise SystemExit(f"GRAFANA_HOST must be a bare hostname like grafana.example.com, got {host!r}")

# Space- or comma-separated in prod.env; JMESPath list literals here. A single
# quote in an address would close the literal early and rewrite the rest of the
# expression, so it is rejected rather than escaped.
def parse_emails(var, *, required):
    emails = [e for e in re.split(r"[\s,]+", os.environ.get(var, "")) if e]
    if required and not emails:
        raise SystemExit(f"{var} is empty — nobody could sign in with Google")
    for email in emails:
        if "@" not in email or "'" in email:
            raise SystemExit(f"{var} entry {email!r} is not a usable email address")
    return emails


admins = parse_emails("GRAFANA_ADMIN_EMAILS", required=True)
viewers = parse_emails("GRAFANA_VIEWER_EMAILS", required=False)

# Admin would win the `||` chain anyway, but an address in both lists means the
# operator lost track of who is meant to have what — which is worth stopping on
# when the two roles differ by "can edit every dashboard and datasource".
both = sorted(set(admins) & set(viewers))
if both:
    raise SystemExit(f"in both GRAFANA_ADMIN_EMAILS and GRAFANA_VIEWER_EMAILS: {', '.join(both)}")

# Grafana evaluates this against the userinfo claims. `||` binds looser than
# `&&`, so it reads as (admin && 'Admin') || (viewer && 'Viewer') || '': the
# first matching list wins and an unlisted address yields no role at all,
# which role_attribute_strict in values.yaml turns into a refused login.
def contains(emails):
    return "contains([" + ", ".join(f"'{e}'" for e in emails) + "], email)"


clauses = [f"{contains(admins)} && 'Admin'"]
if viewers:
    clauses.append(f"{contains(viewers)} && 'Viewer'")
role_path = " || ".join(clauses + ["''"])

subs = {
    # A YAML double-quoted scalar is JSON-string-compatible, so json.dumps is
    # the right escaper here: it covers the quotes, backslashes and control
    # characters that would otherwise alter the value or break the parse.
    "CHANGE_ME_GRAFANA_ADMIN": json.dumps(os.environ["GRAFANA_ADMIN_PASSWORD"]),
    "CHANGE_ME_GRAFANA_HOST": json.dumps(host),
    "CHANGE_ME_GRAFANA_ROOT_URL": json.dumps(f"https://{host}/"),
    "CHANGE_ME_GRAFANA_ROLE_ATTRIBUTE_PATH": json.dumps(role_path),
    # Not the credentials themselves — this annotation is world-readable to
    # anyone who can get the pod.
    "CHANGE_ME_GRAFANA_CREDENTIALS_CHECKSUM": hashlib.sha256(
        "\0".join(
            (
                os.environ["GRAFANA_OAUTH_CLIENT_ID"],
                os.environ["GRAFANA_OAUTH_CLIENT_SECRET"],
                os.environ["GRAFANA_DB_PASSWORD"],
            )
        ).encode()
    ).hexdigest(),
    "TELEGRAM_CHAT_ID": chat_id,
}
for placeholder, value in subs.items():
    if placeholder not in text:
        raise SystemExit(f"placeholder {placeholder} not found in {src}")
    text = text.replace(placeholder, value)
open(dst, "w").write(text)
PY
chmod 600 "$OUT"

# GKE runs the control plane and kube-dns itself, so these four are never
# scrapable here. Leaving them on does more than add dead targets: the chart
# ships each component's alert rules alongside its ServiceMonitor, and
# KubeSchedulerDown/KubeControllerManagerDown/KubeProxyDown all fire the moment
# the component is expected but absent. An editing accident that drops these
# lines pages you at 3am about a control plane Google is running fine, so
# assert them rather than trusting the file.
for component in kubeControllerManager kubeScheduler kubeProxy kubeEtcd coreDns; do
  grep -A1 "^$component:" "$OUT" | grep -q "enabled: false" || {
    echo "ERROR: $component is not disabled in $OUT — see infra/README.md § 3" >&2
    exit 1
  }
done

echo "wrote $OUT"

# `repo add` on a machine that already has the repo prints "already exists ...
# skipping" and exits 0 *without* refreshing the cached index, so `repo update`
# is not optional here: without it the first CHART_VERSION bump past whatever
# is cached dies at `helm upgrade` with "chart ... not found in
# prometheus-community index" — and by then this script has already applied the
# Secret, the ConfigMap and the rendered values.
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update prometheus-community >/dev/null

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version "$CHART_VERSION" -n monitoring -f "$OUT"

# The rules and the egress PodMonitor are plain manifests: the chart has no
# hook for either, and Prometheus loads them from anywhere in the cluster.
kubectl apply -f "$HERE/rules/arhiteq-alerts.yaml"
kubectl apply -f "$HERE/extra/livekit-egress-podmonitor.yaml"

cat <<EOF

Grafana: https://$GRAFANA_HOST (Google sign-in)
  admins:  $GRAFANA_ADMIN_EMAILS
  viewers: ${GRAFANA_VIEWER_EMAILS:-(none)}
  cert:  kubectl -n monitoring get managedcertificate grafana-cert -o jsonpath='{.status.certificateStatus}'
         first provision takes 15-60 min and needs $GRAFANA_HOST resolving to the ingress IP
  local: kubectl -n monitoring port-forward svc/monitoring-grafana 3001:80
EOF
