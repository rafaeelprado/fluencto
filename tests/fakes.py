"""Provedores falsos: testam o orquestrador sem rede nem chaves."""
from __future__ import annotations

import asyncio
from typing import Any

from app.providers.base import LLMFinal, LLMText, STTEvent


class FakeSTT:
    def __init__(self):
        self.q: asyncio.Queue[STTEvent | None] = asyncio.Queue()
        self.audio_bytes = 0

    async def connect(self):
        pass

    async def send_audio(self, pcm: bytes):
        self.audio_bytes += len(pcm)
        # Protocolo de teste: um frame b"SAY:<texto>" vira transcrição final + fim de turno.
        if pcm.startswith(b"SAY:"):
            text = pcm[4:].decode()
            await self.q.put(STTEvent("interim", text[:10]))
            await self.q.put(STTEvent("final", text, speech_final=True, last_word_end=0.0))

    async def events(self):
        while (ev := await self.q.get()) is not None:
            yield ev

    async def close(self):
        await self.q.put(None)


class ScriptedLLM:
    """Cada chamada a stream() consome o próximo item do roteiro:
    uma string (resposta final) ou (texto_antes, [tool_uses])."""

    def __init__(self, script: list[Any], token_delay: float = 0.0):
        self.script = list(script)
        self.calls: list[list[dict]] = []
        self.token_delay = token_delay

    async def stream(self, system, messages, tools):
        self.calls.append([dict(m) for m in messages])
        item = self.script.pop(0)
        text, tool_uses = (item, []) if isinstance(item, str) else item
        for tok in text.split(" "):
            if self.token_delay:
                await asyncio.sleep(self.token_delay)
            yield LLMText(tok + " ")
        content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": f"tu_{i}", "name": n, "input": a} for i, (n, a) in enumerate(tool_uses)
        ]
        yield LLMFinal(content, "tool_use" if tool_uses else "end_turn")


class FakeTTS:
    instances: list["FakeTTS"] = []

    def __init__(self, delay: float = 0.0):
        self.q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.texts: list[str] = []
        self.delay = delay
        FakeTTS.instances.append(self)

    async def connect(self):
        pass

    async def send_text(self, text, flush=False):
        self.texts.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        await self.q.put(b"\x00\x00" * 2400)   # 100 ms de silêncio @24k

    async def end_input(self):
        await self.q.put(None)

    async def audio(self):
        while (b := await self.q.get()) is not None:
            yield b

    async def close(self):
        pass
