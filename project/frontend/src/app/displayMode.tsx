/** Simple mode and text size (FE-40 to FE-43): attributes on <html> switch token values only. */
import { useCallback, useEffect, useState } from "react";

export type DisplayMode = "standard" | "simple";
export type TextSize = "normal" | "large" | "xlarge";
const MODE_KEY = "suwapath.displayMode"; // display preferences, not health data
const SIZE_KEY = "suwapath.textSize";

function read<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const value = localStorage.getItem(key);
  return allowed.includes(value as T) ? (value as T) : fallback;
}

export function useDisplayMode(defaultMode: DisplayMode = "standard") {
  const [mode, setModeState] = useState<DisplayMode>(() => read(MODE_KEY, ["standard", "simple"], defaultMode));
  const [textSize, setTextSizeState] = useState<TextSize>(() => read(SIZE_KEY, ["normal", "large", "xlarge"], "normal"));

  useEffect(() => {
    const root = document.documentElement;
    if (mode === "simple") root.dataset.mode = "simple";
    else delete root.dataset.mode;
    if (textSize === "normal") delete root.dataset.textSize;
    else root.dataset.textSize = textSize;
  }, [mode, textSize]);

  const setMode = useCallback((next: DisplayMode) => {
    localStorage.setItem(MODE_KEY, next);
    setModeState(next);
  }, []);
  const setTextSize = useCallback((next: TextSize) => {
    localStorage.setItem(SIZE_KEY, next);
    setTextSizeState(next);
  }, []);
  return { mode, setMode, textSize, setTextSize };
}
