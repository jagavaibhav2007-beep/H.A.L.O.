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
