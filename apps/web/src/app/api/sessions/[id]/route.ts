// GET /api/sessions/[id] -> SessionWithMessages
//
// Used by the chat UI's "Resume" flow to rehydrate the message list.

import { NextRequest } from "next/server";

import { getSession } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(_req: NextRequest, { params }: { params: { id: string } }) {
  try {
    const session = await getSession(params.id);
    return new Response(JSON.stringify(session), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  } catch (err) {
    return new Response(JSON.stringify({ error: String(err) }), { status: 404 });
  }
}
