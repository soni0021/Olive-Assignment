// TypeScript port of the Python SDK's observe() contract.
// Mirrors the same semantics:
// - Bounded in-memory queue, fire-and-forget POST to ingestion.
// - Status classification: SUCCESS / ERROR / CANCELLED / RATE_LIMITED.
// - Streaming wrappers update ctx incrementally so partial state survives
//   consumer cancellation (same fix as the Python streaming wrappers).
// - Never throws into the host route handler.

import { InferenceLog, type InferenceLog as InferenceLogType } from "@llm-observe/shared-schema";

const INGESTION_URL = process.env.OBSERVE_INGESTION_URL ?? "http://localhost:8000/v1/logs";
const BUFFER_SIZE = Number(process.env.OBSERVE_BUFFER_SIZE ?? "1000");
const TIMEOUT_MS = Number(process.env.OBSERVE_TIMEOUT_MS ?? "2000");

type Status = "SUCCESS" | "ERROR" | "CANCELLED" | "RATE_LIMITED";

const buffer: InferenceLogType[] = [];
let draining = false;

function classifyError(err: unknown): Status {
  if (err instanceof DOMException && err.name === "AbortError") return "CANCELLED";
  const msg = String((err as Error | undefined)?.message ?? err);
  if (/abort|cancel/i.test(msg)) return "CANCELLED";
  if (/rate.?limit|429/i.test(msg)) return "RATE_LIMITED";
  return "ERROR";
}

