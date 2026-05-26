"""Streaming smoke test for Phase 2.

Requires:
- OPENAI_API_KEY in the environment
- ingestion service reachable at OBSERVE_INGESTION_URL

Run:
    python examples/streaming_call.py

What it exercises:
- stream_openai wrapper records TTFT on first text chunk
- Usage is read from the final chunk because we pass include_usage=True
- Tokens stream to stdout as they arrive
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import uuid4

from llm_observe import observe, stream_openai
from llm_observe.transport import get_transport
from openai import AsyncOpenAI


async def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")

    session_id = uuid4()
    client = AsyncOpenAI()
    prompt = "Stream me a four-sentence haiku-adjacent poem about latency."

    async with observe(provider="openai", model="gpt-4o-mini", session_id=session_id) as ctx:
        ctx.set_preview(input_text=prompt)
        stream = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            stream=True,
            stream_options={"include_usage": True},
        )
        async for chunk in stream_openai(stream, ctx, prompt_text=prompt):
            choices = chunk.choices
            if choices and choices[0].delta and choices[0].delta.content:
                sys.stdout.write(choices[0].delta.content)
                sys.stdout.flush()

    sys.stdout.write("\n")
    print(f"session_id = {session_id}")
    print(f"ttft_ms    = {ctx.ttft_ms:.1f}" if ctx.ttft_ms else "ttft_ms    = (none)")
    print(f"tokens     = in={ctx.input_tokens} out={ctx.output_tokens}")

    await get_transport().aclose()


if __name__ == "__main__":
    asyncio.run(main())
