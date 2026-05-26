import { z } from "zod";

export const Provider = z.enum(["openai", "anthropic", "google", "deepseek", "xai", "openrouter"]);
export type Provider = z.infer<typeof Provider>;

export const RequestStatus = z.enum(["SUCCESS", "ERROR", "CANCELLED", "RATE_LIMITED"]);
export type RequestStatus = z.infer<typeof RequestStatus>;

export const InferenceLog = z
  .object({
    log_id: z.string().uuid(),
    session_id: z.string().uuid(),
    message_id: z.string().uuid().nullable().optional(),
    provider: Provider,
    model: z.string().min(1).max(100),
    request_status: RequestStatus,
    latency_ms: z.number().nonnegative(),
    ttft_ms: z.number().nonnegative().nullable().optional(),
    input_tokens: z.number().int().nonnegative(),
    output_tokens: z.number().int().nonnegative(),
    total_cost: z.number().nonnegative(),
    error_stack: z.string().nullable().optional(),
    input_preview: z.string().max(500).nullable().optional(),
    output_preview: z.string().max(500).nullable().optional(),
    timestamp: z.string().datetime(),
    metadata: z.record(z.unknown()).default({}),
  })
  .strict();

export type InferenceLog = z.infer<typeof InferenceLog>;
