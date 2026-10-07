"""LLM: Claude com streaming + tool calling."""
from __future__ import annotations

from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from app.providers.base import LLMFinal, LLMText


class ClaudeLLM:
    def __init__(self, api_key: str, model: str, max_tokens: int, temperature: float):
        # Cliente único por processo: reaproveita a conexão HTTP/TLS (economiza ~100-300ms por turno).
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def stream(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMText | LLMFinal]:
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,            
            system=system,
            messages=messages,
            tools=tools,
        ) as stream:
            async for event in stream:
                if event.type == "text":
                    yield LLMText(event.text)
            final = await stream.get_final_message()
        yield LLMFinal(
            content=[b.model_dump(exclude_none=True) for b in final.content],
            stop_reason=final.stop_reason,
        )
