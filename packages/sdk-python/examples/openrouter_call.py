"""OpenRouter smoke test.

Requires:
- OPENROUTER_API_KEY in the environment.
- Ingestion service reachable at OBSERVE_INGESTION_URL.

Run:
    python examples/openrouter_call.py

Demonstrates that the same observe() context manager works seamlessly with
OpenRouter: the SDK doesn't care which upstream model serves the request,
only that the response shape is OpenAI-compatible. Cost is computed from
pricing.yaml's openrouter section.
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import uuid4

from llm_observe import observe, stream_openai
from llm_observe.providers import openrouter_client_kwargs
from llm_observe.transport import get_transport
from openai import AsyncOpenAI


async def main() -> None:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY is not set")

    session_id = uuid4()
    model = "anthropic/claude-3.5-haiku"
    prompt = "In one sentence, why is multi-provider routing useful for LLM observability?"

    client = AsyncOpenAI(**openrouter_client_kwargs())

    async with observe(provider="openrouter", model=model, session_id=session_id) as ctx:
        ctx.set_preview(input_text=prompt)
        stream = await client.chat.completions.create(
            model=model,
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
    print(f"model      = {model}")
    print(f"ttft_ms    = {ctx.ttft_ms:.1f}" if ctx.ttft_ms else "ttft_ms    = (none)")
    print(f"tokens     = in={ctx.input_tokens} out={ctx.output_tokens}")

    await get_transport().aclose()


if __name__ == "__main__":
    asyncio.run(main())
