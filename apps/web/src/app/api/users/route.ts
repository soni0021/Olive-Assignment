// POST /api/users  { username } -> User (upsert)
// GET  /api/users?username=foo -> User | 404

import { NextRequest } from "next/server";
import { z } from "zod";

import { getUser, upsertUser } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UsernameBody = z.object({ username: z.string().min(1).max(64) });

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const parsed = UsernameBody.safeParse(body);
  if (!parsed.success) {
    return new Response(JSON.stringify({ error: "invalid body" }), { status: 400 });
  }
  const user = await upsertUser(parsed.data.username);
  return new Response(JSON.stringify(user), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

export async function GET(req: NextRequest) {
  const username = req.nextUrl.searchParams.get("username");
  if (!username) {
    return new Response(JSON.stringify({ error: "username required" }), { status: 400 });
  }
  try {
    const user = await getUser(username);
    return new Response(JSON.stringify(user), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), { status: 404 });
  }
}
