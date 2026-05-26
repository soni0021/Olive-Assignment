"""End-to-end smoke test for Phase 1.

Requires:
- OPENAI_API_KEY in the environment (or set provider='anthropic' with ANTHROPIC_API_KEY)
- ingestion service reachable at OBSERVE_INGESTION_URL (default http://localhost:8000/v1/logs)

Run:
    python examples/basic_call.py
"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from llm_observe import observe
from llm_observe.providers import extract_openai_usage
from llm_observe.transport import get_transport
from openai import AsyncOpenAI


async def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set")

    session_id = uuid4()
    client = AsyncOpenAI()
    prompt = "In one sentence, explain why TTFT matters for chat UX."

    async with observe(provider="openai", model="gpt-4o-mini", session_id=session_id) as ctx:
        ctx.set_preview(input_text=prompt)
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )
        extracted = extract_openai_usage(response)
        ctx.set_usage(extracted["input_tokens"], extracted["output_tokens"])
        ctx.set_preview(output_text=extracted["output_text"])
        ctx.merge_metadata(extracted["metadata"])

    print("session_id =", session_id)
    print("response   =", extracted["output_text"])

    # Force a clean drain before exit so the script doesn't lose the in-flight payload.
    await get_transport().aclose()


if __name__ == "__main__":
    asyncio.run(main())
