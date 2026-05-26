// Provider routing: native SDKs for OpenAI/Anthropic, LiteLLM proxy for the rest.
// LiteLLM exposes an OpenAI-compatible /chat/completions endpoint, so the
// Gemini/DeepSeek/Grok paths reuse the OpenAI client pointing at the LiteLLM URL.

import Anthropic from "@anthropic-ai/sdk";
import OpenAI from "openai";

import type { Provider } from "./observe";
import { getRuntimeConfig, type RuntimeConfig } from "./runtime-config";

export interface ChatMessage {
  role: "system" | "user" | "assistant";
  content: string;
}

export class MissingCredentialsError extends Error {
  constructor(
    public readonly provider: Provider,
    public readonly settingField: string,
    public readonly envVar: string,
  ) {
    super(
      `Missing credentials for provider "${provider}". Set ${envVar} in .env (then restart the web container) or fill in "${settingField}" on /dashboard/settings.`,
    );
    this.name = "MissingCredentialsError";
  }
}

// Provider → (settings field on RuntimeConfig.providers, env var name).
// Kept next to the per-provider stream code so adding a provider here is hard
// to forget when wiring credentials.
const PROVIDER_CREDENTIALS: Record<
  Provider,
  { field: keyof RuntimeConfig["providers"]; env: string }
> = {
  openai: { field: "openai_api_key", env: "OPENAI_API_KEY" },
  anthropic: { field: "anthropic_api_key", env: "ANTHROPIC_API_KEY" },
  openrouter: { field: "openrouter_api_key", env: "OPENROUTER_API_KEY" },
  google: { field: "google_api_key", env: "GOOGLE_API_KEY" },
  deepseek: { field: "deepseek_api_key", env: "DEEPSEEK_API_KEY" },
  xai: { field: "xai_api_key", env: "XAI_API_KEY" },
};

export async function assertProviderCredentials(provider: Provider): Promise<void> {
  const cfg = await getRuntimeConfig();
  const spec = PROVIDER_CREDENTIALS[provider];
  if (!cfg.providers[spec.field]) {
    throw new MissingCredentialsError(provider, spec.field, spec.env);
  }
}

export interface StreamHandle {
  // Yields plain text deltas to the caller and updates the observation ctx in-flight.
  iterate(
    onDelta: (delta: string) => void,
    onUsage: (input: number, output: number) => void,
    signal: AbortSignal
  ): Promise<void>;
}

const OPENROUTER_URL = process.env.OPENROUTER_BASE_URL ?? "https://openrouter.ai/api/v1";

export const PROVIDER_MODELS: Record<Provider, string[]> = {
  openai: ["gpt-4o", "gpt-4o-mini"],
  anthropic: ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
  google: ["gemini-1.5-pro", "gemini-1.5-flash"],
  deepseek: ["deepseek-chat"],
  xai: ["grok-2"],
  // OpenRouter exposes 300+ models; we list a popular subset and let users
  // type any slug from the OpenRouter catalog via the chat UI's free text
  // model input. The slug format is "<upstream-provider>/<model>".
  openrouter: [
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3.5-haiku",
    "google/gemini-1.5-pro",
    "google/gemini-1.5-flash",
    "deepseek/deepseek-chat",
    "x-ai/grok-2",
    "meta-llama/llama-3.1-70b-instruct",
    "mistralai/mistral-large",
  ],
};

export function isProvider(value: string): value is Provider {
  return value in PROVIDER_MODELS;
}

export async function startStream(args: {
  provider: Provider;
  model: string;
  messages: ChatMessage[];
  signal: AbortSignal;
}): Promise<StreamHandle> {
  if (args.provider === "openai") return openaiStream(args.model, args.messages, args.signal);
  if (args.provider === "anthropic") return anthropicStream(args.model, args.messages, args.signal);
  if (args.provider === "openrouter") return openrouterStream(args.model, args.messages, args.signal);
  return litellmStream(args.model, args.messages, args.signal);
}

