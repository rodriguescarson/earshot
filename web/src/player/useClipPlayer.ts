import { useCallback, useEffect, useRef, useState } from "react";
import { sharedAudio } from "./audioElement";
import type { Arm, Manifest } from "./manifest";

export type PlayerState = "idle" | "playing" | "paused" | "ended" | "blocked" | "error";

/**
 * One <audio> element plays the pre-mixed clip, so speech survives a locked screen and the
 * lock-screen controls (Media Session) work. The clock comes from the element's currentTime.
 */
export function useClipPlayer(base: string, manifest: Manifest | null, arm: Arm) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState<PlayerState>("idle");
  const [tMs, setTMs] = useState(0);
  // Switching arms (output mode) swaps the file: carry the position and play state across.
  const carry = useRef<{ at: number; playing: boolean }>({ at: 0, playing: false });

  useEffect(() => {
    if (!manifest) return;
    const file = manifest.audio[arm] ?? manifest.audio.full;
    if (!file) return;
    const el = sharedAudio();
    el.src = `${base}/${file}`;
    audio.current = el;
    const { at, playing } = carry.current;
    if (at > 0) {
      el.currentTime = at;
      setTMs(at * 1000);
    }
    if (playing) void el.play().catch(() => setState("blocked"));
    let raf = 0;
    const tick = () => {
      setTMs(el.currentTime * 1000);
      if (!el.paused) raf = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      setState("playing");
      raf = requestAnimationFrame(tick);
    };
    const onPause = () => {
      if (!el.ended) setState((s) => (s === "blocked" ? s : "paused"));
      setTMs(el.currentTime * 1000);
    };
    const onEnded = () => setState("ended");
    const onError = () => setState("error");
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("ended", onEnded);
    el.addEventListener("error", onError);
    return () => {
      cancelAnimationFrame(raf);
      carry.current = { at: el.currentTime, playing: !el.paused && !el.ended };
      el.pause();
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("ended", onEnded);
      el.removeEventListener("error", onError);
      audio.current = null;
    };
  }, [base, manifest, arm]);

  const play = useCallback(async () => {
    const el = audio.current;
    if (!el) return;
    if (el.ended) el.currentTime = 0;
    try {
      await el.play();
    } catch {
      setState("blocked");
    }
  }, []);

  const pause = useCallback(() => audio.current?.pause(), []);

  return { state, tMs, play, pause, audio };
}
