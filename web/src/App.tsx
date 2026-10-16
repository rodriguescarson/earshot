import { useState } from "react";
import { useTranslation } from "react-i18next";
import { deviceLanguage, setLanguage } from "./i18n";
import { unlockAudio } from "./player/audioElement";
import { type Choices, clipBase, loadChoices, saveChoices } from "./player/choices";
import { loadManifest, type Manifest } from "./player/manifest";
import { Landing } from "./screens/Landing";
import { Listen } from "./screens/Listen";
import { Setup } from "./screens/Setup";

type Screen =
  | { name: "landing" }
  | { name: "setup" }
  | { name: "listen"; manifest: Manifest; choices: Choices };

const defaults: Choices = loadChoices({ lang: deviceLanguage(), output: "app", mode: "casual" });
void setLanguage(defaults.lang);

export function App() {
  const { t } = useTranslation();
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
      setScreen({ name: "listen", manifest: await loadManifest(clipBase(c)), choices: c });
    } catch {
      setFailed(true);
      setScreen({ name: "landing" });
    } finally {
      setBusy(false);
    }
  };

  if (screen.name === "listen")
    return (
      <Listen
        base={clipBase(screen.choices)}
        manifest={screen.manifest}
        initialOutput={screen.choices.output}
      />
    );
  if (screen.name === "setup") return <Setup initial={choices} onDone={(c) => void start(c)} />;
  return (
    <>
      <Landing
        busy={busy}
        onListen={() => void start(choices)}
        onChoose={() => setScreen({ name: "setup" })}
      />
      {failed && (
        <p role="alert" className="landing__error">
          {t("listen.giveUp")}
        </p>
      )}
    </>
  );
}
