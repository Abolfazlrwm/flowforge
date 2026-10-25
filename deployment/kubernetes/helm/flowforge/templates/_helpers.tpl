{{/* Common chart helpers for FlowForge. */}}

{{- define "flowforge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "flowforge.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "flowforge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "flowforge.labels" -}}
helm.sh/chart: {{ include "flowforge.chart" . }}
{{ include "flowforge.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "flowforge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "flowforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "flowforge.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "flowforge.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "flowforge.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- printf "%s-secret" (include "flowforge.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "flowforge.configMapName" -}}
{{- printf "%s-config" (include "flowforge.fullname" .) -}}
{{- end -}}

{{- define "flowforge.image" -}}
{{- $tag := default .Chart.AppVersion .Values.image.tag -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end -}}

{{- define "flowforge.podSecurityContext" -}}
{{- toYaml .Values.podSecurityContext -}}
{{- end -}}

{{- define "flowforge.containerSecurityContext" -}}
{{- toYaml .Values.containerSecurityContext -}}
{{- end -}}

{{/*
Common env injection: pull non-sensitive settings from the ConfigMap and secrets
from the Secret by reference only. The chart never inlines a secret value.
*/}}
{{- define "flowforge.commonEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "flowforge.configMapName" . }}
  - secretRef:
      name: {{ include "flowforge.secretName" . }}
{{- end -}}

{{/*
Migration gate: an init container that blocks the pod until Django migrations have
been applied. `manage.py migrate --check` exits non-zero while migrations are
pending, so the init container restarts until the migrate Job completes.
*/}}
{{- define "flowforge.migrateGate" -}}
- name: migrate-check
  image: {{ include "flowforge.image" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command: ["python", "manage.py", "migrate", "--check", "--noinput"]
  securityContext:
    {{- include "flowforge.containerSecurityContext" . | nindent 4 }}
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 256Mi
{{- end -}}
