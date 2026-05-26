// GET  /api/sessions?user_id=X       -> Session[]
// POST /api/sessions { user_id, title? } -> Session

import { NextRequest } from "next/server";
import { z } from "zod";

import { createSession, listSessions } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const userId = req.nextUrl.searchParams.get("user_id");
  if (!userId) {
    return new Response(JSON.stringify({ error: "user_id required" }), { status: 400 });
  }
  const sessions = await listSessions(userId);
  return new Response(JSON.stringify(sessions), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

const CreateBody = z.object({
  user_id: z.string().uuid(),
  title: z.string().max(255).optional(),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const parsed = CreateBody.safeParse(body);
  if (!parsed.success) {
    return new Response(JSON.stringify({ error: "invalid body" }), { status: 400 });
  }
  const session = await createSession(parsed.data.user_id, parsed.data.title);
  return new Response(JSON.stringify(session), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}
