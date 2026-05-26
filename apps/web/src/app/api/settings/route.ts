// GET  /api/settings          -> { providers (masked), defaults }
// GET  /api/settings?raw=1    -> full raw config (only callable from the
//                                 settings page; never echoes secrets in HTML)
// POST /api/settings { ... }   -> persist runtime.yaml
//
// The masking on GET prevents secret leakage if the dashboard page is ever
// cached by an upstream proxy. The raw form is used only by the settings
// editor, which loads it once into a form on a not-cached request.

import { NextRequest } from "next/server";
import { z } from "zod";

import { getRuntimeConfig, maskSecrets, writeRuntimeConfig } from "@/lib/runtime-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const raw = req.nextUrl.searchParams.get("raw") === "1";
  const cfg = await getRuntimeConfig();
  const out = raw ? cfg : maskSecrets(cfg);
  return new Response(JSON.stringify(out), {
    status: 200,
    headers: {
      "content-type": "application/json",
      // Defense-in-depth: never let an intermediary cache secrets.
      "cache-control": "no-store",
    },
  });
}

const Body = z.object({
  providers: z.object({
    openai_api_key: z.string(),
    anthropic_api_key: z.string(),
    google_api_key: z.string(),
    deepseek_api_key: z.string(),
    xai_api_key: z.string(),
    openrouter_api_key: z.string(),
    openrouter_http_referer: z.string(),
    openrouter_x_title: z.string(),
    litellm_master_key: z.string(),
    litellm_base_url: z.string(),
  }),
  defaults: z.object({
    provider: z.string(),
    model: z.string(),
  }),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const parsed = Body.safeParse(body);
  if (!parsed.success) {
    return new Response(
      JSON.stringify({ error: "invalid body", issues: parsed.error.issues }),
      { status: 400, headers: { "content-type": "application/json" } }
    );
  }
  await writeRuntimeConfig(parsed.data);
  // Return the masked form so the response shape matches a regular GET.
  return new Response(JSON.stringify(maskSecrets(parsed.data)), {
    status: 200,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}
