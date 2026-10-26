# Fluencto — Real-time Voice AI Agent

🇺🇸 **English** · 🇧🇷 [Português](README.md)

A voice receptionist for a (fictional) clinic, "Clínica Vitalis". It runs in the browser, understands the patient, queries a local SQLite database through **tool calling**, and answers with natural speech. Target: **< 1.5 s voice-to-voice**.

```
Browser (AudioWorklet)                       Server (FastAPI, asyncio)
┌───────────────────────┐   WebSocket   ┌──────────────────────────────────────────────┐
│ mic → PCM16 16 kHz ───┼──────────────►│ Deepgram Nova-3 (WS, pt-BR, endpointing)     │
│                       │               │        │ end of turn                          │
│                       │               │        ▼                                      │
│                       │               │ Claude Haiku 4.5 (stream + tools) ◄─► SQLite  │
│                       │               │        │ tokens → sentences (SentenceChunker) │
│                       │               │        ▼                                      │
│ player ◄─ PCM16 24k ──┼◄──────────────│ ElevenLabs Flash v2.5 (WS input streaming)   │
└───────────────────────┘               └──────────────────────────────────────────────┘
```

> **Status:** the latency figures below are the design *budget* (targets), not measurements. Measuring real latency with live API keys and calibrating endpointing and chunking is the documented next step (see [`docs/DIARIO_TECNICO.md`](docs/DIARIO_TECNICO.md)). The test suite runs offline with fake providers.

## Running it (Windows / PowerShell)

```powershell
cd C:\Fluencto
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env      # fill in the 3 keys
uvicorn app.main:app --reload
```

Open **http://localhost:8000** in Chrome, click *Iniciar chamada* (start call) and talk. Use **headphones** to test interruptions (barge-in).

Keys (all have free starting credit):
- Anthropic: https://console.anthropic.com
- Deepgram: https://console.deepgram.com
- ElevenLabs: https://elevenlabs.io → Profile → API Keys (pick a Portuguese voice in the Voice Library and paste its ID into `ELEVENLABS_VOICE_ID`)

Tests (no network, fake providers): `pytest -q`

## What the project demonstrates

| Technique | Where |
|---|---|
| End-to-end streaming (no stage waits for the previous one to finish) | `app/session.py` |
| Two-layer end-of-turn detection (300 ms VAD + UtteranceEnd) | `app/providers/deepgram_stt.py` |
| LLM tokens → sentences → incremental TTS | `app/text_chunker.py` |
| TTS handshake in parallel with the LLM | `VoiceSession._respond` |
| Tool calling with always-valid history (atomicity on barge-in) | `app/agent/agent.py` |
| Barge-in: cancels LLM + TTS and flushes the client player | `VoiceSession._barge_in` + `worklets.js` |
| Audio in an AudioWorklet (dedicated thread), raw PCM with no MP3 decoding | `static/worklets.js` |
| Latency measured on the audio clock (true voice-to-voice) | `app/metrics.py` |
| Providers behind interfaces → network-free E2E tests | `app/providers/base.py`, `tests/` |

## Latency budget (target)

| Stage | Target |
|---|---|
| Endpointing (silence until the turn closes) | ~300–400 ms |
| Claude Haiku — first token | ~350–600 ms |
| First complete sentence | +100–200 ms |
| ElevenLabs Flash — first audio | ~150–250 ms |
| **Voice-to-voice** | **~0.9–1.4 s** |

The right-hand panel in the UI shows each stage per turn. Details in [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md); design decisions in [`docs/DIARIO_TECNICO.md`](docs/DIARIO_TECNICO.md). (Both documents are in Portuguese.)

## Structure

```
app/
  main.py              FastAPI: /, /ws, /health, /api/agenda
  session.py           call orchestrator (state machine + barge-in)
  text_chunker.py      token stream → sentences
  metrics.py           per-turn latency metrics
  config.py            .env
  agent/               voice prompt, tools, agent loop
  providers/           Deepgram, Claude, ElevenLabs (+ interfaces)
  db/database.py       SQLite: services, professionals, schedule, FAQ
static/                UI + AudioWorklets
tests/                 unit tests + WebSocket E2E with fakes
```

## Stack

`FastAPI` · `asyncio` · `WebSockets` · `Deepgram Nova-3` · `Claude Haiku 4.5` · `ElevenLabs Flash v2.5` · `SQLite` · `AudioWorklet` · `pytest`
