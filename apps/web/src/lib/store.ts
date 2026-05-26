// Client-side wrapper around the ingestion service's chat-store endpoints.
// Used from Next.js server components and from server-side route handlers —
// the chat UI itself doesn't talk to ingestion directly; it goes through
// /api/users and /api/sessions so the BFF can layer concerns later.

const BASE = process.env.OBSERVE_INGESTION_BASE ?? "http://localhost:8000";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${path} returned ${response.status}: ${text}`);
  }
  return (await response.json()) as T;
}

export interface User {
  id: string;
  username: string;
  created_at: string;
  last_seen_at: string;
}

export interface Session {
  id: string;
  user_id: string | null;
  title: string | null;
  created_at: string;
  updated_at: string;
  message_count: number;
  preview: string | null;
}

export interface Message {
  id: string;
  session_id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  created_at: string;
}

export interface SessionWithMessages extends Session {
  messages: Message[];
}

export const upsertUser = (username: string) =>
  call<User>("/v1/users", { method: "POST", body: JSON.stringify({ username }) });

export const getUser = (username: string) =>
  call<User>(`/v1/users/${encodeURIComponent(username)}`);

export const createSession = (userId: string, title?: string) =>
  call<Session>("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ user_id: userId, title: title ?? null }),
  });

export const listSessions = (userId: string, limit = 50) =>
  call<Session[]>(`/v1/users/${userId}/sessions?limit=${limit}`);

export const getSession = (sessionId: string) =>
  call<SessionWithMessages>(`/v1/sessions/${sessionId}`);

export const appendMessage = (sessionId: string, role: Message["role"], content: string) =>
  call<Message>(`/v1/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ role, content }),
  });
