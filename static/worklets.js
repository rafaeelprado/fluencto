// AudioWorklets rodam numa thread de áudio dedicada: zero jank da UI no áudio.

// ---------------------------------------------------------------- Captura
// Converte o mic (44.1k/48k float) em PCM16 16 kHz mono, em frames de 20 ms.
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.frame = new Int16Array(320); // 20 ms @ 16 kHz
    this.idx = 0;
    this.pos = 0; // posição fracionária para o resampling
    this.energy = 0;
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    // Downsample por média do bloco (filtro passa-baixa barato + decimação).
    while (this.pos < ch.length) {
      const start = Math.floor(this.pos);
      const end = Math.min(ch.length, Math.floor(this.pos + this.ratio));
      let sum = 0;
      for (let i = start; i < Math.max(end, start + 1); i++) sum += ch[i];
      const v = sum / Math.max(1, end - start);
      this.energy += v * v; this.n++;
      const s = Math.max(-1, Math.min(1, v));
      this.frame[this.idx++] = s < 0 ? s * 0x8000 : s * 0x7fff;
      if (this.idx === this.frame.length) {
        const rms = Math.sqrt(this.energy / this.n);
        this.port.postMessage({ pcm: this.frame.buffer, rms }, [this.frame.buffer]);
        this.frame = new Int16Array(320);
        this.idx = 0; this.energy = 0; this.n = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor("mic-processor", MicProcessor);

// ---------------------------------------------------------------- Playback
// Fila de PCM com "clear" instantâneo (barge-in) — impossível com <audio>/MP3.
class PlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.offset = 0;
    this.playing = false;
    this.port.onmessage = (e) => {
      if (e.data === "clear") { this.queue = []; this.offset = 0; return; }
      const i16 = new Int16Array(e.data);
      const f32 = new Float32Array(i16.length);
      for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;
      this.queue.push(f32);
    };
  }
  process(_inputs, outputs) {
    const out = outputs[0][0];
    let w = 0, level = 0;
    while (w < out.length && this.queue.length) {
      const buf = this.queue[0];
      const n = Math.min(out.length - w, buf.length - this.offset);
      for (let i = 0; i < n; i++) { const v = buf[this.offset + i]; out[w + i] = v; level += v * v; }
      w += n; this.offset += n;
      if (this.offset >= buf.length) { this.queue.shift(); this.offset = 0; }
    }
    const written = w;
    for (; w < out.length; w++) out[w] = 0;
    const nowPlaying = written > 0 || this.queue.length > 0;
    if (nowPlaying !== this.playing) {
      this.playing = nowPlaying;
      this.port.postMessage({ playing: nowPlaying });
    }
    if (nowPlaying && (this.tick = (this.tick || 0) + 1) % 8 === 0) {
      this.port.postMessage({ rms: Math.sqrt(level / out.length) });
    }
    return true;
  }
}
registerProcessor("player-processor", PlayerProcessor);
