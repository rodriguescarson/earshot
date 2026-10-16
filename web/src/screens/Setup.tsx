import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { LANGUAGES, type Lang, setLanguage } from "../i18n";
import { type Choices, type Mode, type Output, STATS, type Stat } from "../player/choices";

type Step = "language" | "output" | "mode" | "stat";
const NO_STAT = "none";

/** One decision per screen (DR-15): the heading takes focus, Back keeps the choice. */
export function Setup({ initial, onDone }: { initial: Choices; onDone: (c: Choices) => void }) {
  const { t } = useTranslation();
  const [step, setStep] = useState<Step>("language");
  const [c, setC] = useState<Choices>(initial);
  const heading = useRef<HTMLHeadingElement>(null);
  // "Follow one stat?" exists only after Analyst is chosen (one decision per screen, DR-15).
  const STEPS: Step[] = ["language", "output", "mode", ...(c.mode === "analyst" ? (["stat"] as const) : [])];
  const index = STEPS.indexOf(step);

  // biome-ignore lint/correctness/useExhaustiveDependencies: focus moves to the heading on every step change
  useEffect(() => {
    heading.current?.focus();
  }, [step]);

  const next = () => (index < STEPS.length - 1 ? setStep(STEPS[index + 1] as Step) : onDone(c));
  const options: { value: string; label: string; hint?: string; lang?: string }[] =
    step === "language"
      ? LANGUAGES.map((l) => ({ value: l, label: t(`languages.${l}`), lang: l }))
      : step === "output"
        ? (["app", "reader", "oneEar"] as Output[]).map((o) => ({
            value: o,
            label: t(`setup.output.${o}`),
            hint: t(`setup.output.${o}Hint`),
          }))
        : step === "mode"
          ? (["casual", "analyst"] as Mode[]).map((m) => ({
              value: m,
              label: t(`setup.mode.${m}`),
              hint: t(`setup.mode.${m}Hint`),
            }))
          : [NO_STAT, ...STATS].map((v) => ({
              value: v,
              label: t(`setup.stat.${v}`),
              hint: t(`setup.stat.${v}Hint`),
            }));
  const current =
    step === "language"
      ? c.lang
      : step === "output"
        ? c.output
        : step === "mode"
          ? c.mode
          : (c.stat ?? NO_STAT);

  return (
    <main className="setup">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          next();
        }}
      >
        <fieldset>
          <legend>
            <h1 ref={heading} tabIndex={-1} className="setup__title">
              <span className="setup__step">{t("setup.step", { n: index + 1, m: STEPS.length })}</span>
              <span className="visually-hidden">. </span>
              {t(`setup.${step}.title`)}
            </h1>
          </legend>
          {options.map((o) => (
            <label key={o.value} className="choice">
              <input
                type="radio"
                name={step}
                value={o.value}
                checked={current === o.value}
                onChange={() => {
                  if (step === "language") {
                    setC({ ...c, lang: o.value as Lang });
                    void setLanguage(o.value as Lang);
                  } else if (step === "output") setC({ ...c, output: o.value as Output });
                  else if (step === "mode") setC({ ...c, mode: o.value as Mode });
                  else setC({ ...c, stat: o.value === NO_STAT ? null : (o.value as Stat) });
                }}
              />
              <span className="choice__label" lang={o.lang}>
                {o.label}
              </span>
              {o.hint && <span className="choice__hint">{o.hint}</span>}
            </label>
          ))}
        </fieldset>
        <div className="setup__actions">
          <button type="submit" className="primary">
            {index < STEPS.length - 1 ? t("setup.next") : t("setup.listen")}
          </button>
          {index > 0 && (
            <button type="button" className="link" onClick={() => setStep(STEPS[index - 1] as Step)}>
              {t("setup.back")}
            </button>
          )}
        </div>
      </form>
    </main>
  );
}
