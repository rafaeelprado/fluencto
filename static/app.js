// Fluencto — cliente de voz no navegador.
// Protocolo WS: frames binários = áudio PCM16 (mic→16k / voz→24k); frames texto = eventos JSON.

const $ = (id) => document.getElementById(id);
const els = {
  orb: $("orb"), state: $("stateLabel"), btn: $("callBtn"), transcript: $("transcript"),
  form: $("textForm"), input: $("textInput"), textBtn: $("textBtn"), health: $("health"),
  latBig: $("latBig"), latAvg: $("latAvg"), latBar: $("latBar"), rows: $("turnRows"),
  tools: $("tools"), agenda: $("agenda"),
};

const TTS_RATE = 24000;
let ws = null, micCtx = null, playCtx = null, micNode = null, player = null, stream = null;
let partialEl = null, assistantEl = null;
let turnEndAt = null, clientLatency = null;
const v2v = [];

// ------------------------------------------------------------------ UI helpers
const STATE_LABEL = { idle: "Pronto para ligar", listening: "Ouvindo…", thinking: "Pensando…", speaking: "Falando…" };
function setState(s) {
  els.orb.className = `orb ${s}`;
  els.state.textContent = STATE_LABEL[s] ?? s;
}
function setLevel(v) { els.orb.style.setProperty("--lvl", Math.min(1, v * 6).toFixed(3)); }

function addMsg(role, text) {
  els.transcript.querySelector(".empty")?.remove();
  const p = document.createElement("div");
  p.className = `msg ${role}`;
  p.textContent = text;
  els.transcript.appendChild(p);
  els.transcript.scrollTop = els.transcript.scrollHeight;
  return p;
}

function addTool(name, args) {
  if (els.tools.querySelector(".muted")) els.tools.innerHTML = "";
  const li = document.createElement("li");
  li.innerHTML = `<b></b> <span class="args"></span><span class="res">…</span>`;
  li.querySelector("b").textContent = name;
  li.querySelector(".args").textContent = JSON.stringify(args);
  els.tools.prepend(li);
  return li;
}

async function loadAgenda() {
  const rows = await fetch("/api/agenda").then((r) => r.json()).catch(() => []);
  els.agenda.innerHTML = "";
  for (const r of rows) {
    const tr = document.createElement("tr");
    if (r.status === "cancelado") tr.className = "cancelado";
    const d = new Date(r.inicio);
    const when = d.toLocaleString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    for (const v of [when, r.paciente, r.servico, r.profissional]) {
      const td = document.createElement("td"); td.textContent = v; tr.appendChild(td);
    }
    els.agenda.appendChild(tr);
  }
}

function renderMetrics(m) {
  const fmt = (x) => (x == null ? "—" : x);
  const main = m.voice_to_voice_ms ?? m.tts_first_audio_ms;
  if (main != null && !m.interrupted && m.turn > 0) {
    v2v.push(main);
    els.latBig.textContent = main;
    els.latBig.parentElement.className = `big ${main < 1500 ? "good" : main < 2200 ? "warn" : "bad"}`;
    els.latAvg.textContent = `média ${Math.round(v2v.reduce((a, b) => a + b, 0) / v2v.length)} ms · ${v2v.length} turnos`;
    // Barra empilhada: cada segmento = tempo gasto naquela etapa.
    const end = m.endpointing_ms ?? 0;
    const llm = m.llm_ttft_ms ?? 0;
    const sent = Math.max(0, (m.first_sentence_ms ?? llm) - llm);
    const tts = Math.max(0, (m.tts_first_audio_ms ?? 0) - (m.first_sentence_ms ?? llm));
    const total = end + llm + sent + tts || 1;
    els.latBar.innerHTML = [["c-end", end], ["c-llm", llm], ["c-sent", sent], ["c-tts", tts]]
      .map(([c, v]) => `<div class="${c}" style="width:${(v / total) * 100}%" title="${v} ms"></div>`).join("");
  }
  const tr = document.createElement("tr");
  const cells = [m.turn, m.endpointing_ms, m.llm_ttft_ms, m.tts_first_audio_ms, m.voice_to_voice_ms, clientLatency];
  cells.forEach((v, i) => {
    const td = document.createElement("td");
    td.textContent = fmt(v) + (m.interrupted && i === 0 ? " ✂" : "");
    if (i === 4) td.className = "hl";
    tr.appendChild(td);
  });
  els.rows.prepend(tr);
  clientLatency = null;
}

