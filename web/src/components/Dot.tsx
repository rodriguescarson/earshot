/** The state grammar's marker: form first (filled, hollow, doubled), colour second. */
export type DotState = "on-air" | "waiting" | "never-drop";

export function Dot({ state }: { state: DotState }) {
  const size = 18;
  return (
    <svg className={`dot dot--${state}`} width={size} height={size} viewBox="0 0 18 18" aria-hidden="true">
      {state === "on-air" && <circle cx="9" cy="9" r="7" className="dot__fill" />}
      {state === "waiting" && <circle cx="9" cy="9" r="6" className="dot__ring" />}
      {state === "never-drop" && (
        <>
          <circle cx="5.5" cy="9" r="4" className="dot__ink" />
          <circle cx="13" cy="9" r="4" className="dot__ink" />
        </>
      )}
    </svg>
  );
}