async function openaiStream(model: string, messages: ChatMessage[], signal: AbortSignal): Promise<StreamHandle> {
  const cfg = await getRuntimeConfig();
  const client = new OpenAI({ apiKey: cfg.providers.openai_api_key });
  const stream = await client.chat.completions.create(
    {
      model,
      messages,
      stream: true,
      stream_options: { include_usage: true },
    },
    { signal }
  );
  return {
    async iterate(onDelta, onUsage) {
      for await (const chunk of stream) {
        const text = chunk.choices[0]?.delta?.content;
        if (text) onDelta(text);
        if (chunk.usage) {
          onUsage(chunk.usage.prompt_tokens ?? 0, chunk.usage.completion_tokens ?? 0);
        }
      }
    },
  };
}

async function anthropicStream(model: string, messages: ChatMessage[], signal: AbortSignal): Promise<StreamHandle> {
  const cfg = await getRuntimeConfig();
  const client = new Anthropic({ apiKey: cfg.providers.anthropic_api_key });
  const systemMsg = messages.find((m) => m.role === "system")?.content;
  const userMsgs = messages.filter((m) => m.role !== "system").map((m) => ({
    role: m.role as "user" | "assistant",
    content: m.content,
  }));

  const stream = await client.messages.create(
    {
      model,
      max_tokens: 1024,
      system: systemMsg,
      messages: userMsgs,
      stream: true,
    },
    { signal }
  );

  return {
    async iterate(onDelta, onUsage) {
      let inputTokens = 0;
      let outputTokens = 0;
      for await (const event of stream) {
        if (event.type === "message_start") {
          inputTokens = event.message.usage.input_tokens ?? 0;
          onUsage(inputTokens, outputTokens);
        } else if (event.type === "content_block_delta" && event.delta.type === "text_delta") {
          onDelta(event.delta.text);
        } else if (event.type === "message_delta") {
          outputTokens = event.usage.output_tokens ?? outputTokens;
          onUsage(inputTokens, outputTokens);
        }
      }
    },
  };
}

async function openrouterStream(model: string, messages: ChatMessage[], signal: AbortSignal): Promise<StreamHandle> {
  // OpenAI-compatible client pointed at OpenRouter. The HTTP-Referer + X-Title
  // headers are optional but recommended — they show up on the OpenRouter
  // leaderboard and attribute usage to this app.
  const cfg = await getRuntimeConfig();
  const defaultHeaders: Record<string, string> = {};
  if (cfg.providers.openrouter_http_referer) {
    defaultHeaders["HTTP-Referer"] = cfg.providers.openrouter_http_referer;
  }
  if (cfg.providers.openrouter_x_title) {
    defaultHeaders["X-Title"] = cfg.providers.openrouter_x_title;
  }
  const client = new OpenAI({
    baseURL: OPENROUTER_URL,
    apiKey: cfg.providers.openrouter_api_key,
    defaultHeaders: Object.keys(defaultHeaders).length ? defaultHeaders : undefined,
  });
  const stream = await client.chat.completions.create(
    {
      model,
      messages,
      stream: true,
      stream_options: { include_usage: true },
    },
    { signal }
  );
  return {
    async iterate(onDelta, onUsage) {
      for await (const chunk of stream) {
        const text = chunk.choices[0]?.delta?.content;
        if (text) onDelta(text);
        if (chunk.usage) {
          onUsage(chunk.usage.prompt_tokens ?? 0, chunk.usage.completion_tokens ?? 0);
        }
      }
    },
  };
}

async function litellmStream(model: string, messages: ChatMessage[], signal: AbortSignal): Promise<StreamHandle> {
  // OpenAI-compatible client pointed at the LiteLLM proxy. LiteLLM forwards to
  // Gemini / DeepSeek / Grok and normalizes the response shape.
  const cfg = await getRuntimeConfig();
  const client = new OpenAI({
    baseURL: cfg.providers.litellm_base_url,
    apiKey: cfg.providers.litellm_master_key || "anything",
  });
  const stream = await client.chat.completions.create(
    {
      model,
      messages,
      stream: true,
      stream_options: { include_usage: true },
    },
    { signal }
  );
  return {
    async iterate(onDelta, onUsage) {
      for await (const chunk of stream) {
        const text = chunk.choices[0]?.delta?.content;
        if (text) onDelta(text);
        if (chunk.usage) {
          onUsage(chunk.usage.prompt_tokens ?? 0, chunk.usage.completion_tokens ?? 0);
        }
      }
    },
  };
}