// ------------------------------------------------------------------ áudio
async function startAudio() {
  // Contexto de captura na taxa nativa (o worklet faz o resample p/ 16k — compatível com Firefox).
  stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  micCtx = new AudioContext();
  await micCtx.audioWorklet.addModule("/static/worklets.js");
  micNode = new AudioWorkletNode(micCtx, "mic-processor");
  micCtx.createMediaStreamSource(stream).connect(micNode);
  micNode.port.onmessage = ({ data }) => {
    if (ws?.readyState === WebSocket.OPEN) ws.send(data.pcm);
    if (els.orb.classList.contains("listening")) setLevel(data.rms);
  };

  // Contexto de playback em 24k = mesma taxa do PCM do ElevenLabs (sem resample).
  playCtx = new AudioContext({ sampleRate: TTS_RATE });
  await playCtx.audioWorklet.addModule("/static/worklets.js");
  player = new AudioWorkletNode(playCtx, "player-processor", { outputChannelCount: [1] });
  // Rotear via <audio> ajuda o cancelamento de eco do navegador a "ver" a voz do agente.
  const dest = playCtx.createMediaStreamDestination();
  player.connect(dest);
  const audioEl = new Audio();
  audioEl.srcObject = dest.stream;
  await audioEl.play().catch(() => player.connect(playCtx.destination));
  player.port.onmessage = ({ data }) => {
    if (data.playing === true && turnEndAt) {
      clientLatency = Math.round(performance.now() - turnEndAt);
      turnEndAt = null;
    }
    if (data.rms != null && els.orb.classList.contains("speaking")) setLevel(data.rms);
  };
}

function stopAudio() {
  stream?.getTracks().forEach((t) => t.stop());
  micCtx?.close(); playCtx?.close();
  stream = micCtx = playCtx = micNode = player = null;
}

// ------------------------------------------------------------------ WebSocket
function onEvent(ev) {
  switch (ev.type) {
    case "ready":
      ws.send(JSON.stringify({ type: "start" }));
      break;
    case "state":
      setState(ev.state);
      break;
    case "transcript":
      if (!partialEl) partialEl = addMsg("user partial", ev.text);
      partialEl.textContent = ev.text;
      if (ev.final) {
        partialEl.className = "msg user";
        partialEl = null;
        assistantEl = null;
        turnEndAt = performance.now();
      }
      break;
    case "assistant":
      if (!assistantEl) assistantEl = addMsg("assistant", ev.text);
      else assistantEl.textContent += " " + ev.text;
      els.transcript.scrollTop = els.transcript.scrollHeight;
      break;
    case "tool_call":
      addMsg("tool", `⚙ ${ev.name}(${JSON.stringify(ev.args)})`);
      addTool(ev.name, ev.args);
      break;
    case "tool_result": {
      const li = els.tools.querySelector("li");
      if (li) { li.querySelector(".res").textContent = "→ " + ev.result; if (ev.is_error) li.classList.add("err"); }
      loadAgenda();
      break;
    }
    case "metrics":
      renderMetrics(ev);
      assistantEl = null;
      break;
    case "interrupt":
      player?.port.postMessage("clear");
      addMsg("system", "— interrompido —");
      assistantEl = null;
      break;
    case "error":
      addMsg("system", "Erro: " + ev.message);
      break;
  }
}

async function startCall() {
  els.btn.disabled = true;
  try {
    await startAudio();
  } catch (e) {
    addMsg("system", "Não consegui acessar o microfone: " + e.message);
    els.btn.disabled = false;
    return;
  }
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (m) => {
    if (m.data instanceof ArrayBuffer) player?.port.postMessage(m.data, [m.data]);
    else onEvent(JSON.parse(m.data));
  };
  ws.onopen = () => {
    els.btn.textContent = "Encerrar";
    els.btn.classList.add("hang");
    els.btn.disabled = false;
    els.input.disabled = els.textBtn.disabled = false;
    setState("listening");
  };
  ws.onclose = () => endCall();
}

function endCall() {
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
  ws?.close();
  ws = null;
  stopAudio();
  setState("idle");
  setLevel(0);
  els.btn.textContent = "Iniciar chamada";
  els.btn.classList.remove("hang");
  els.btn.disabled = false;
  els.input.disabled = els.textBtn.disabled = true;
}

els.btn.onclick = () => (ws ? endCall() : startCall());
els.form.onsubmit = (e) => {
  e.preventDefault();
  const text = els.input.value.trim();
  if (!text || !ws) return;
  player?.port.postMessage("clear");
  addMsg("user", text);
  assistantEl = null;
  turnEndAt = performance.now();
  ws.send(JSON.stringify({ type: "text", text }));
  els.input.value = "";
};
$("refreshAgenda").onclick = loadAgenda;

fetch("/health").then((r) => r.json()).then((h) => {
  const ok = !h.missing_keys.length;
  els.health.textContent = ok ? `online · ${h.llm}` : `faltam chaves: ${h.missing_keys.join(", ")}`;
  els.health.className = `pill ${ok ? "ok" : "bad"}`;
}).catch(() => { els.health.textContent = "servidor offline"; els.health.className = "pill bad"; });
loadAgenda();
