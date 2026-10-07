"""Métricas de latência por turno.

Duas réguas:
1. Relógio do servidor (time.perf_counter): quanto cada etapa do pipeline levou.
2. Relógio do ÁUDIO: segundos de áudio recebidos do microfone. Como o mic chega
   em tempo real, "segundos de áudio recebidos no momento do 1º byte de TTS"
   menos "fim da última palavra (timestamp do Deepgram)" = latência voz-a-voz
   percebida pelo usuário (sem contar rede de volta + buffer do player).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


def now_ms() -> float:
    return time.perf_counter() * 1000


@dataclass
class TurnMetrics:
    turn: int
    user_text: str = ""
    t_end_of_turn: float = 0.0          # servidor decidiu que o usuário terminou
    t_llm_first_token: float | None = None
    t_first_sentence: float | None = None
    t_tts_first_audio: float | None = None
    t_done: float | None = None
    last_word_end_s: float | None = None     # timestamp do Deepgram (relógio do áudio)
    audio_clock_at_eot_s: float | None = None
    audio_clock_at_first_audio_s: float | None = None
    tool_calls: list[str] = field(default_factory=list)
    interrupted: bool = False

    def mark(self, attr: str, audio_clock: float | None = None) -> bool:
        """Marca o timestamp só na primeira vez. Retorna True se marcou agora."""
        if getattr(self, attr) is None:
            setattr(self, attr, now_ms())
            if attr == "t_tts_first_audio" and audio_clock is not None:
                self.audio_clock_at_first_audio_s = audio_clock
            return True
        return False

    def _delta(self, a: float | None) -> int | None:
        return None if a is None else round(a - self.t_end_of_turn)

    def summary(self) -> dict:
        endpointing = voice_to_voice = None
        if self.last_word_end_s is not None and self.audio_clock_at_eot_s is not None:
            endpointing = round((self.audio_clock_at_eot_s - self.last_word_end_s) * 1000)
        if self.last_word_end_s is not None and self.audio_clock_at_first_audio_s is not None:
            voice_to_voice = round((self.audio_clock_at_first_audio_s - self.last_word_end_s) * 1000)
        return {
            "turn": self.turn,
            "endpointing_ms": endpointing,          # fim da fala → turno fechado
            "llm_ttft_ms": self._delta(self.t_llm_first_token),
            "first_sentence_ms": self._delta(self.t_first_sentence),
            "tts_first_audio_ms": self._delta(self.t_tts_first_audio),  # turno fechado → 1º áudio
            "voice_to_voice_ms": voice_to_voice,    # fim da fala → 1º áudio (métrica principal)
            "total_ms": self._delta(self.t_done),
            "tool_calls": self.tool_calls,
            "interrupted": self.interrupted,
        }

    def raw(self) -> dict:
        return asdict(self)
