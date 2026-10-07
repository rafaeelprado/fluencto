# Fluencto — Voice AI Agent em tempo real

Recepcionista de voz para uma clínica (fictícia, "Clínica Vitalis"). Roda no navegador, entende o paciente, consulta uma base local (SQLite) via **tool calling** e responde com voz natural — meta: **< 1,5 s voz-a-voz**.

```
Navegador (AudioWorklet)                     Servidor (FastAPI, asyncio)
┌───────────────────────┐   WebSocket   ┌──────────────────────────────────────────────┐
│ mic → PCM16 16 kHz ───┼──────────────►│ Deepgram Nova-3 (WS, pt-BR, endpointing)     │
│                       │               │        │ fim de turno                         │
│                       │               │        ▼                                      │
│                       │               │ Claude Haiku 4.5 (stream + tools) ◄─► SQLite  │
│                       │               │        │ tokens → frases (SentenceChunker)    │
│                       │               │        ▼                                      │
│ player ◄─ PCM16 24k ──┼◄──────────────│ ElevenLabs Flash v2.5 (WS input streaming)   │
└───────────────────────┘               └──────────────────────────────────────────────┘
```

## Rodando (Windows / PowerShell)

```powershell
cd C:\Fluencto
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
copy .env.example .env      # preencha as 3 chaves
uvicorn app.main:app --reload
```

Abra **http://localhost:8000** no Chrome, clique em *Iniciar chamada* e fale. Use **fone de ouvido** para testar interrupções (barge-in).

Chaves (todas têm crédito grátis inicial):
- Anthropic: https://console.anthropic.com
- Deepgram: https://console.deepgram.com
- ElevenLabs: https://elevenlabs.io → Profile → API Keys (escolha uma voz em português na Voice Library e cole o ID em `ELEVENLABS_VOICE_ID`)

Testes (sem rede, com provedores falsos): `pytest -q`

## O que o projeto demonstra

| Técnica | Onde |
|---|---|
| Streaming ponta a ponta (nenhuma etapa espera a anterior terminar) | `app/session.py` |
| Fim de turno em duas camadas (VAD 300 ms + UtteranceEnd) | `app/providers/deepgram_stt.py` |
| Tokens do LLM → frases → TTS incremental | `app/text_chunker.py` |
| Handshake do TTS em paralelo com o LLM | `VoiceSession._respond` |
| Tool calling com histórico sempre válido (atomicidade no barge-in) | `app/agent/agent.py` |
| Barge-in: cancela LLM+TTS e zera o player do cliente | `VoiceSession._barge_in` + `worklets.js` |
| Áudio em AudioWorklet (thread dedicada), PCM cru sem decodificar MP3 | `static/worklets.js` |
| Latência medida no relógio do áudio (voz-a-voz real) | `app/metrics.py` |
| Provedores atrás de interfaces → testes E2E sem rede | `app/providers/base.py`, `tests/` |

## Orçamento de latência (alvo)

| Etapa | Alvo |
|---|---|
| Endpointing (silêncio até fechar o turno) | ~300–400 ms |
| Claude Haiku — 1º token | ~350–600 ms |
| 1ª frase completa | +100–200 ms |
| ElevenLabs Flash — 1º áudio | ~150–250 ms |
| **Voz-a-voz** | **~0,9–1,4 s** |

O painel da direita mostra cada etapa por turno. Detalhes em [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) e decisões em [`docs/DIARIO_TECNICO.md`](docs/DIARIO_TECNICO.md).

## Estrutura

```
app/
  main.py              FastAPI: /, /ws, /health, /api/agenda
  session.py           orquestrador da chamada (máquina de estados + barge-in)
  text_chunker.py      stream de tokens → frases
  metrics.py           métricas de latência por turno
  config.py            .env
  agent/               prompt de voz, tools, loop agente
  providers/           Deepgram, Claude, ElevenLabs (+ interfaces)
  db/database.py       SQLite: serviços, profissionais, agenda, FAQ
static/                UI + AudioWorklets
tests/                 unitários + E2E do WebSocket com fakes
```
