{{/* Common labels */}}
{{- define "arhiteq.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: arhiteq
{{- end }}

{{/* Selector labels for a component; call with dict "root" . "component" "api" */}}
{{- define "arhiteq.selectorLabels" -}}
app.kubernetes.io/name: arhiteq-{{ .component }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
{{- end }}

{{/* Full image reference; call with dict "root" . "image" .Values.api.image */}}
{{- define "arhiteq.image" -}}
{{ .root.Values.global.imageRegistry }}/{{ .image.repository }}:{{ .image.tag | default .root.Chart.AppVersion }}
{{- end }}

{{/* Name of the secret consumed by envFrom */}}
{{- define "arhiteq.secretName" -}}
{{- if .Values.secrets.create -}}
{{ .Release.Name }}-secrets
{{- else -}}
{{ required "secrets.existingSecret is required when secrets.create=false" .Values.secrets.existingSecret }}
{{- end -}}
{{- end }}
