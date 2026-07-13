// Minimal theme helper: "auto" follows prefers-color-scheme (tokens.css
// media query), a manual choice sets [data-theme] on <html> and persists.
export type Theme = "auto" | "light" | "dark";

const STORAGE_KEY = "halo-theme";

export function getTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" || stored === "dark" ? stored : "auto";
}

export function setTheme(theme: Theme): void {
  localStorage.setItem(STORAGE_KEY, theme);
  if (theme === "auto") {
    document.documentElement.removeAttribute("data-theme");
  } else {
    document.documentElement.setAttribute("data-theme", theme);
  }
}

// Call once on app start to apply whatever was persisted.
export function initTheme(): void {
  setTheme(getTheme());
}

// Orb and workspace are separate windows sharing one origin, so they share
// localStorage — but the `storage` event only fires in OTHER windows, never
// the one that called setTheme(). Settings' theme control calls setTheme()
// directly (instant in its own window) and this listener catches the other
// window, so a theme switch is instant in BOTH (Step 13 acceptance).
export function watchThemeAcrossWindows(): () => void {
  function onStorage(e: StorageEvent) {
    if (e.key === STORAGE_KEY) setTheme(getTheme());
  }
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
