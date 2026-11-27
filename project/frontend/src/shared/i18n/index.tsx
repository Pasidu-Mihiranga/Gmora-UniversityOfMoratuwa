import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { en, type MessageKey } from "./en";
import { si } from "./si";

export type Language = "si" | "en";
const DICTIONARIES: Record<Language, Record<MessageKey, string>> = { en, si };
const STORAGE_KEY = "suwapath.language"; // a preference, not health data (FE-33 allows it)

interface I18n {
  language: Language;
  setLanguage: (language: Language) => void;
  t: (key: MessageKey) => string;
}

const I18nContext = createContext<I18n | null>(null);

function initialLanguage(): Language {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "en" || stored === "si" ? stored : "si";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(initialLanguage);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const setLanguage = useCallback((next: Language) => {
    localStorage.setItem(STORAGE_KEY, next);
    setLanguageState(next);
  }, []);

  const value = useMemo<I18n>(
    () => ({ language, setLanguage, t: (key) => DICTIONARIES[language][key] }),
    [language, setLanguage],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useT(): I18n {
  const context = useContext(I18nContext);
  if (!context) throw new Error("useT must be used inside I18nProvider");
  return context;
}

export type { MessageKey };
