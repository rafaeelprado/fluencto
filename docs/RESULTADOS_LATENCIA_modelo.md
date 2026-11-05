# Modelo da seção "Resultados medidos" (Fluencto)

Preencha só com números do SEU painel de latência. Depois cole a tabela no README.md,
logo abaixo de "Orçamento de latência (alvo)", e apague este arquivo.

## Como medir (15 minutos)

1. Rode o servidor e abra http://localhost:8000 no Chrome, com fone de ouvido.
2. Faça 20 turnos de conversa com as mesmas 5 frases repetidas 4 vezes, por exemplo:
   - "Oi, queria marcar uma consulta de dermatologia."
   - "Tem horário amanhã de manhã?"
   - "Pode ser às nove."
   - "Meu nome é Carlos Silva."
   - "Quanto custa a consulta?"
3. Anote, para cada turno, os valores que o painel da direita mostra por etapa.
4. Calcule mediana e p95 de cada coluna (planilha: MEDIANA e PERCENTIL com 0,95).
5. Anote o ambiente: sistema, rede (Wi-Fi ou cabo), cidade, modelo do Claude, voz do ElevenLabs.

## Tabela para colar

| Etapa | Alvo | Mediana | p95 |
|---|---|---|---|
| Endpointing (fim da fala até fechar o turno) | 300 a 400 ms | [preencha] | [preencha] |
| Claude, primeiro token | 350 a 600 ms | [preencha] | [preencha] |
| Primeira frase completa | +100 a 200 ms | [preencha] | [preencha] |
| ElevenLabs, primeiro áudio | 150 a 250 ms | [preencha] | [preencha] |
| **Voz a voz** | **menos de 1,5 s** | [preencha] | [preencha] |

Medido em 20 turnos, [sistema], [tipo de rede], [cidade], Claude Haiku 4.5, ElevenLabs Flash v2.5.

## Se a meta de 1,5 s não bater

Isso também é conteúdo bom. Escreva uma linha dizendo qual etapa passou do alvo e o que você
tentaria (outro endpointing, frase menor no primeiro chunk, região do servidor).
