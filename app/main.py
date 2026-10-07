"""Fluencto — servidor FastAPI.

Rodar:  uvicorn app.main:app --reload
Abrir:  http://localhost:8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.agent import Agent
from app.agent.tools import ToolExecutor
from app.config import ROOT, get_settings
from app.db.database import ClinicDB
from app.providers.claude_llm import ClaudeLLM
from app.providers.deepgram_stt import DeepgramSTT
from app.providers.elevenlabs_tts import ElevenLabsTTS
from app.session import VoiceSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fluencto")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = ClinicDB(settings.db_path, settings.timezone)
    db.seed_if_empty()
    app.state.db = db
    # Um cliente LLM por processo (reuso de conexão HTTP/TLS).
    app.state.llm = ClaudeLLM(settings.anthropic_api_key, settings.llm_model,
                              settings.llm_max_tokens, settings.llm_temperature)
    if missing := settings.missing_keys():
        log.warning("Chaves ausentes no .env: %s", ", ".join(missing))
    yield
    db.conn.close()


app = FastAPI(title="Fluencto", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
async def health():
    return {"ok": True, "missing_keys": settings.missing_keys(), "llm": settings.llm_model}


@app.get("/api/agenda")
async def agenda():
    """Painel da demo: próximos agendamentos (para ver as tools mexendo no banco)."""
    rows = app.state.db.conn.execute(
        """SELECT a.id, a.inicio, a.status, s.nome servico, p.nome profissional, pa.nome paciente
           FROM agendamentos a JOIN servicos s ON s.id=a.servico_id
           JOIN profissionais p ON p.id=a.profissional_id JOIN pacientes pa ON pa.id=a.paciente_id
           ORDER BY a.criado_em DESC, a.id DESC LIMIT 15"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    if missing := settings.missing_keys():
        await ws.send_json({"type": "error", "message": f"Configure no .env: {', '.join(missing)}"})
        await ws.close()
        return
    s = settings
    session = VoiceSession(
        ws=ws,
        settings=s,
        agent=Agent(app.state.llm, ToolExecutor(app.state.db), s.llm_max_tool_rounds),
        db=app.state.db,
        stt_factory=lambda: DeepgramSTT(s.deepgram_api_key, s.deepgram_model, s.deepgram_language,
                                        s.input_sample_rate, s.deepgram_endpointing_ms, s.deepgram_utterance_end_ms),
        tts_factory=lambda: ElevenLabsTTS(s.elevenlabs_api_key, s.elevenlabs_voice_id,
                                          s.elevenlabs_model, s.tts_sample_rate),
    )
    try:
        await session.run()
    except Exception:  # noqa: BLE001
        log.exception("Sessão encerrada com erro")
