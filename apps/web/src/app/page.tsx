"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type Role = "user" | "assistant" | "system";
interface Msg { role: Role; content: string; }
interface User { id: string; username: string; }
interface SessionMeta {
  id: string;
  title: string | null;
  updated_at: string;
  message_count: number;
  preview: string | null;
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
    "deepseek/deepseek-chat",
    "x-ai/grok-2",
    "meta-llama/llama-3.1-70b-instruct",
    "mistralai/mistral-large",
  ],
};

const USERNAME_KEY = "llm-observe:username";

export default function ChatPage() {
  const [user, setUser] = useState<User | null>(null);
  const [usernameDraft, setUsernameDraft] = useState("");
  const [provider, setProvider] = useState("openai");
  const [model, setModel] = useState(PROVIDER_MODELS["openai"][0]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [ttftMs, setTtftMs] = useState<number | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Restore username on mount, fetch user record from server.
  useEffect(() => {
    const stored = typeof window !== "undefined" ? localStorage.getItem(USERNAME_KEY) : null;
    if (stored) void claimUsername(stored, /*persistDraft*/ false);
  }, []);

  // Token-batch incoming deltas with rAF so we re-render once per frame, not per chunk.
  const pendingRef = useRef<string>("");
  const rafRef = useRef<number | null>(null);
  const flush = useCallback(() => {
    rafRef.current = null;
    const chunk = pendingRef.current;
    if (!chunk) return;
    pendingRef.current = "";
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last && last.role === "assistant") {
        next[next.length - 1] = { ...last, content: last.content + chunk };
      }
      return next;
    });
  }, []);
  const appendDelta = useCallback(
    (delta: string) => {
      pendingRef.current += delta;
      if (rafRef.current === null) rafRef.current = requestAnimationFrame(flush);
    },
    [flush]
  );

  useEffect(() => {
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const models = useMemo(() => PROVIDER_MODELS[provider] ?? [], [provider]);

  const onProviderChange = (p: string) => {
    setProvider(p);
    setModel(PROVIDER_MODELS[p][0]);
  };

  async function refreshSessions(uid: string) {
    try {
      const response = await fetch(`/api/sessions?user_id=${uid}`);
      if (response.ok) setSessions(await response.json());
    } catch (err) {
      console.warn("session refresh failed", err);
    }
  }

  async function claimUsername(name: string, persistDraft: boolean) {
    if (!name.trim()) return;
    try {
      const response = await fetch("/api/users", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: name.trim() }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const u = (await response.json()) as User;
      setUser(u);
      if (persistDraft) localStorage.setItem(USERNAME_KEY, u.username);
      await refreshSessions(u.id);
    } catch (err) {
      alert(`Could not log in: ${err}`);
    }
  }

  async function startNewChat() {
    if (!user) return;
    try {
      const response = await fetch("/api/sessions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ user_id: user.id }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const s = (await response.json()) as SessionMeta;
      setSessionId(s.id);
      setMessages([]);
      setTtftMs(null);
      await refreshSessions(user.id);
    } catch (err) {
      alert(`Could not create session: ${err}`);
    }
  }

  async function resumeSession(sid: string) {
    setSessionId(sid);
    setMessages([]);
    try {
      const response = await fetch(`/api/sessions/${sid}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const detail = await response.json();
      const loaded: Msg[] = (detail.messages ?? []).map((m: { role: Role; content: string }) => ({
        role: m.role,
        content: m.content,
      }));
      setMessages(loaded);
    } catch (err) {
      console.warn("resume failed", err);
    }
  }

  function logout() {
    localStorage.removeItem(USERNAME_KEY);
    setUser(null);
    setSessions([]);
    setSessionId(null);
    setMessages([]);
  }

  const cancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setBusy(false);
  };

  const send = async () => {
    if (!input.trim() || busy || !user) return;
    // Lazily create a session on first send so empty new-chat sessions don't pile up.
    let sid = sessionId;
    if (!sid) {
      const r = await fetch("/api/sessions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ user_id: user.id }),
      });
      const s = (await r.json()) as SessionMeta;
      sid = s.id;
      setSessionId(sid);
    }

    const userMsg: Msg = { role: "user", content: input.trim() };
    setMessages((prev) => [...prev, userMsg, { role: "assistant", content: "" }]);
    setInput("");
    setBusy(true);
    setTtftMs(null);

    const controller = new AbortController();
    abortRef.current = controller;
    const startedAt = performance.now();
    let firstTokenSeen = false;

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          session_id: sid,
          provider,
          model,
          messages: [...messages, userMsg],
        }),
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`);

      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      let buffered = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buffered += value;
        let idx;
        while ((idx = buffered.indexOf("\n\n")) !== -1) {
          const event = buffered.slice(0, idx);
          buffered = buffered.slice(idx + 2);
          for (const line of event.split("\n")) {
            if (!line.startsWith("data:")) continue;
            const data = line.slice(5).trim();
            if (data === "[DONE]") continue;
            try {
              const payload = JSON.parse(data) as { delta?: string };
              if (payload.delta) {
                if (!firstTokenSeen) {
                  firstTokenSeen = true;
                  setTtftMs(performance.now() - startedAt);
                }
                appendDelta(payload.delta);
              }
            } catch {
              /* ignore malformed event */
            }
          }
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.role === "assistant") {
          next[next.length - 1] = { ...last, content: (last.content || "") + `\n\n[error: ${msg}]` };
        }
        return next;
      });
    } finally {
      flush();
      setBusy(false);
      abortRef.current = null;
      // Refresh sidebar to show the new session / updated preview.
      if (user) void refreshSessions(user.id);
    }
  };

  if (!user) {
    return (
      <main style={{ maxWidth: 480, margin: "0 auto", paddingTop: 120 }}>
        <h1 style={{ fontSize: 22, marginBottom: 24 }}>llm-observe</h1>
        <p style={{ color: "#9aa6b2", marginBottom: 16, lineHeight: 1.5 }}>
          Type a username to continue. New names create a fresh history; existing names load yours.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void claimUsername(usernameDraft, true);
          }}
          style={{ display: "flex", gap: 8 }}
        >
          <input
            autoFocus
            placeholder="username"
            value={usernameDraft}
            onChange={(e) => setUsernameDraft(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="primary" type="submit" disabled={!usernameDraft.trim()}>
            Continue
          </button>
        </form>
      </main>
    );
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "260px 1fr", minHeight: "100vh" }}>
      <aside
        style={{
          borderRight: "1px solid #1f242a",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span style={{ fontWeight: 600 }}>{user.username}</span>
          <button onClick={logout} style={{ fontSize: 11 }}>logout</button>
        </div>
        <button className="primary" onClick={startNewChat}>+ New chat</button>
        <div style={{ overflowY: "auto", flex: 1, marginTop: 8 }}>
          {sessions.length === 0 && (
            <div style={{ color: "#9aa6b2", fontSize: 12 }}>No sessions yet.</div>
          )}
          {sessions.map((s) => (
            <button
              key={s.id}
              onClick={() => resumeSession(s.id)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "6px 8px",
                marginBottom: 4,
                background: s.id === sessionId ? "#1a2535" : "transparent",
                border: "1px solid transparent",
                borderColor: s.id === sessionId ? "#2a72c8" : "transparent",
                fontSize: 13,
              }}
              title={s.title ?? "Untitled"}
            >
              <div style={{ fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {s.title ?? "Untitled"}
              </div>
              <div style={{ fontSize: 11, color: "#9aa6b2", marginTop: 2 }}>
                {new Date(s.updated_at).toLocaleString()} · {s.message_count} msg
              </div>
            </button>
          ))}
        </div>
      </aside>

      <main>
        <header>
          <h1>llm-observe chat</h1>
          <nav>
            <a href="/">Chat</a>
            <a href="/dashboard">Dashboard</a>
            <a href="/dashboard/settings">Settings</a>
          </nav>
        </header>

        <div className="toolbar">
          <select value={provider} onChange={(e) => onProviderChange(e.target.value)}>
            {Object.keys(PROVIDER_MODELS).map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
          <span style={{ flex: 1 }} />
          <span style={{ alignSelf: "center", color: "#9aa6b2", fontSize: 12 }}>
            session: {sessionId ? sessionId.slice(0, 8) + "…" : "(new on first send)"}
          </span>
        </div>

        <div className="messages">
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="role">{m.role}</div>
              {m.content ||
                (m.role === "assistant" && busy && i === messages.length - 1 ? "…" : "")}
            </div>
          ))}
        </div>

        <div className="composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a message…  (Cmd/Ctrl+Enter sends)"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                void send();
              }
            }}
          />
          {busy ? (
            <button className="danger" onClick={cancel}>Cancel</button>
          ) : (
            <button className="primary" onClick={send} disabled={!input.trim()}>Send</button>
          )}
        </div>

        {ttftMs !== null && (
          <div className="metrics">
            <span>TTFT: {ttftMs.toFixed(0)} ms</span>
          </div>
        )}
      </main>
    </div>
  );
}
