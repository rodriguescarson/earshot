import { useState } from "react";
import { useTranslation } from "react-i18next";
import { unlockAudio } from "./player/audioElement";
import { loadManifest, type Manifest } from "./player/manifest";
import { Landing } from "./screens/Landing";
import { Listen } from "./screens/Listen";

const CLIP_BASE = import.meta.env.VITE_CLIP_BASE ?? "/clips/demo-en-casual";

export function App() {
  const { t } = useTranslation();
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  if (manifest) return <Listen base={CLIP_BASE} manifest={manifest} />;
  return (
    <>
      <Landing
        busy={busy}
        onListen={async () => {
          unlockAudio();
          setBusy(true);
          setFailed(false);
          try {
            setManifest(await loadManifest(CLIP_BASE));
          } catch {
            setFailed(true);
          } finally {
            setBusy(false);
          }
        }}
      />
      {failed && (
        <p role="alert" className="landing__error">
          {t("listen.giveUp")}
        </p>
      )}
    </>
  );
}
