import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import ar from "./locales/ar.json";
import en from "./locales/en.json";
import es from "./locales/es.json";
import hi from "./locales/hi.json";

export const LANGUAGES = ["en", "es", "ar", "hi"] as const;
export type Lang = (typeof LANGUAGES)[number];
export const RTL = new Set(["ar", "he", "fa", "ur"]);

/** The device language when Earshot speaks it, else English. */
export function deviceLanguage(): Lang {
  for (const tag of navigator.languages ?? [navigator.language]) {
    const base = tag.toLowerCase().split("-")[0];
    if ((LANGUAGES as readonly string[]).includes(base ?? "")) return base as Lang;
  }
  return "en";
}

void i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    es: { translation: es },
    ar: { translation: ar },
    hi: { translation: hi },
  },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export function applyDocumentLanguage(lng: string): void {
  document.documentElement.lang = lng;
  document.documentElement.dir = RTL.has(lng) ? "rtl" : "ltr";
}

export async function setLanguage(lng: Lang): Promise<void> {
  await i18n.changeLanguage(lng);
  applyDocumentLanguage(lng);
}

export default i18n;
