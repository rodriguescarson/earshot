import { useTranslation } from "react-i18next";

/** "Listen now": one decision, one primary action (CEO-11, DR-1, DR-7, DR-8). */
export function Landing({
  onListen,
  onChoose,
  onLive,
  busy,
}: {
  onListen: () => void;
  onChoose: () => void;
  onLive?: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  return (
    <main className="landing">
      <header className="plate landing__mark">
        <h1 className="wordmark">{t("landing.wordmark")}</h1>
        <p className="braille" aria-hidden="true">
          ⠑⠁⠗⠎⠓⠕⠞
        </p>
      </header>
      <p className="landing__tagline">{t("landing.tagline")}</p>
      <p className="landing__note">{t("landing.screenReader")}</p>
      <button type="button" className="primary" onClick={onListen} disabled={busy} aria-describedby="hp">
        {busy ? t("landing.connecting") : t("landing.listen")}
      </button>
      <p id="hp" className="landing__small">
        {t("landing.headphones")}
      </p>
      <button type="button" className="link" onClick={onChoose}>
        {t("landing.choose")}
      </button>
      {onLive && (
        <button type="button" className="link" onClick={onLive}>
          {t("landing.live")}
        </button>
      )}
      <p className="landing__small">{t("landing.fiction")}</p>
    </main>
  );
}
