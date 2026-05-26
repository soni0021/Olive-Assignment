# CLAUDE.md — repo-wide conventions

Non-negotiables for any agent working in this repo.

## Repo shape

Monorepo. Python services use `uv` + `pyproject.toml`. The web app uses `pnpm`. There is intentionally no Nx/Turbo orchestrator — package boundaries are enforced by directory, not by build tool.

## Schema is the contract

`packages/shared-schema/jsonschema/inference_log.schema.json` is the single source of truth for the SDK→ingestion payload. The Pydantic and TypeScript types are derived from it. Never edit one type without regenerating the others. Adding a field is a major version bump on the SDK.

## Telemetry must never break the host

Anything the SDK does on behalf of the host application must be:
1. Non-blocking on the LLM call's critical path (use `asyncio.create_task` or background threads).
2. Exception-safe — a broken ingestion URL or DNS failure cannot raise into user code.
3. Bounded in memory — retry buffers have an explicit `maxlen`, never an unbounded list.

If you find yourself writing `try: ... except Exception: pass` in the SDK, that's a hint you've broken rule 2. Fix it correctly: log to the SDK's internal logger and drop the payload.

## Status enum is closed

`SUCCESS | ERROR | CANCELLED | RATE_LIMITED`. Don't add states without a migration plan.

## Cost figures

Pricing is in `packages/sdk-python/llm_observe/pricing.yaml`. Hardcoded prices anywhere else are a bug. When a provider omits final token usage on streaming responses (LiteLLM-routed minor providers), estimated tokens are flagged with `metadata.cost_estimated=true`.

## No PII in commits / fixtures

Test fixtures must use synthetic PII (e.g., `john.doe@example.test`, `555-0100` phone numbers). Real-looking PII in committed test data is a finding even if it's fake — assume reviewers can't tell.

## What not to do without asking

- Adding a new database column to `inference_logs` (it's a partitioned table; migrations are costly).
- Changing the `status` enum.
- Adding a synchronous HTTP call inside the SDK's hot path.
- Bumping Presidio's model from `en_core_web_lg` (NER false-positive profile is calibrated to this model).
