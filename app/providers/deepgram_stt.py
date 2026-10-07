"""STT em streaming via WebSocket do Deepgram (Nova-3, pt-BR).

Detecção de fim de turno em duas camadas:
- endpointing (VAD, ~300ms de silêncio) → `speech_final=True`  → rápido
- utterance_end_ms (lacuna entre palavras) → msg `UtteranceEnd` → robusto a ruído
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect

from app.providers.base import STTEvent

log = logging.getLogger(__name__)


class DeepgramSTT:
    URL = "wss://api.deepgram.com/v1/listen"

    def __init__(self, api_key: str, model: str, language: str, sample_rate: int,
                 endpointing_ms: int, utterance_end_ms: int):
        self.api_key = api_key
        self.params = {
            "model": model,
            "language": language,
            "encoding": "linear16",
            "sample_rate": sample_rate,
            "channels": 1,
            "interim_results": "true",
            "endpointing": endpointing_ms,
            "utterance_end_ms": utterance_end_ms,
            "vad_events": "true",
            "smart_format": "true",
            "punctuate": "true",
        }
        self.ws: ClientConnection | None = None
        self._last_send = time.monotonic()
        self._keepalive_task: asyncio.Task | None = None

    async def connect(self) -> None:
        url = f"{self.URL}?{urlencode(self.params)}"
        self.ws = await connect(url, additional_headers={"Authorization": f"Token {self.api_key}"},
                                max_size=None, ping_interval=20)
        self._keepalive_task = asyncio.create_task(self._keepalive())

    async def send_audio(self, pcm16: bytes) -> None:
        if self.ws:
            await self.ws.send(pcm16)
            self._last_send = time.monotonic()

    async def _keepalive(self) -> None:
        # Deepgram fecha a conexão após ~10s sem dados.
        while self.ws:
            await asyncio.sleep(4)
            if time.monotonic() - self._last_send > 4 and self.ws:
                try:
                    await self.ws.send(json.dumps({"type": "KeepAlive"}))
                except Exception:  # noqa: BLE001
                    return

    async def events(self) -> AsyncIterator[STTEvent]:
        assert self.ws
        async for raw in self.ws:
            if isinstance(raw, bytes):
                continue
            msg = json.loads(raw)
            t = msg.get("type")
            if t == "Results":
                alt = msg["channel"]["alternatives"][0]
                text = alt.get("transcript", "").strip()
                words = alt.get("words") or []
                last_end = words[-1]["end"] if words else None
                if msg.get("is_final"):
                    yield STTEvent("final", text, bool(msg.get("speech_final")), last_end)
                elif text:
                    yield STTEvent("interim", text, False, last_end)
            elif t == "SpeechStarted":
                yield STTEvent("speech_started")
            elif t == "UtteranceEnd":
                yield STTEvent("utterance_end", last_word_end=msg.get("last_word_end"))
            elif t == "Error" or "err_code" in msg:
                log.error("Deepgram: %s", msg)

    async def close(self) -> None:
        if self._keepalive_task:
            self._keepalive_task.cancel()
        if self.ws:
            try:
                await self.ws.send(json.dumps({"type": "CloseStream"}))
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            self.ws = None
