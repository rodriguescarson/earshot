import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/base.css";
import "./styles/app.css";
import { App } from "./App";
import { applyDocumentLanguage } from "./i18n";

applyDocumentLanguage("en");
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
