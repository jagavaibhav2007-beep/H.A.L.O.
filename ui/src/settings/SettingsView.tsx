// Phase 1 Step 13 — Settings view: lean, single scroll, grouped. Only the
// pieces with somewhere real to land are interactive (theme, the two voice
// toggles that just record intent via settings_update — Voice itself stays
// the Phase-0 idle stub until Step 14/Phase 2); everything that needs a real
// backend (launch-at-startup, key entry, advanced knobs) renders disabled
// with an honest note instead of pretending to work.

import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getTheme, setTheme, type Theme } from "../styles/theme";
import { useHaloStore, selectSpend } from "../state/store";
import "./SettingsView.css";

interface SettingsViewProps {
  sendSettingsUpdate: (key: string, value: unknown) => void;
}

// ponytail: no `model_config` frame exists in the contract (nothing to fetch
// IDs from yet) — a real model picker is a Phase 2 concern once there's a
// real Brain choosing models. This stays a static, honestly-labeled list.
const MOCK_MODELS = ["Chat: halo-mock-1", "Vision: halo-mock-vision-1"];

export function SettingsView({ sendSettingsUpdate }: SettingsViewProps) {
  const spend = useHaloStore(selectSpend);
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const [hotkey, setHotkey] = useState<string | null>(null);
  const [wakeWord, setWakeWord] = useState(true);
  const [pushToTalk, setPushToTalk] = useState(false);

  useEffect(() => {
    if (!isTauri()) return;
    invoke<string>("active_hotkey")
      .then(setHotkey)
      .catch(() => setHotkey(null));
  }, []);

  function onTheme(next: Theme) {
    setTheme(next); // instant in this window; watchThemeAcrossWindows() catches the other
    setThemeState(next);
    sendSettingsUpdate("theme", next);
  }

  return (
    <div className="settings-view">
      <div className="settings-content">
        <section className="settings-group">
          <h3 className="settings-group-title">General</h3>
          <div className="settings-row">
            <span className="settings-label">Summon hotkey</span>
            <span className="settings-value">{hotkey ?? (isTauri() ? "…" : "not available in browser preview")}</span>
          </div>
          <div className="settings-row">
            <span className="settings-label">Theme</span>
            <select className="settings-select" value={theme} onChange={(e) => onTheme(e.target.value as Theme)}>
              <option value="auto">Auto</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </div>
          <div className="settings-row" data-disabled>
            <label className="settings-label">
              <input type="checkbox" disabled />
              Launch at startup
            </label>
            <span className="settings-note">Needs OS integration — lands in a later phase.</span>
          </div>
        </section>

        <section className="settings-group">
          <h3 className="settings-group-title">Voice</h3>
          <div className="settings-row">
            <label className="settings-label">
              <input
                type="checkbox"
                checked={wakeWord}
                onChange={(e) => {
                  setWakeWord(e.target.checked);
                  sendSettingsUpdate("voice.wake_word", e.target.checked);
                }}
              />
              Wake word ("Halo")
            </label>
          </div>
          <div className="settings-row">
            <label className="settings-label">
              <input
                type="checkbox"
                checked={pushToTalk}
                onChange={(e) => {
                  setPushToTalk(e.target.checked);
                  sendSettingsUpdate("voice.push_to_talk", e.target.checked);
                }}
              />
              Push-to-talk instead
            </label>
          </div>
          <p className="settings-note">Voice is still scripted for demos — real listening lands with the Voice worker.</p>
        </section>

        <section className="settings-group">
          <h3 className="settings-group-title">Models</h3>
          {MOCK_MODELS.map((m) => (
            <div className="settings-row" key={m}>
              <span className="settings-value">{m}</span>
            </div>
          ))}
          <div className="settings-row">
            <span className="settings-label">This month</span>
            <span className="settings-value">${spend.monthUsd.toFixed(2)}</span>
          </div>
        </section>

        <section className="settings-group">
          <h3 className="settings-group-title">Keys &amp; connections</h3>
          {["OpenAI", "Anthropic", "Google"].map((name) => (
            <div className="settings-row" key={name} data-disabled>
              <span className="settings-label">{name}</span>
              <span className="settings-dots" aria-label="not configured">●●●</span>
            </div>
          ))}
          <p className="settings-note">Key entry lands in Phase 2 with real secret storage.</p>
        </section>

        <details className="settings-advanced">
          <summary>Advanced</summary>
          <div className="settings-row" data-disabled>
            <span className="settings-label">Activity log cap</span>
            <span className="settings-value">10,000 entries</span>
          </div>
          <div className="settings-row" data-disabled>
            <span className="settings-label">Approval hold duration</span>
            <span className="settings-value">700ms</span>
          </div>
          <p className="settings-note">Defaults shown for reference — not editable yet.</p>
        </details>
      </div>
    </div>
  );
}
