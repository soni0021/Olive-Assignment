{{- define "llm-observe.labels" -}}
app.kubernetes.io/name: llm-observe
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "llm-observe.envCommon" -}}
- name: DATABASE_URL
  value: {{ .Values.config.databaseUrl | quote }}
- name: REDIS_URL
  value: {{ .Values.config.redisUrl | quote }}
- name: LOG_LEVEL
  value: {{ .Values.config.logLevel | quote }}
{{- end -}}
