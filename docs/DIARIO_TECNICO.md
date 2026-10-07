# Diário técnico — Fluencto

Registro das decisões e do "porquê" de cada uma (material para entrevistas).

## Fase 1 — Esqueleto funcional ponta a ponta (2026-09-18)

**Decisões**
- **Deepgram Nova-3 em vez de Whisper API**: Whisper API é batch (manda o arquivo, espera). Deepgram é streaming por WebSocket, com endpointing embutido e suporte a `pt-BR`. Sem streaming, a meta de 1,5 s é inviável.
- **Claude Haiku 4.5**: o modelo mais rápido da família, suficiente para agendamento. O LLM é a etapa de maior variância; `max_tokens=300` e prompt de voz ("frases curtas") reduzem o tempo total da resposta.
- **ElevenLabs Flash v2.5 via WebSocket de input streaming**: aceita texto aos pedaços e devolve áudio aos pedaços. Saída `pcm_24000` para o navegador tocar sem decodificar.
- **Python + FastAPI + asyncio**: cada chamada tem ~4 tarefas concorrentes (receber áudio, eventos do STT, resposta LLM→TTS, bombeamento de áudio). asyncio resolve sem threads.
- **Raw WebSockets em vez dos SDKs de Deepgram/ElevenLabs**: controle total do protocolo e menos dependências; o código mostra o que acontece na rede.
- **Interfaces de provedor (`providers/base.py`)**: permitem testes E2E do orquestrador sem rede e trocar de provedor por configuração.
- **Disponibilidade calculada, não armazenada**: horários livres = expediente − agendamentos. Sem tabela de slots para ficar inconsistente.

**Bugs/cuidados encontrados**
- Histórico inválido após interrupção no meio de um tool call → solução: gravar o round de tool de forma atômica (sem ponto de suspensão).
- `speech_final` pode vir com texto vazio (a última palavra já veio num `is_final` anterior) → o fechamento de turno usa o buffer acumulado, não o texto do evento.
- Frase de espera "Deixa eu ver aqui." termina sem espaço antes da tool call, então o chunker não a liberava → `flush()` explícito ao receber `ToolCall`.
- Firefox não conecta MediaStreamSource a um AudioContext com sample rate diferente → captura na taxa nativa e resample dentro do worklet.

**Próximo**: medir latência real com as chaves, calibrar endpointing e chunking.
