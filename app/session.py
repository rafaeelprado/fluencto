"""Orquestrador de uma chamada de voz (1 WebSocket = 1 sessão).

Fluxo de um turno:

  mic ──PCM16──► Deepgram ──final/speech_final──► fim de turno
                                                     │
            ┌────────────────────────────────────────┘
            ▼
     Claude (stream) ──tokens──► SentenceChunker ──frases──► ElevenLabs (WS)
                                                                 │
  alto-falante ◄──────────────────PCM16 24k──────────────────────┘

Otimizações de latência aplicadas aqui:
1. Conexão TTS aberta EM PARALELO com a chamada ao LLM (esconde o handshake).
2. Primeira frase vai pro TTS assim que fecha (não espera a resposta inteira).
3. Frase de "espera" antes de tool calls é falada enquanto a tool roda.
4. Barge-in: se o usuário fala por cima, cancela LLM+TTS e zera o player.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any, AsyncIterator, Callable

from fastapi import WebSocket, WebSocketDisconnect

from app.agent.agent import Agent, AgentEvent, TextDelta, ToolCall, ToolResult, append_user_text, dumps_short
from app.agent.prompts import GREETING, build_system_prompt
from app.config import Settings
from app.db.database import ClinicDB
from app.metrics import TurnMetrics, now_ms
from app.providers.base import STTEvent, STTStream, TTSStream
from app.text_chunker import SentenceChunker

log = logging.getLogger(__name__)


class VoiceSession:
    def __init__(
        self,
        ws: WebSocket,
        settings: Settings,
        agent: Agent,
        db: ClinicDB,
        stt_factory: Callable[[], STTStream],
        tts_factory: Callable[[], TTSStream],
    ):
        self.ws = ws
        self.s = settings
        self.agent = agent
        self.db = db
        self.stt_factory = stt_factory
        self.tts_factory = tts_factory

        self.history: list[dict[str, Any]] = []
        self.stt: STTStream | None = None
        self._send_lock = asyncio.Lock()
        self._audio_bytes_in = 0
        self._user_buffer: list[str] = []
        self._last_word_end: float | None = None
        self._response: asyncio.Task | None = None
        self._playback_until = 0.0     # estimativa de quando o player do cliente termina (monotonic)
        self._turn_no = 0

    # ---------------------------------------------------------------- utils
    @property
    def audio_clock(self) -> float:
        """Segundos de áudio do microfone recebidos até agora."""
        return self._audio_bytes_in / (self.s.input_sample_rate * 2)

    async def send(self, payload: dict) -> None:
        async with self._send_lock:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False))

    async def send_audio(self, pcm: bytes) -> None:
        async with self._send_lock:
            await self.ws.send_bytes(pcm)
        dur = len(pcm) / (self.s.tts_sample_rate * 2)
        self._playback_until = max(self._playback_until, time.monotonic()) + dur

    def assistant_speaking(self) -> bool:
        running = self._response is not None and not self._response.done()
        return running or time.monotonic() < self._playback_until

    # ------------------------------------------------------------------ run
    async def run(self) -> None:
        self.stt = self.stt_factory()
        await self.stt.connect()
        await self.send({"type": "ready", "model": self.s.llm_model})
        tasks = [asyncio.create_task(self._recv_loop()), asyncio.create_task(self._stt_loop())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception() and not isinstance(t.exception(), WebSocketDisconnect):
                    log.error("Tarefa da sessão falhou", exc_info=t.exception())
        finally:
            for t in tasks:
                t.cancel()
            await self._cancel_response()
            await self.stt.close()

    async def _recv_loop(self) -> None:
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                self._audio_bytes_in += len(msg["bytes"])
                await self.stt.send_audio(msg["bytes"])
            elif msg.get("text"):
                data = json.loads(msg["text"])
                match data.get("type"):
                    case "start":
                        await self._greet()
                    case "text":   # modo digitado (debug sem microfone)
                        if data.get("text", "").strip():
                            await self._start_turn(data["text"].strip(), last_word_end=None)
                    case "interrupt":
                        await self._barge_in()
                    case "stop":
                        return

    async def _stt_loop(self) -> None:
        async for ev in self.stt.events():
            await self._on_stt(ev)

    async def _on_stt(self, ev: STTEvent) -> None:
        if not self.s.barge_in and self.assistant_speaking():
            return  # half-duplex: descarta o que o mic capta enquanto o agente fala (eco)
        if ev.kind == "interim":
            await self.send({"type": "transcript", "text": " ".join(self._user_buffer + [ev.text]), "final": False})
            if len(ev.text) >= self.s.barge_in_min_chars and self.assistant_speaking():
                await self._barge_in()
        elif ev.kind == "final":
            if ev.text:
                self._user_buffer.append(ev.text)
                self._last_word_end = ev.last_word_end or self._last_word_end
                await self.send({"type": "transcript", "text": " ".join(self._user_buffer), "final": False})
                if self.assistant_speaking():
                    await self._barge_in()
            if ev.speech_final and self._user_buffer:
                await self._end_of_turn()
        elif ev.kind == "utterance_end" and self._user_buffer:
            await self._end_of_turn()

    async def _end_of_turn(self) -> None:
        text = " ".join(self._user_buffer).strip()
        self._user_buffer.clear()
        await self.send({"type": "transcript", "text": text, "final": True})
        await self._start_turn(text, self._last_word_end)

    # -------------------------------------------------------------- turnos
    async def _greet(self) -> None:
        greeting = GREETING.format(clinic=self.s.clinic_name)
        self.history = [
            {"role": "user", "content": "(chamada iniciada)"},
            {"role": "assistant", "content": greeting},
        ]

        async def fixed() -> AsyncIterator[AgentEvent]:
            yield TextDelta(greeting)

        await self._launch(TurnMetrics(turn=0, t_end_of_turn=now_ms()), fixed())

    async def _start_turn(self, text: str, last_word_end: float | None) -> None:
        await self._cancel_response()
        self._turn_no += 1
        turn = TurnMetrics(
            turn=self._turn_no, user_text=text, t_end_of_turn=now_ms(),
            last_word_end_s=last_word_end,
            audio_clock_at_eot_s=self.audio_clock if last_word_end is not None else None,
        )
        if not self.history:
            self.history = [{"role": "user", "content": "(chamada iniciada)"},
                            {"role": "assistant", "content": GREETING.format(clinic=self.s.clinic_name)}]
        append_user_text(self.history, text)
        system = build_system_prompt(self.s.clinic_name, self.db.now())
        await self._launch(turn, self.agent.run(system, self.history))

    async def _launch(self, turn: TurnMetrics, events: AsyncIterator[AgentEvent]) -> None:
        await self._cancel_response()
        self._response = asyncio.create_task(self._respond(turn, events))

    async def _respond(self, turn: TurnMetrics, events: AsyncIterator[AgentEvent]) -> None:
        await self.send({"type": "state", "state": "thinking"})
        tts = self.tts_factory()
        connect_task = asyncio.create_task(tts.connect())   # (1) handshake em paralelo com o LLM
        pump_task: asyncio.Task | None = None
        chunker = SentenceChunker()
        spoken: list[str] = []

        async def pump() -> None:
            async for pcm in tts.audio():
                if turn.mark("t_tts_first_audio", self.audio_clock):
                    await self.send({"type": "state", "state": "speaking"})
                await self.send_audio(pcm)

        async def speak(sentence: str) -> None:
            nonlocal pump_task
            turn.mark("t_first_sentence")
            if pump_task is None:
                await connect_task
                pump_task = asyncio.create_task(pump())
            await tts.send_text(sentence, flush=True)
            spoken.append(sentence)
            await self.send({"type": "assistant", "text": sentence})

        try:
            async for ev in events:
                if isinstance(ev, TextDelta):
                    turn.mark("t_llm_first_token")
                    for sentence in chunker.feed(ev.text):          # (2) frase a frase
                        await speak(sentence)
                elif isinstance(ev, ToolCall):
                    if rest := chunker.flush():                     # (3) "Deixa eu ver aqui."
                        await speak(rest)
                    turn.tool_calls.append(ev.name)
                    await self.send({"type": "tool_call", "name": ev.name, "args": ev.args})
                elif isinstance(ev, ToolResult):
                    await self.send({"type": "tool_result", "name": ev.name,
                                     "result": dumps_short(ev.result), "is_error": ev.is_error})
            if rest := chunker.flush():
                await speak(rest)
            if pump_task:
                await tts.end_input()
                await pump_task
            turn.mark("t_done")
            await self.send({"type": "metrics", **turn.summary()})
            log.info("turno %s: %s", turn.turn, turn.summary())
        except asyncio.CancelledError:
            turn.interrupted = True
            # Se o LLM não terminou, registra o que chegou a ser falado (mantém o contexto honesto).
            if self.history and self.history[-1]["role"] == "user" and spoken:
                self.history.append({"role": "assistant",
                                     "content": " ".join(spoken) + " [interrompido pelo usuário]"})
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("Erro no turno")
            await self.send({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            if pump_task and not pump_task.done():
                pump_task.cancel()
            connect_task.cancel()
            await tts.close()
            with contextlib.suppress(Exception):
                await self.send({"type": "state", "state": "listening"})

    async def _cancel_response(self) -> None:
        if self._response and not self._response.done():
            self._response.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._response
        self._response = None

    async def _barge_in(self) -> None:
        """(4) Usuário falou por cima: para tudo e manda o cliente zerar o player."""
        await self._cancel_response()
        self._playback_until = 0.0
        await self.send({"type": "interrupt"})
