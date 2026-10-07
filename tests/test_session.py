"""Teste de ponta a ponta do orquestrador com provedores falsos (sem rede)."""
import json

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.agent.agent import Agent
from app.agent.tools import ToolExecutor
from app.config import Settings
from app.db.database import ClinicDB
from app.session import VoiceSession
from tests.fakes import FakeSTT, FakeTTS, ScriptedLLM


def make_client(llm: ScriptedLLM, tts_delay: float = 0.0, holder: dict | None = None) -> TestClient:
    db = ClinicDB(":memory:")
    db.seed_if_empty()
    settings = Settings(anthropic_api_key="x", deepgram_api_key="x", elevenlabs_api_key="x")
    app = FastAPI()

    @app.websocket("/ws")
    async def ws_route(ws: WebSocket):
        await ws.accept()
        s = VoiceSession(ws, settings, Agent(llm, ToolExecutor(db)), db,
                         stt_factory=FakeSTT, tts_factory=lambda: FakeTTS(tts_delay))
        if holder is not None:
            holder["session"] = s
        await s.run()

    return TestClient(app)


def collect_until(ws, pred, limit=200):
    events, audio = [], 0
    for _ in range(limit):
        m = ws.receive()
        if m.get("bytes"):
            audio += len(m["bytes"])
            continue
        ev = json.loads(m["text"])
        events.append(ev)
        if pred(ev):
            return events, audio
    raise AssertionError(f"evento esperado não chegou: {events}")


def test_turno_completo_com_tool_call():
    llm = ScriptedLLM([
        ("Deixa eu ver aqui.", [("buscar_horarios_disponiveis", {"servico": "Dermatologia"})]),
        "Tenho quinta às duas da tarde. Pode ser?",
    ])
    holder: dict = {}
    with make_client(llm, holder=holder).websocket_connect("/ws") as ws:
        collect_until(ws, lambda e: e["type"] == "ready")
        ws.send_text(json.dumps({"type": "start"}))
        _, greet_audio = collect_until(ws, lambda e: e["type"] == "metrics" and e["turn"] == 0)
        assert greet_audio > 0

        ws.send_bytes(b"SAY:Quero marcar dermatologia")
        events, audio = collect_until(ws, lambda e: e["type"] == "metrics" and e["turn"] == 1)

    types = [e["type"] for e in events]
    assert "tool_call" in types and "tool_result" in types
    said = [e["text"] for e in events if e["type"] == "assistant"]
    assert said[0] == "Deixa eu ver aqui."          # frase de espera sai ANTES da tool
    assert types.index("assistant") < types.index("tool_call")
    assert audio > 0
    m = events[-1]
    assert m["tool_calls"] == ["buscar_horarios_disponiveis"]
    assert m["llm_ttft_ms"] is not None and m["tts_first_audio_ms"] is not None

    # Histórico válido para a Messages API: tool_use seguido de tool_result com o mesmo id.
    h = holder["session"].history
    roles = [x["role"] for x in h]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles
    tool_use = next(b for x in h if x["role"] == "assistant" and isinstance(x["content"], list)
                    for b in x["content"] if b["type"] == "tool_use")
    tool_res = next(b for x in h if x["role"] == "user" and isinstance(x["content"], list)
                    for b in x["content"] if b.get("type") == "tool_result")
    assert tool_use["id"] == tool_res["tool_use_id"]


def test_barge_in_interrompe_o_agente():
    long_answer = " ".join(["Temos vários horários disponíveis nesta semana."] * 30)
    llm = ScriptedLLM([long_answer, "Claro, pode falar."], token_delay=0.01)
    holder: dict = {}
    with make_client(llm, tts_delay=0.0, holder=holder).websocket_connect("/ws") as ws:
        collect_until(ws, lambda e: e["type"] == "ready")
        ws.send_bytes(b"SAY:Quais horarios voces tem")
        collect_until(ws, lambda e: e["type"] == "assistant")   # agente começou a falar
        ws.send_bytes(b"SAY:Espera, na verdade quero outra coisa")
        events, _ = collect_until(ws, lambda e: e["type"] == "interrupt")
        events, _ = collect_until(ws, lambda e: e["type"] == "metrics" and e["turn"] == 2)

    h = holder["session"].history
    interrompida = [x for x in h if x["role"] == "assistant" and "interrompido" in str(x["content"])]
    assert interrompida, "a fala cortada deve ficar marcada no histórico"
    roles = [x["role"] for x in h]
    assert all(a != b for a, b in zip(roles, roles[1:])), roles
