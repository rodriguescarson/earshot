/**
 * The continuous layer, synthesized in the browser from ball messages (no samples):
 * a soft tone panned along the pitch (or, in one-ear mode, pitched by third) and a crowd bed
 * that rises with danger. Everything ducks while Earshot speaks.
 */
export class Continuous {
  private ctx: AudioContext;
  private tone: OscillatorNode;
  private toneGain: GainNode;
  private pan: StereoPannerNode;
  private crowdGain: GainNode;
  private master: GainNode;
  private oneEar: boolean;
  private ducked = false;

  constructor(oneEar: boolean) {
    this.oneEar = oneEar;
    this.ctx = new AudioContext();
    this.master = this.ctx.createGain();
    this.master.gain.value = 1;
    this.master.connect(this.ctx.destination);
    this.tone = this.ctx.createOscillator();
    this.tone.type = "sine";
    this.tone.frequency.value = 196;
    this.toneGain = this.ctx.createGain();
    this.toneGain.gain.value = 0;
    this.pan = this.ctx.createStereoPanner();
    this.tone.connect(this.toneGain).connect(this.pan).connect(this.master);
    this.tone.start();
    const n = this.ctx.sampleRate * 2;
    const buf = this.ctx.createBuffer(2, n, this.ctx.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      let b = 0;
      for (let i = 0; i < n; i++) {
        b = 0.995 * b + 0.05 * (Math.random() * 2 - 1);
        d[i] = b;
      }
    }
    const noise = this.ctx.createBufferSource();
    noise.buffer = buf;
    noise.loop = true;
    this.crowdGain = this.ctx.createGain();
    this.crowdGain.gain.value = 0.02;
    noise.connect(this.crowdGain).connect(this.master);
    noise.start();
  }

  private speechSource: MediaElementAudioSourceNode | null = null;
  private speechPanNode: StereoPannerNode | null = null;

  /** Route the shared <audio> element through this context so a mono line can be placed at a pan.
   * Done once per element (the Web Audio API allows one source per element) and only when the
   * server serves the streamed codec, because a non-CORS element goes silent once attached. */
  attach(el: HTMLAudioElement): void {
    if (this.speechSource) return;
    this.speechPanNode = this.ctx.createStereoPanner();
    this.speechSource = this.ctx.createMediaElementSource(el);
    this.speechSource.connect(this.speechPanNode).connect(this.master);
  }

  speechPan(p: number): void {
    this.speechPanNode?.pan.setTargetAtTime(
      this.oneEar ? 0 : Math.max(-1, Math.min(1, p)),
      this.ctx.currentTime,
      0.02,
    );
  }

  async resume(): Promise<void> {
    if (this.ctx.state !== "running") await this.ctx.resume();
  }

  ball(x: number, danger: number, inPlay: boolean): void {
    const t = this.ctx.currentTime;
    const p = Math.max(-1, Math.min(1, x / 52.5 - 1));
    if (this.oneEar) {
      this.pan.pan.setTargetAtTime(0, t, 0.1);
      this.tone.frequency.setTargetAtTime(x < 35 ? 165 : x < 70 ? 196 : 247, t, 0.05);
    } else {
      this.pan.pan.setTargetAtTime(p, t, 0.25);
    }
    const duck = this.ducked ? 0.35 : 1;
    this.toneGain.gain.setTargetAtTime(inPlay ? 0.05 * duck : 0, t, 0.15);
    this.crowdGain.gain.setTargetAtTime((0.02 + 0.06 * danger) * (this.ducked ? 0.6 : 1), t, 0.6);
  }

  duck(on: boolean): void {
    this.ducked = on;
  }

  pause(): void {
    void this.ctx.suspend();
  }

  close(): void {
    void this.ctx.close();
  }
}
