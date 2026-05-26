# llm-observe

An end-to-end observability platform for LLM inference: non-invasive Python SDK, event-driven ingestion, Postgres analytical store, and a multi-provider chat UI used to exercise it.

## Layout

```
apps/web                 Next.js 14 chat UI + BFF route handlers
services/ingestion       FastAPI ingestion API + worker pool
packages/sdk-python      llm-observe SDK (the wrapper)
packages/shared-schema   JSON Schema -> Pydantic + zod (single source of truth)
infra/docker             docker-compose.yml for local dev
infra/k8s                Helm chart / manifests for prod
```

## Quickstart (local)

```bash
# 1. start the stack
cd infra/docker
docker compose up -d postgres ingestion

# 2. run a wrapped LLM call
cd ../../packages/sdk-python
uv venv && source .venv/bin/activate
uv pip install -e .
export OPENAI_API_KEY=sk-...
export OBSERVE_INGESTION_URL=http://localhost:8000/v1/logs
python examples/basic_call.py

# 3. confirm the log landed
docker compose exec postgres psql -U observe -d observe \
  -c "select provider, model, latency_ms, total_cost from inference_logs;"
```

## Phase status

This repo was built in 6 phases. See `/Users/manishsoni/.claude/plans/architectural-blueprint-and-autonomous-wiggly-fox.md`.

- [x] Phase 1 — Skeleton + SDK sync path + Postgres direct write
- [x] Phase 2 — Streaming + TTFT + cancellation
- [x] Phase 3 — Redis Streams + worker pool
- [x] Phase 4 — PII redaction (regex + Presidio)
- [x] Phase 5 — Multi-provider chat UI (Next.js 14)
- [x] Phase 6 — Dashboards + K8s Helm chart

## Run the full stack locally (one command)

```bash
cp .env.example .env             # then fill in whichever provider keys you want
cd infra/docker
docker compose --env-file ../../.env up --build
```

Once everything is healthy:

- Chat UI:    http://localhost:3000
- Dashboard:  http://localhost:3000/dashboard
- Ingestion:  http://localhost:8000/healthz, /metrics, /v1/analytics/*
- LiteLLM:    http://localhost:4000 (proxy for Gemini / DeepSeek / Grok)
- Postgres:   localhost:5432 (user/db: observe)
- Redis:      localhost:6379

The compose file ships six services + one one-shot migrate job, all on a single
bridge network with health-checked startup ordering. To bake the heavyweight
PII NER stack (Presidio + en_core_web_lg, ~600MB) into the worker image:

```bash
WITH_NLP=1 docker compose --env-file ../../.env up --build
```

## Providers

| Provider     | Route                | Env var               |
| ------------ | -------------------- | --------------------- |
| `openai`     | native SDK           | `OPENAI_API_KEY`      |
| `anthropic`  | native SDK           | `ANTHROPIC_API_KEY`   |
| `openrouter` | OpenAI-compat client | `OPENROUTER_API_KEY`  |
| `google`     | LiteLLM proxy        | `GOOGLE_API_KEY`      |
| `deepseek`   | LiteLLM proxy        | `DEEPSEEK_API_KEY`    |
| `xai`        | LiteLLM proxy        | `XAI_API_KEY`         |

OpenRouter is the recommended path for non-native providers — it gives you 300+
models with one key and no extra service to run. LiteLLM stays available as
an alternative for teams that prefer self-hosted proxying.

## Development

Per-package details live in each subdirectory's `README.md` and `CLAUDE.md`. The root `CLAUDE.md` captures cross-cutting conventions (commit style, schema-change protocol, etc.).
