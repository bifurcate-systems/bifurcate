{{- define "bifurcate.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bifurcate.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "bifurcate.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "bifurcate.labels" -}}
app.kubernetes.io/name: {{ include "bifurcate.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}
