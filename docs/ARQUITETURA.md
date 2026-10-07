# Arquitetura do Fluencto

## Princípio: latência é soma de etapas em série — então elimine o "em série"

Um pipeline ingênuo faz: grava tudo → transcreve → LLM gera tudo → TTS gera tudo → toca. Cada etapa espera a anterior terminar: 4–8 s.
O Fluencto sobrepõe as etapas:

1. **STT em streaming**: o áudio vai para o Deepgram a cada 20 ms. Quando o usuário para de falar, a transcrição já está pronta; só falta *decidir* que o turno acabou.
2. **LLM em streaming**: os tokens chegam um a um.
3. **Chunking por frase**: a 1ª frase vai para o TTS assim que fecha (a 1ª pode ser cortada numa vírgula depois de ~35 caracteres).
4. **TTS em streaming (input + output)**: o ElevenLabs recebe frase a frase pelo WebSocket e devolve áudio em pedaços.
5. **Player incremental**: o AudioWorklet toca cada pedaço assim que chega.

Resultado: o tempo percebido ≈ endpointing + TTFT do LLM + 1ª frase + TTFB do TTS, e não a soma das durações completas.

## Protocolo do WebSocket (`/ws`)

| Direção | Tipo | Conteúdo |
|---|---|---|
| cliente → servidor | binário | PCM16 mono 16 kHz, frames de 20 ms |
| cliente → servidor | `{"type":"start"}` | toca a saudação |
| cliente → servidor | `{"type":"text","text":...}` | turno digitado (debug) |
| cliente → servidor | `{"type":"stop"}` | encerra |
| servidor → cliente | binário | PCM16 mono 24 kHz (voz do agente) |
| servidor → cliente | `ready`, `state`, `transcript`, `assistant`, `tool_call`, `tool_result`, `metrics`, `interrupt`, `error` | eventos JSON |

Por que PCM cru e não MP3? MP3 exige decodificar em blocos (`decodeAudioData` não é streaming) e adiciona latência + cliques entre pedaços. PCM vai direto para o buffer do worklet, e o `clear` no barge-in é instantâneo.

## Detecção de fim de turno

- `endpointing=300`: o VAD do Deepgram marca `speech_final` após 300 ms de silêncio → caminho rápido.
- `utterance_end_ms=1000`: fallback baseado em lacuna entre palavras, imune a ruído de fundo.
- As transcrições `is_final` são acumuladas num buffer; o turno fecha no primeiro dos dois sinais.

Trade-off: endpointing menor = mais rápido, porém corta quem faz pausas ("meu telefone é... 79..."). 300 ms é um bom começo; ver roadmap (turn detection semântico).

## Barge-in

Com o agente falando (resposta em curso **ou** áudio ainda tocando no cliente — estimado pela duração do PCM enviado), qualquer transcrição do usuário ≥ 3 caracteres:
1. cancela a task da resposta (LLM + TTS);
2. envia `interrupt` → o worklet do cliente descarta a fila;
3. registra no histórico o que chegou a ser dito + `[interrompido pelo usuário]`.

Usar a *transcrição* (e não o VAD) como gatilho evita que tosse ou barulho interrompam o agente.
Eco: com caixa de som, o microfone pode captar a voz do agente. O navegador faz cancelamento de eco (o player é roteado por um `<audio>` para ajudar), mas para demos sem fone use `BARGE_IN=false` (half-duplex).

## Tool calling e consistência do histórico

A Messages API exige que todo `tool_use` seja seguido de um `tool_result`. Se o usuário interrompe no meio de um round, o histórico não pode ficar pela metade. Por isso o `Agent` executa as tools e grava `assistant(tool_use)` + `user(tool_result)` **sem nenhum `await`/`yield` entre eles**: ou o round inteiro entra, ou nada entra.

Antes de chamar uma tool o modelo diz uma frase curta ("Deixa eu ver aqui."), que é falada enquanto a tool executa — mascara a latência do 2º round do LLM.

## Métricas

- Relógio do servidor: `llm_ttft_ms`, `first_sentence_ms`, `tts_first_audio_ms` (a partir do fechamento do turno).
- Relógio do áudio: `voice_to_voice_ms = (segundos de áudio recebidos no 1º byte de TTS) − (fim da última palavra, timestamp do Deepgram)`. Como o mic chega em tempo real, isso mede o silêncio que o usuário realmente percebe, incluindo o endpointing.
- Cliente: `Cliente` = do evento de transcrição final até o 1º sample tocado (inclui rede de volta e buffer).
