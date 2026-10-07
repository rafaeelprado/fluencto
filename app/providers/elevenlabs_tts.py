"""TTS em streaming via WebSocket do ElevenLabs (input streaming).

Mandamos o texto frase a frase conforme o LLM gera; o áudio volta em PCM16 cru,
que o navegador toca direto (sem decodificar MP3).
"""
from __future__ import annotations

import base64
import json
import logging
from typing import AsyncIterator
from urllib.parse import urlencode

from websockets.asyncio.client import ClientConnection, connect

log = logging.getLogger(__name__)


class ElevenLabsTTS:
    def __init__(self, api_key: str, voice_id: str, model: str, sample_rate: int):
        self.api_key = api_key
        self.url = (
            f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?"
            + urlencode({
                "model_id": model,
                "output_format": f"pcm_{sample_rate}",
                "language_code": "pt",
                "inactivity_timeout": 30,
            })
        )
        self.ws: ClientConnection | None = None

    async def connect(self) -> None:
        self.ws = await connect(self.url, additional_headers={"xi-api-key": self.api_key}, max_size=None)
        # 1ª mensagem obrigatória: inicializa a geração (texto = espaço).
        await self.ws.send(json.dumps({
            "text": " ",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "speed": 1.05},
            # Buffer mínimo de caracteres antes de gerar. Baixo no início = 1º áudio mais cedo.
            "generation_config": {"chunk_length_schedule": [50, 90, 120, 160]},
        }))

    async def send_text(self, text: str, flush: bool = False) -> None:
        assert self.ws
        # O ElevenLabs espera o texto terminando em espaço para concatenar corretamente.
        await self.ws.send(json.dumps({"text": text.strip() + " ", "flush": flush}))

    async def end_input(self) -> None:
        if self.ws:
            await self.ws.send(json.dumps({"text": ""}))  # EOS: gera o restante e fecha

    async def audio(self) -> AsyncIterator[bytes]:
        assert self.ws
        async for raw in self.ws:
            msg = json.loads(raw)
            if msg.get("audio"):
                yield base64.b64decode(msg["audio"])
            if msg.get("isFinal") or msg.get("is_final"):
                return
            if msg.get("error") or msg.get("message") and not msg.get("audio"):
                log.error("ElevenLabs: %s", msg)

    async def close(self) -> None:
        if self.ws:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            self.ws = None
