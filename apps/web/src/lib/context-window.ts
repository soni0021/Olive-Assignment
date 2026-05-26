// Sliding-window context manager. Per-model turn caps, applied to the
// {system + history} array before it's sent to the provider.
//
// Rough rule of thumb: cap at the smaller of (turns_max, token_budget). For
// MVP we use turn counts since rough tokens-per-turn averages out — proper
// token-budgeted truncation needs per-provider tokenizers and is Phase-future.

import type { ChatMessage } from "./providers";

const TURN_CAPS: Record<string, number> = {
  "gpt-4o": 30,
  "gpt-4o-mini": 60,
  "claude-3-5-sonnet-latest": 30,
  "claude-3-5-haiku-latest": 60,
  "gemini-1.5-pro": 40,
  "gemini-1.5-flash": 80,
  "deepseek-chat": 40,
  "grok-2": 30,
};

const DEFAULT_TURN_CAP = 20;

export function applyWindow(messages: ChatMessage[], model: string): ChatMessage[] {
  const cap = TURN_CAPS[model] ?? DEFAULT_TURN_CAP;
  const system = messages.find((m) => m.role === "system");
  const rest = messages.filter((m) => m.role !== "system");
  const trimmed = rest.slice(-cap);
  return system ? [system, ...trimmed] : trimmed;
}