async function drain(): Promise<void> {
  if (draining) return;
  draining = true;
  try {
    while (buffer.length > 0) {
      const payload = buffer[0];
      try {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
        const response = await fetch(INGESTION_URL, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        clearTimeout(timer);
        if (response.status === 202 || response.status === 200) {
          buffer.shift();
        } else if (response.status === 429 || response.status >= 500) {
          // Transient — back off and retry.
          await new Promise((r) => setTimeout(r, 200));
          break;
        } else {
          // Permanent 4xx — drop to avoid head-of-line blocking.
          console.warn(`[observe] permanent rejection ${response.status} log_id=${payload.log_id}`);
          buffer.shift();
        }
      } catch (err) {
        console.warn(`[observe] transport error log_id=${payload.log_id}:`, err);
        await new Promise((r) => setTimeout(r, 200));
        break;
      }
    }
  } finally {
    draining = false;
  }
}

function enqueue(payload: InferenceLogType): void {
  if (buffer.length >= BUFFER_SIZE) {
    const evicted = buffer.shift();
    console.warn(`[observe] buffer full; evicting log_id=${evicted?.log_id}`);
  }
  buffer.push(payload);
  void drain();
}

const PREVIEW_MAX = 500;
function truncate(text: string | null | undefined): string | null {
  if (text == null) return null;
  return text.slice(0, PREVIEW_MAX);
}

export type Provider = "openai" | "anthropic" | "google" | "deepseek" | "xai" | "openrouter";

export interface ObservationContext {
  logId: string;
  sessionId: string;
  messageId?: string;
  provider: Provider;
  model: string;
  inputTokens: number;
  outputTokens: number;
  ttftMs: number | null;
  inputPreview: string | null;
  outputPreview: string | null;
  metadata: Record<string, unknown>;
  // internal
  _perfStart: number;
  _startedAt: string;
}

function newCtx(provider: Provider, model: string, sessionId: string): ObservationContext {
  return {
    logId: crypto.randomUUID(),
    sessionId,
    provider,
    model,
    inputTokens: 0,
    outputTokens: 0,
    ttftMs: null,
    inputPreview: null,
    outputPreview: null,
    metadata: {},
    _perfStart: performance.now(),
    _startedAt: new Date().toISOString(),
  };
}

export function markFirstToken(ctx: ObservationContext): void {
  if (ctx.ttftMs === null) {
    ctx.ttftMs = performance.now() - ctx._perfStart;
  }
}

export function setUsage(ctx: ObservationContext, input: number, output: number): void {
  ctx.inputTokens = input;
  ctx.outputTokens = output;
}

export function setPreview(
  ctx: ObservationContext,
  { input, output }: { input?: string; output?: string }
): void {
  if (input !== undefined) ctx.inputPreview = truncate(input);
  if (output !== undefined) ctx.outputPreview = truncate(output);
}

export function mergeMetadata(ctx: ObservationContext, extra: Record<string, unknown>): void {
  Object.assign(ctx.metadata, extra);
}

interface PricingRow {
  input_per_1k: number;
  output_per_1k: number;
}
const PRICING: Record<string, Record<string, PricingRow>> = {
  openai: {
    "gpt-4o": { input_per_1k: 0.0025, output_per_1k: 0.01 },
    "gpt-4o-2024-08-06": { input_per_1k: 0.0025, output_per_1k: 0.01 },
    "gpt-4o-mini": { input_per_1k: 0.00015, output_per_1k: 0.0006 },
    "gpt-4-turbo": { input_per_1k: 0.01, output_per_1k: 0.03 },
  },
  anthropic: {
    "claude-3-5-sonnet-latest": { input_per_1k: 0.003, output_per_1k: 0.015 },
    "claude-3-5-sonnet-20241022": { input_per_1k: 0.003, output_per_1k: 0.015 },
    "claude-3-5-haiku-latest": { input_per_1k: 0.001, output_per_1k: 0.005 },
  },
  google: {
    "gemini-1.5-pro": { input_per_1k: 0.00125, output_per_1k: 0.005 },
    "gemini-1.5-flash": { input_per_1k: 0.000075, output_per_1k: 0.0003 },
  },
  deepseek: { "deepseek-chat": { input_per_1k: 0.00014, output_per_1k: 0.00028 } },
  xai: { "grok-2": { input_per_1k: 0.002, output_per_1k: 0.01 } },
  // OpenRouter uses "<upstream-provider>/<model>" slugs. Same per-token rates
  // as native providers since OpenRouter doesn't mark up.
  openrouter: {
    "openai/gpt-4o": { input_per_1k: 0.0025, output_per_1k: 0.01 },
    "openai/gpt-4o-mini": { input_per_1k: 0.00015, output_per_1k: 0.0006 },
    "anthropic/claude-3.5-sonnet": { input_per_1k: 0.003, output_per_1k: 0.015 },
    "anthropic/claude-3-5-sonnet": { input_per_1k: 0.003, output_per_1k: 0.015 },
    "anthropic/claude-3.5-haiku": { input_per_1k: 0.001, output_per_1k: 0.005 },
    "google/gemini-1.5-pro": { input_per_1k: 0.00125, output_per_1k: 0.005 },
    "google/gemini-1.5-flash": { input_per_1k: 0.000075, output_per_1k: 0.0003 },
    "deepseek/deepseek-chat": { input_per_1k: 0.00014, output_per_1k: 0.00028 },
    "x-ai/grok-2": { input_per_1k: 0.002, output_per_1k: 0.01 },
    "meta-llama/llama-3.1-70b-instruct": { input_per_1k: 0.00088, output_per_1k: 0.00088 },
    "mistralai/mistral-large": { input_per_1k: 0.003, output_per_1k: 0.009 },
  },
};

function computeCost(provider: Provider, model: string, inTokens: number, outTokens: number): { cost: number; missing: boolean } {
  const row = PRICING[provider]?.[model];
  if (!row) return { cost: 0, missing: true };
  const cost = (inTokens / 1000) * row.input_per_1k + (outTokens / 1000) * row.output_per_1k;
  return { cost: Math.round(cost * 1e8) / 1e8, missing: false };
}

export async function observe<T>(
  args: { provider: Provider; model: string; sessionId: string },
  fn: (ctx: ObservationContext) => Promise<T>
): Promise<T> {
  const ctx = newCtx(args.provider, args.model, args.sessionId);
  let status: Status = "SUCCESS";
  let errorStack: string | null = null;
  try {
    return await fn(ctx);
  } catch (err) {
    status = classifyError(err);
    errorStack = err instanceof Error ? (err.stack ?? err.message) : String(err);
    throw err;
  } finally {
    const latencyMs = performance.now() - ctx._perfStart;
    const { cost, missing } = computeCost(ctx.provider, ctx.model, ctx.inputTokens, ctx.outputTokens);
    if (missing) ctx.metadata.cost_missing = true;

    const parsed = InferenceLog.safeParse({
      log_id: ctx.logId,
      session_id: ctx.sessionId,
      message_id: ctx.messageId ?? null,
      provider: ctx.provider,
      model: ctx.model,
      request_status: status,
      latency_ms: latencyMs,
      ttft_ms: ctx.ttftMs,
      input_tokens: ctx.inputTokens,
      output_tokens: ctx.outputTokens,
      total_cost: cost,
      error_stack: errorStack,
      input_preview: ctx.inputPreview,
      output_preview: ctx.outputPreview,
      timestamp: ctx._startedAt,
      metadata: ctx.metadata,
    });

    if (parsed.success) {
      enqueue(parsed.data);
    } else {
      console.error("[observe] payload validation failed:", parsed.error.issues);
    }
  }
}
