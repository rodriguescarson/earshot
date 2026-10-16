import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { deviceLanguage, setLanguage } from "./i18n";
import { type LiveSummary, liveAvailable, liveSummary } from "./live/client";
import { unlockAudio } from "./player/audioElement";
import { type Choices, clipBase, followed, loadChoices, saveChoices } from "./player/choices";
import { loadManifest, type Manifest } from "./player/manifest";
import { Landing } from "./screens/Landing";
import { Listen } from "./screens/Listen";
import { LiveListen } from "./screens/LiveListen";
import { Setup } from "./screens/Setup";
import { TestPage } from "./screens/TestPage";

type Screen =
  | { name: "landing" }
  | { name: "setup" }
  | { name: "listen"; manifest: Manifest; choices: Choices; base: string }
  | { name: "live"; summary: LiveSummary; choices: Choices };

const defaults: Choices = loadChoices({ lang: deviceLanguage(), output: "app", mode: "casual" });
void setLanguage(defaults.lang);

export function App() {
  const { t } = useTranslation();
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const [screen, setScreen] = useState<Screen>({ name: "landing" });
  const [choices, setChoices] = useState<Choices>(defaults);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const start = async (c: Choices) => {
    unlockAudio();
    setBusy(true);
    setFailed(false);
    try {
      saveChoices(c);
      setChoices(c);
      let base = clipBase(c);
      let manifest: Manifest;
      try {
        manifest = await loadManifest(base);
      } catch (e) {
        if (!followed(c)) throw e;
        base = clipBase({ ...c, stat: null }); // nothing for this stat in the clip: the plain clip is identical
        manifest = await loadManifest(base);
      }
      setScreen({ name: "listen", manifest, choices: c, base });
    } catch {
      setFailed(true);
      setScreen({ name: "landing" });
    } finally {
      setBusy(false);
    }
  };

  const join = async (c: Choices) => {
    unlockAudio();
    setBusy(true);
    setFailed(false);
    try {
      setScreen({ name: "live", summary: await liveSummary(), choices: c });
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  if (hash === "#test") return <TestPage />;
  if (screen.name === "live") return <LiveListen choices={screen.choices} summary={screen.summary} />;
  if (screen.name === "listen")
    return <Listen base={screen.base} manifest={screen.manifest} initialOutput={screen.choices.output} />;
  if (screen.name === "setup") return <Setup initial={choices} onDone={(c) => void start(c)} />;
  return (
    <>
      <Landing
        busy={busy}
        onListen={() => void start(choices)}
        onChoose={() => setScreen({ name: "setup" })}
        onLive={liveAvailable() ? () => void join(choices) : undefined}
      />
      {failed && (
        <p role="alert" className="landing__error">
          {t("listen.giveUp")}
        </p>
      )}
    </>
  );
}
