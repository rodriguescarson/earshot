/**
 * One shared <audio> element for the whole session. iOS Safari only lets an element play
 * after a user gesture has played it once, so the "Listen now" tap unlocks it synchronously
 * (DR-4) and every later clip reuses the same element.
 */
const SILENCE =
  "data:audio/mp4;base64,AAAAHGZ0eXBNNEEgAAAAAE00QSBpc29tbXA0MgAAAAhmcmVlAAAAZm1kYXQhEAUgpBv/wAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==";

let el: HTMLAudioElement | null = null;

export function sharedAudio(): HTMLAudioElement {
  if (!el) {
    el = new Audio();
    el.preload = "auto";
    el.setAttribute("playsinline", "");
  }
  return el;
}

/** Call inside the click handler, before any await. */
export function unlockAudio(): void {
  const a = sharedAudio();
  if (a.src) return;
  a.src = SILENCE;
  void a.play().catch(() => undefined);
}
