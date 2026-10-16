import { forwardRef, useImperativeHandle, useRef } from "react";

export interface Announcer {
  announce: (text: string, urgent?: boolean) => void;
}

/**
 * Both live regions exist, empty, from first paint (DR-14). Each update clears the region and
 * sets the text about 150 ms later, so a repeated line is read again.
 */
export const LiveRegions = forwardRef<Announcer>(function LiveRegions(_props, ref) {
  const polite = useRef<HTMLDivElement>(null);
  const urgent = useRef<HTMLDivElement>(null);
  useImperativeHandle(ref, () => ({
    announce(text, isUrgent = false) {
      const el = isUrgent ? urgent.current : polite.current;
      if (!el) return;
      el.textContent = "";
      window.setTimeout(() => {
        el.textContent = text;
      }, 150);
    },
  }));
  return (
    <>
      <div ref={polite} className="visually-hidden" role="status" aria-live="polite" aria-atomic="true" />
      <div ref={urgent} className="visually-hidden" role="alert" aria-live="assertive" aria-atomic="true" />
    </>
  );
});
