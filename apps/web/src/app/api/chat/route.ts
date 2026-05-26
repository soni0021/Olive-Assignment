// POST /api/chat
//
// Streams assistant tokens as SSE. Wraps the upstream LLM call with the
// telemetry helper so every request lands a row in inference_logs. Honors
// the client's AbortController: when the connection is dropped, the upstream
// stream is aborted, telemetry status flips to CANCELLED, and any partial
// output is preserved.

import { NextRequest } from "next/server";
import { z } from "zod";

import { applyWindow } from "@/lib/context-window";
import { markFirstToken, mergeMetadata, observe, setPreview, setUsage } from "@/lib/observe";
import {
  MissingCredentialsError,
  PROVIDER_MODELS,
  assertProviderCredentials,
  isProvider,
  startStream,
  type ChatMessage,
} from "@/lib/providers";
import { appendMessage } from "@/lib/store";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const RequestBody = z.object({
  session_id: z.string().uuid(),
  provider: z.string(),
  model: z.string(),
  messages: z
    .array(
      z.object({
        role: z.enum(["system", "user", "assistant"]),
        content: z.string(),
      })
    )
    .min(1),
});

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const parsed = RequestBody.safeParse(body);
  if (!parsed.success) {
    return new Response(JSON.stringify({ error: "invalid body", issues: parsed.error.issues }), {
      status: 400,
      headers: { "content-type": "application/json" },
    });
  }
  const { session_id, provider, model, messages } = parsed.data;

  if (!isProvider(provider)) {
    return new Response(JSON.stringify({ error: `unknown provider ${provider}` }), { status: 400 });
  }
  if (!PROVIDER_MODELS[provider].includes(model)) {
    return new Response(JSON.stringify({ error: `unknown model ${model} for ${provider}` }), {
      status: 400,
    });
  }

  try {
    await assertProviderCredentials(provider);
  } catch (err) {
    if (err instanceof MissingCredentialsError) {
      return new Response(
        JSON.stringify({
          error: err.message,
          provider: err.provider,
          missing_env: err.envVar,
          missing_setting: err.settingField,
        }),
        { status: 400, headers: { "content-type": "application/json" } },
      );
    }
    throw err;
  }

  const windowed = applyWindow(messages as ChatMessage[], model);
  const lastUser = [...windowed].reverse().find((m) => m.role === "user");

  // Persist the user turn fire-and-forget. We deliberately don't await so the
  // stream can start immediately; if the DB write fails the telemetry still
  // captures the call and we surface the failure in logs only.
  if (lastUser) {
    void appendMessage(session_id, "user", lastUser.content).catch((err) => {
      console.warn(`[chat] persist user msg failed for session=${session_id}:`, err);
    });
  }

  const encoder = new TextEncoder();
  const upstream = new AbortController();
  // If the downstream client disconnects, abort the upstream provider call.
  req.signal.addEventListener("abort", () => upstream.abort());

  const stream = new ReadableStream({
    async start(controller) {
      try {
        await observe(
          { provider, model, sessionId: session_id },
          async (ctx) => {
            if (lastUser) setPreview(ctx, { input: lastUser.content });
            mergeMetadata(ctx, { turns: windowed.length });

            const handle = await startStream({
              provider,
              model,
              messages: windowed,
              signal: upstream.signal,
            });

            const collected: string[] = [];
            const onDelta = (delta: string) => {
              markFirstToken(ctx);
              collected.push(delta);
              setPreview(ctx, { output: collected.join("") });
              controller.enqueue(encoder.encode(`data: ${JSON.stringify({ delta })}\n\n`));
            };
            const onUsage = (input: number, output: number) => {
              setUsage(ctx, input, output);
            };
            await handle.iterate(onDelta, onUsage, upstream.signal);
            controller.enqueue(encoder.encode(`data: [DONE]\n\n`));

            // Persist the assistant turn after the stream completes successfully.
            // On error or cancel we skip the write — observability still captures
            // the call via the telemetry path, but the messages table only stores
            // completed turns so the resume flow doesn't show half-finished text.
            const assistantText = collected.join("");
            if (assistantText) {
              void appendMessage(session_id, "assistant", assistantText).catch((err) => {
                console.warn(`[chat] persist asst msg failed for session=${session_id}:`, err);
              });
            }
          }
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        controller.enqueue(
          encoder.encode(`event: error\ndata: ${JSON.stringify({ error: msg })}\n\n`)
        );
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
    },
  });
}
