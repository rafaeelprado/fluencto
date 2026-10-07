"""Quebra o stream de tokens do LLM em frases prontas para o TTS.

Por que isso importa: o TTS não pode esperar a resposta inteira do LLM.
Mandamos a primeira frase assim que ela fecha — é aqui que se ganha ~0,5-1s.

Trade-off: pedaços menores = áudio mais cedo, mas prosódia pior.
Por isso a PRIMEIRA frase pode ser cortada numa vírgula (latência), e as
seguintes só em pontuação final (naturalidade).
"""
import re

_ABREVIACOES = {"dr", "dra", "sr", "sra", "srta", "prof", "profa", "av", "n", "nº", "tel", "etc"}
_FINAL = re.compile(r"[.!?…]+[\"')\]]*\s")
_SOFT = re.compile(r"[,;:—]\s")


class SentenceChunker:
    def __init__(self, first_soft_min: int = 35, soft_min: int = 120):
        self.buf = ""
        self.first = True
        self.first_soft_min = first_soft_min
        self.soft_min = soft_min

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            chunk = self._next_chunk()
            if chunk is None:
                break
            out.append(chunk)
        return out

    def flush(self) -> str | None:
        rest, self.buf = self.buf.strip(), ""
        return rest or None

    def _next_chunk(self) -> str | None:
        for m in _FINAL.finditer(self.buf):
            before = self.buf[: m.start()]
            last_word = before.rsplit(" ", 1)[-1].lower().strip("(\"'")
            if last_word in _ABREVIACOES:
                continue
            if m.group().startswith(".") and before[-1:].isdigit() and self.buf[m.end():m.end() + 1].isdigit():
                continue  # "1.500"
            return self._cut(m.end())
        limit = self.first_soft_min if self.first else self.soft_min
        if len(self.buf) >= limit:
            soft = [m for m in _SOFT.finditer(self.buf) if m.end() >= limit * 0.6]
            if soft:
                return self._cut(soft[0].end())
        return None

    def _cut(self, idx: int) -> str | None:
        chunk, self.buf = self.buf[:idx].strip(), self.buf[idx:]
        if not chunk:
            return None
        self.first = False
        return chunk
