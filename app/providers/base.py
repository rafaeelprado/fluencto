"""Contratos dos provedores.

A sessão de voz depende destas interfaces, não de Deepgram/Claude/ElevenLabs.
Benefícios: testes sem rede (fakes) e troca de provedor sem tocar no orquestrador.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal, Protocol


# ---------------------------------------------------------------- STT
@dataclass
class STTEvent:
    kind: Literal["interim", "final", "speech_started", "utterance_end"]
    text: str = ""
    speech_final: bool = False
    last_word_end: float | None = None   # segundos no relógio do áudio


class STTStream(Protocol):
    async def connect(self) -> None: ...
    async def send_audio(self, pcm16: bytes) -> None: ...
    def events(self) -> AsyncIterator[STTEvent]: ...
    async def close(self) -> None: ...


# ---------------------------------------------------------------- LLM
@dataclass
class LLMText:
    text: str


@dataclass
class LLMFinal:
    content: list[dict[str, Any]]   # blocos no formato da Messages API (text / tool_use)
    stop_reason: str | None


class LLMClient(Protocol):
    def stream(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMText | LLMFinal]: ...


# ---------------------------------------------------------------- TTS
class TTSStream(Protocol):
    async def connect(self) -> None: ...
    async def send_text(self, text: str, flush: bool = False) -> None: ...
    async def end_input(self) -> None: ...
    def audio(self) -> AsyncIterator[bytes]: ...
    async def close(self) -> None: ...


class TTSFactory(Protocol):
    def __call__(self) -> TTSStream: ...
