import React, { Suspense, lazy } from "react";
import ReactDOM from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { OrbRoot } from "./orb/OrbRoot";
import { WorkspaceRoot } from "./workspace/WorkspaceRoot";
import { initTheme, watchThemeAcrossWindows } from "./styles/theme";
import "./styles/tokens.css";

// ponytail: TokensPreview is lazy so its CSS only loads when that route
// actually renders — a static import would inject that stylesheet into
// every route, including the orb/workspace windows that need to stay
// transparent.
const TokensPreview = lazy(() => import("./dev/TokensPreview").then((m) => ({ default: m.TokensPreview })));

initTheme();
watchThemeAcrossWindows();

const params = new URLSearchParams(window.location.search);

// D3/D9: the Tauri window label picks the root when running natively
// (`orb`/`main`); `?window=orb|workspace` picks it in a plain browser tab,
// where there is no window label. `?dev=tokens` stays the Step-3 preview
// route; anything else (including no param at all) falls back to the
// workspace root.
function resolveRoot(): "orb" | "workspace" | "tokens" {
  if (params.get("dev") === "tokens") return "tokens";
  if (isTauri()) {
    const label = getCurrentWindow().label;
    if (label === "orb") return "orb";
    if (label === "main") return "workspace";
  }
  const windowParam = params.get("window");
  if (windowParam === "orb") return "orb";
  return "workspace";
}

const root = resolveRoot();
const Root = root === "orb" ? OrbRoot : root === "tokens" ? TokensPreview : WorkspaceRoot;

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <Suspense fallback={null}>
      <Root />
    </Suspense>
  </React.StrictMode>,
);
