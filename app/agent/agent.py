"""Loop agente: LLM → (tool_use → executa → tool_result)* → resposta final.

Emite eventos em streaming para o orquestrador de voz falar enquanto pensa.
O histórico só recebe rounds COMPLETOS (tool_use + tool_result juntos), então
uma interrupção (barge-in) nunca deixa o histórico num estado inválido.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.agent.tools import TOOLS, ToolExecutor
from app.providers.base import LLMClient, LLMFinal, LLMText


@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class ToolResult:
    name: str
    result: str
    is_error: bool


AgentEvent = TextDelta | ToolCall | ToolResult


class Agent:
    def __init__(self, llm: LLMClient, tools: ToolExecutor, max_tool_rounds: int = 4):
        self.llm = llm
        self.tools = tools
        self.max_tool_rounds = max_tool_rounds

    async def run(self, system: str, history: list[dict[str, Any]]) -> AsyncIterator[AgentEvent]:
        """`history` deve terminar com a mensagem do usuário. É mutado in-place."""
        for _ in range(self.max_tool_rounds + 1):
            final: LLMFinal | None = None
            async for ev in self.llm.stream(system, history, TOOLS):
                if isinstance(ev, LLMText):
                    yield TextDelta(ev.text)
                else:
                    final = ev
            assert final is not None

            tool_uses = [b for b in final.content if b.get("type") == "tool_use"]
            if not tool_uses:
                history.append({"role": "assistant", "content": final.content})
                return

            # Executa as tools e grava no histórico SEM nenhum ponto de suspensão (await/yield)
            # no meio: se o usuário interromper, ou o round inteiro entra, ou nada entra.
            # (SQLite local responde em ~1ms; uma tool lenta/remota iria para asyncio.to_thread + shield.)
            events: list[AgentEvent] = []
            results = []
            for tu in tool_uses:
                out, is_err = self.tools.run(tu["name"], tu["input"])
                events += [ToolCall(tu["name"], tu["input"]), ToolResult(tu["name"], out, is_err)]
                results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": out, "is_error": is_err})
            history.append({"role": "assistant", "content": final.content})
            history.append({"role": "user", "content": results})
            for ev in events:
                yield ev

        history.append({"role": "assistant", "content": [{"type": "text", "text": "Desculpe, tive um problema aqui."}]})
        yield TextDelta("Desculpe, tive um problema aqui. Pode repetir?")


def append_user_text(history: list[dict[str, Any]], text: str) -> None:
    """Mantém alternância user/assistant: se a última msg já é do usuário, concatena."""
    if history and history[-1]["role"] == "user":
        last = history[-1]
        content = last["content"]
        if isinstance(content, str):
            last["content"] = [{"type": "text", "text": content}]
        last["content"].append({"type": "text", "text": text})
    else:
        history.append({"role": "user", "content": text})


def dumps_short(obj: Any, limit: int = 400) -> str:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + "…"
