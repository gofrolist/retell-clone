#!/usr/bin/env bash
# Deploys the monitoring stack from infra/private/prod.env.
#
#   infra/helm/monitoring/gen-values.sh
#
# Does three things, in this order because each depends on the last:
#
#   1. Applies the Alertmanager Telegram bot token as a k8s Secret. It has to
#      exist before the release references it — `alertmanagerSpec.secrets`
#      mounts it as a volume, and a pod whose volume is missing never starts.
#   2. Renders infra/private/monitoring-values.yaml: this chart's values.yaml
#      with the two placeholders filled from prod.env. Gitignored, because the
#      repo is public and the chat id identifies a real person's chat.
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

kubectl get namespace monitoring >/dev/null 2>&1 || kubectl create namespace monitoring

# `create --dry-run=client | apply` rather than `create`: this has to be safe
# to re-run, and plain `create` fails once the Secret exists.
kubectl -n monitoring create secret generic alertmanager-telegram \
  --from-literal=bot-token="$TELEGRAM_BOT_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n monitoring create configmap arhiteq-dashboards \
  --from-file="$HERE/dashboards/" \
  --dry-run=client -o yaml | kubectl apply -f -

# Never clobber hand edits silently: keep the previous file for diffing.
[ -f "$OUT" ] && cp -p "$OUT" "$OUT.bak"

# `|` as the delimiter and no regex metacharacters in the replacements: the
# Grafana password is operator-chosen, and a `/` in it would end the s command.
python3 - "$HERE/values.yaml" "$OUT" <<'PY'
import os, sys
src, dst = sys.argv[1], sys.argv[2]
text = open(src).read()
subs = {
    "CHANGE_ME_GRAFANA_ADMIN": os.environ["GRAFANA_ADMIN_PASSWORD"],
    "TELEGRAM_CHAT_ID": os.environ["TELEGRAM_CHAT_ID"],
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

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --version "$CHART_VERSION" -n monitoring -f "$OUT"

# The rules and the egress PodMonitor are plain manifests: the chart has no
# hook for either, and Prometheus loads them from anywhere in the cluster.
kubectl apply -f "$HERE/rules/arhiteq-alerts.yaml"
kubectl apply -f "$HERE/extra/livekit-egress-podmonitor.yaml"
