"use client";

import { useEffect, useState } from "react";

interface RuntimeConfig {
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
  defaults: { provider: string; model: string };
}

const PROVIDER_MODELS: Record<string, string[]> = {
  openai: ["gpt-4o", "gpt-4o-mini"],
  anthropic: ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
  google: ["gemini-1.5-pro", "gemini-1.5-flash"],
  deepseek: ["deepseek-chat"],
  xai: ["grok-2"],
  openrouter: [
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3.5-haiku",
    "google/gemini-1.5-pro",
    "google/gemini-1.5-flash",
  ],
};

const SECRET_FIELDS = new Set<keyof RuntimeConfig["providers"]>([
  "openai_api_key",
  "anthropic_api_key",
  "google_api_key",
  "deepseek_api_key",
  "xai_api_key",
  "openrouter_api_key",
  "litellm_master_key",
]);

function isSecret(key: keyof RuntimeConfig["providers"]): boolean {
  return SECRET_FIELDS.has(key);
}

export default function SettingsPage() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [status, setStatus] = useState<string>("");
  const [showSecrets, setShowSecrets] = useState(false);

  async function load() {
    setStatus("Loading…");
    const response = await fetch("/api/settings?raw=1", { cache: "no-store" });
    if (!response.ok) {
      setStatus(`Load failed: HTTP ${response.status}`);
      return;
    }
    setConfig(await response.json());
    setStatus("");
  }

  useEffect(() => {
    void load();
  }, []);

  function update<K extends keyof RuntimeConfig["providers"]>(
    field: K,
    value: RuntimeConfig["providers"][K]
  ) {
    setConfig((prev) => prev && { ...prev, providers: { ...prev.providers, [field]: value } });
  }

  function updateDefault<K extends keyof RuntimeConfig["defaults"]>(
    field: K,
    value: RuntimeConfig["defaults"][K]
  ) {
    setConfig((prev) => prev && { ...prev, defaults: { ...prev.defaults, [field]: value } });
  }

  async function save() {
    if (!config) return;
    setStatus("Saving…");
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(config),
    });
    setStatus(response.ok ? `Saved at ${new Date().toLocaleTimeString()}` : `Save failed: ${response.status}`);
  }

  async function sync() {
    setStatus("Re-reading config from disk…");
    await load();
    setStatus(`Synced at ${new Date().toLocaleTimeString()}`);
  }

  if (!config) {
    return (
      <main>
        <header>
          <h1>llm-observe settings</h1>
          <nav>
            <a href="/">Chat</a>
            <a href="/dashboard">Dashboard</a>
            <a href="/dashboard/settings">Settings</a>
          </nav>
        </header>
        <p style={{ color: "#9aa6b2" }}>{status || "Loading…"}</p>
      </main>
    );
  }

  const providerKeys = Object.keys(config.providers) as Array<keyof RuntimeConfig["providers"]>;
  const defaultModels = PROVIDER_MODELS[config.defaults.provider] ?? [];

  return (
    <main>
      <header>
        <h1>llm-observe settings</h1>
        <nav>
          <a href="/">Chat</a>
          <a href="/dashboard">Dashboard</a>
          <a href="/dashboard/settings">Settings</a>
        </nav>
      </header>

      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <button className="primary" onClick={save}>Save</button>
        <button onClick={sync} title="Re-read runtime.yaml from disk (no restart)">
          ↻ Sync
        </button>
        <label style={{ marginLeft: 12, display: "flex", gap: 6, alignItems: "center", fontSize: 13 }}>
          <input type="checkbox" checked={showSecrets} onChange={(e) => setShowSecrets(e.target.checked)} />
          Show secrets
        </label>
        <span style={{ flex: 1 }} />
        <span style={{ color: "#9aa6b2", fontSize: 12 }}>{status}</span>
      </div>

      <section style={{ marginBottom: 32 }}>
        <h2 style={{ fontSize: 14, color: "#9aa6b2", margin: "12px 0 6px" }}>Defaults</h2>
        <div style={{ display: "grid", gridTemplateColumns: "150px 1fr", gap: 8, maxWidth: 600 }}>
          <label style={{ alignSelf: "center" }}>Provider</label>
          <select
            value={config.defaults.provider}
            onChange={(e) => {
              const p = e.target.value;
              updateDefault("provider", p);
              const next = PROVIDER_MODELS[p]?.[0];
              if (next) updateDefault("model", next);
            }}
          >
            {Object.keys(PROVIDER_MODELS).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <label style={{ alignSelf: "center" }}>Model</label>
          <select value={config.defaults.model} onChange={(e) => updateDefault("model", e.target.value)}>
            {defaultModels.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </section>

      <section>
        <h2 style={{ fontSize: 14, color: "#9aa6b2", margin: "12px 0 6px" }}>Provider keys + config</h2>
        <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", gap: 8, maxWidth: 800 }}>
          {providerKeys.map((k) => (
            <SettingRow
              key={k}
              label={k}
              value={config.providers[k]}
              secret={isSecret(k)}
              reveal={showSecrets}
              onChange={(v) => update(k, v)}
            />
          ))}
        </div>
      </section>

      <p style={{ color: "#9aa6b2", fontSize: 12, marginTop: 24 }}>
        Values are persisted to <code>runtime.yaml</code> in the mounted config volume.
        After Save, the next chat request reads the new values — no restart required.
        Env-var defaults still apply for any field left blank.
      </p>
    </main>
  );
}

function SettingRow({
  label,
  value,
  secret,
  reveal,
  onChange,
}: {
  label: string;
  value: string;
  secret: boolean;
  reveal: boolean;
  onChange: (v: string) => void;
}) {
  return (
    <>
      <label style={{ alignSelf: "center", fontSize: 13, color: "#cfd6dd" }}>{label}</label>
      <input
        type={secret && !reveal ? "password" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={secret ? "(unset — falls back to env var)" : ""}
        spellCheck={false}
        autoComplete="off"
        style={{ fontFamily: secret ? "ui-monospace, monospace" : undefined }}
      />
    </>
  );
}
