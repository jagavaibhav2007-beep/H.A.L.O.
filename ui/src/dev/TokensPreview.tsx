import { useState } from "react";
import { Check, Clock, Play, ShieldAlert, Trash2, Wifi } from "lucide-react";
import { GlassPanel } from "../components/GlassPanel";
import { Button } from "../components/Button";
import { Chip } from "../components/Chip";
import { Icon } from "../components/Icon";
import { getTheme, setTheme, type Theme } from "../styles/theme";
import "../styles/tokens.css";
import "../styles/glass.css";

// Dev-only eyeball-QA page for Step 3 primitives. Renders in a plain
// browser (npm run dev, no Tauri) — reachable via ?dev=tokens.

function Swatch({ label, varName }: { label: string; varName: string }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: "var(--text-sm)" }}>
      <span
        style={{
          width: 18,
          height: 18,
          borderRadius: 4,
          border: "1px solid var(--border)",
          background: `var(${varName})`,
        }}
      />
      <span style={{ color: "var(--text-muted)" }}>{label}</span>
    </div>
  );
}

function PrimitivesSample() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Button variant="primary">Approve</Button>
        <Button variant="ghost">Deny</Button>
        <Button variant="destructive" confirm>
          Delete
        </Button>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Chip icon={ShieldAlert} label="Tier 3" tone="tier3" />
        <Chip icon={Wifi} label="Lane 1 · Fast" tone="primary" />
        <Chip icon={Check} label="Done" tone="success" />
        <Chip icon={Trash2} label="Destructive" tone="destructive" />
        <Chip icon={Clock} label="Paused" tone="muted" />
      </div>
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <Icon icon={Play} size={16} />
        <Icon icon={Play} size={20} />
        <Icon icon={Play} size={24} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 4 }}>
        <Swatch label="primary" varName="--primary" />
        <Swatch label="accent-soft" varName="--accent-soft" />
        <Swatch label="bg" varName="--bg" />
        <Swatch label="text" varName="--text" />
        <Swatch label="text-muted" varName="--text-muted" />
        <Swatch label="tier-3" varName="--tier-3" />
        <Swatch label="destructive" varName="--destructive" />
        <Swatch label="success" varName="--success" />
      </div>
      <p style={{ fontSize: "var(--text-base)", color: "var(--text)", margin: 0 }}>
        Body text at 14px — <span style={{ fontFamily: "var(--font-mono)" }}>JetBrains Mono fallback</span> for code.
      </p>
    </div>
  );
}

function Cell({ dataTheme, transparencyOff }: { dataTheme: "light" | "dark"; transparencyOff: boolean }) {
  return (
    <div
      data-theme={dataTheme}
      data-transparency={transparencyOff ? "off" : undefined}
      style={{ background: "var(--bg)", padding: 20, borderRadius: 12 }}
    >
      <GlassPanel elevation="card" style={{ padding: 20 }}>
        <h3 style={{ fontSize: "var(--text-title)", color: "var(--text)", margin: "0 0 12px" }}>
          {dataTheme} · {transparencyOff ? "solid" : "glass"}
        </h3>
        <PrimitivesSample />
      </GlassPanel>
    </div>
  );
}

export function TokensPreview() {
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const [transparencyOff, setTransparencyOff] = useState(false);

  function applyTheme(next: Theme) {
    setTheme(next);
    setThemeState(next);
  }

  function toggleTransparency() {
    const next = !transparencyOff;
    setTransparencyOff(next);
    if (next) {
      document.documentElement.setAttribute("data-transparency", "off");
    } else {
      document.documentElement.removeAttribute("data-transparency");
    }
  }

  return (
    <div style={{ padding: 24, fontFamily: "var(--font-sans)", background: "var(--bg)", minHeight: "100vh" }}>
      <h1 style={{ fontSize: "var(--text-empty)", color: "var(--text)" }}>Tokens preview</h1>
      <div style={{ display: "flex", gap: 12, marginBottom: 24, alignItems: "center" }}>
        <span style={{ color: "var(--text-muted)", fontSize: "var(--text-sm)" }}>Global theme:</span>
        {(["auto", "light", "dark"] as Theme[]).map((t) => (
          <Button key={t} variant={theme === t ? "primary" : "ghost"} onClick={() => applyTheme(t)}>
            {t}
          </Button>
        ))}
        <Button variant="ghost" onClick={toggleTransparency}>
          Global transparency: {transparencyOff ? "off" : "on"}
        </Button>
      </div>

      <p style={{ color: "var(--text-muted)", fontSize: "var(--text-sm)" }}>
        The four cells below force their own theme/transparency locally (via nested{" "}
        <code>data-theme</code>/<code>data-transparency</code>) so all four render modes are visible
        at once regardless of the global toggles above.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 16 }}>
        <Cell dataTheme="light" transparencyOff={false} />
        <Cell dataTheme="light" transparencyOff={true} />
        <Cell dataTheme="dark" transparencyOff={false} />
        <Cell dataTheme="dark" transparencyOff={true} />
      </div>
    </div>
  );
}
