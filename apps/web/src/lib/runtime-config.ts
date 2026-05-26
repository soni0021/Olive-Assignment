// Runtime configuration that can be edited from the /dashboard/settings page
// without restarting the web container. Backed by a YAML file mounted into
// the container at CONFIG_RUNTIME_PATH (defaults to /app/config/runtime.yaml,
// which docker-compose maps to ./infra/config/runtime.yaml on the host).
//
// Read path: getRuntimeConfig() checks the file's mtime; if it changed since
// last read, the YAML is re-parsed and cached. This gives us:
// - O(1) reads in steady state (the cache hits).
// - Sub-second propagation after a Save button click (the next request to any
//   route handler sees the new values, no restart, no SIGHUP).
//
// Write path: writeRuntimeConfig() atomically writes the YAML and bumps mtime
// implicitly. We use a temp file + rename so partially-written files never
// reach disk.

import { promises as fs } from "node:fs";
import { dirname } from "node:path";

import { parse as parseYaml, stringify as stringifyYaml } from "yaml";

const CONFIG_PATH = process.env.CONFIG_RUNTIME_PATH ?? "/app/config/runtime.yaml";

export interface RuntimeConfig {
  providers: {
    openai_api_key: string;
    anthropic_api_key: string;
    google_api_key: string;
    deepseek_api_key: string;
    xai_api_key: string;
    openrouter_api_key: string;
    openrouter_http_referer: string;
    openrouter_x_title: string;
    litellm_master_key: string;
    litellm_base_url: string;
  };
  defaults: {
    provider: string;
    model: string;
  };
}

const DEFAULT_CONFIG: RuntimeConfig = {
  providers: {
    openai_api_key: "",
    anthropic_api_key: "",
    google_api_key: "",
    deepseek_api_key: "",
    xai_api_key: "",
    openrouter_api_key: "",
    openrouter_http_referer: "http://localhost:3000",
    openrouter_x_title: "llm-observe",
    litellm_master_key: "sk-litellm-local",
    litellm_base_url: "http://litellm:4000/v1",
  },
  defaults: {
    provider: "openai",
    model: "gpt-4o-mini",
  },
};

interface CacheEntry {
  mtimeMs: number;
  data: RuntimeConfig;
}

let cache: CacheEntry | null = null;

function withEnvFallback(file: RuntimeConfig): RuntimeConfig {
  // Env vars seed the catalog so a fresh install with no settings.yaml still
  // works. Once the user saves settings, the file values take over.
  return {
    providers: {
      openai_api_key: file.providers.openai_api_key || process.env.OPENAI_API_KEY || "",
      anthropic_api_key: file.providers.anthropic_api_key || process.env.ANTHROPIC_API_KEY || "",
      google_api_key: file.providers.google_api_key || process.env.GOOGLE_API_KEY || "",
      deepseek_api_key: file.providers.deepseek_api_key || process.env.DEEPSEEK_API_KEY || "",
      xai_api_key: file.providers.xai_api_key || process.env.XAI_API_KEY || "",
      openrouter_api_key: file.providers.openrouter_api_key || process.env.OPENROUTER_API_KEY || "",
      openrouter_http_referer:
        file.providers.openrouter_http_referer || process.env.OPENROUTER_HTTP_REFERER || DEFAULT_CONFIG.providers.openrouter_http_referer,
      openrouter_x_title:
        file.providers.openrouter_x_title || process.env.OPENROUTER_X_TITLE || DEFAULT_CONFIG.providers.openrouter_x_title,
      litellm_master_key:
        file.providers.litellm_master_key || process.env.LITELLM_MASTER_KEY || DEFAULT_CONFIG.providers.litellm_master_key,
      litellm_base_url:
        file.providers.litellm_base_url || process.env.LITELLM_BASE_URL || DEFAULT_CONFIG.providers.litellm_base_url,
    },
    defaults: {
      provider: file.defaults.provider || DEFAULT_CONFIG.defaults.provider,
      model: file.defaults.model || DEFAULT_CONFIG.defaults.model,
    },
  };
}

export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  try {
    const stat = await fs.stat(CONFIG_PATH);
    if (cache && cache.mtimeMs === stat.mtimeMs) return cache.data;
    const raw = await fs.readFile(CONFIG_PATH, "utf-8");
    const parsed = parseYaml(raw) ?? {};
    const merged = withEnvFallback({
      providers: { ...DEFAULT_CONFIG.providers, ...(parsed.providers ?? {}) },
      defaults: { ...DEFAULT_CONFIG.defaults, ...(parsed.defaults ?? {}) },
    });
    cache = { mtimeMs: stat.mtimeMs, data: merged };
    return merged;
  } catch (err) {
    // File doesn't exist yet — fall back entirely to env vars + defaults.
    if ((err as NodeJS.ErrnoException).code === "ENOENT") {
      return withEnvFallback(DEFAULT_CONFIG);
    }
    throw err;
  }
}

export async function writeRuntimeConfig(next: RuntimeConfig): Promise<void> {
  await fs.mkdir(dirname(CONFIG_PATH), { recursive: true });
  const yaml = stringifyYaml(next);
  const tmp = `${CONFIG_PATH}.tmp`;
  await fs.writeFile(tmp, yaml, { mode: 0o600 });
  await fs.rename(tmp, CONFIG_PATH);
  // Force the next getRuntimeConfig() to re-read.
  cache = null;
}

// Helper for the settings page: returns the config but masks every secret so
// the rendered HTML never echoes raw keys to the browser.
export function maskSecrets(cfg: RuntimeConfig): RuntimeConfig {
  const mask = (s: string) => (s ? `${s.slice(0, 4)}…${s.slice(-4)}` : "");
  return {
    ...cfg,
    providers: {
      ...cfg.providers,
      openai_api_key: mask(cfg.providers.openai_api_key),
      anthropic_api_key: mask(cfg.providers.anthropic_api_key),
      google_api_key: mask(cfg.providers.google_api_key),
      deepseek_api_key: mask(cfg.providers.deepseek_api_key),
      xai_api_key: mask(cfg.providers.xai_api_key),
      openrouter_api_key: mask(cfg.providers.openrouter_api_key),
      litellm_master_key: mask(cfg.providers.litellm_master_key),
    },
  };
}
