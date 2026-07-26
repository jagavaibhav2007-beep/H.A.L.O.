// Phase 1 Step 13 — Settings view: lean, single scroll, grouped. Only the
// pieces with somewhere real to land are interactive (theme, the two voice
// toggles that just record intent via settings_update — Voice itself stays
// the Phase-0 idle stub until Step 14/Phase 2); everything that needs a real
// backend (launch-at-startup, key entry, advanced knobs) renders disabled
// with an honest note instead of pretending to work.

import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getTheme, setTheme, type Theme } from "../styles/theme";
import { Button } from "../components/Button";
import { useHaloStore, selectSpend } from "../state/store";
import { usePendingConfirm } from "../lib/usePendingConfirm";
import "./SettingsView.css";

interface SettingsViewProps {
  sendSettingsUpdate: (key: string, value: unknown) => void;
}

// ponytail: no `model_config` frame exists in the contract (nothing to fetch
// IDs from yet) — a real model picker is a Phase 2 concern once there's a
// real Brain choosing models. This stays a static, honestly-labeled list.
// Copy per status - plain, no jargon (design language rule 10).
const KEY_STATUS_COPY: Record<string, string> = {
  set: "connected",
  missing: "not set",
  // "invalid" covers both a rejected key and an unreachable keystore. Never
  // word it as "not set": a user who reads "not set" for a key that IS stored
  // goes and rotates a key they never lost (see mem/Bugs.md).
  invalid: "couldn't verify — the key was rejected, or Windows Credential Manager is unavailable",
  unverified: "saved — will check on first use",
};

export function SettingsView({ sendSettingsUpdate }: SettingsViewProps) {
  const spend = useHaloStore(selectSpend);
  const settings = useHaloStore((s) => s.settings);
  const keyStatus = settings.openrouter_key;
  const [theme, setThemeState] = useState<Theme>(getTheme());
  const [hotkey, setHotkey] = useState<string | null>(null);
  const [wakeWord, setWakeWord] = useState(true);
  const [pushToTalk, setPushToTalk] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  // Rule 3 (lock on press, unlock only on confirm): the shared hook clears
  // pending only when a settings_state frame upserts a new status for the
  // key — never optimistically.
  const { pending, begin } = usePendingConfirm(settings);
  const keyPending = pending.openrouter_key;

  function saveKey() {
    if (!keyInput || !begin("openrouter_key", "checking…")) return;
    sendSettingsUpdate("openrouter_key", keyInput);
    setKeyInput(""); // never lingers in the DOM once sent
  }

  // Explicit removal, so "replace my key" doesn't mean "guess whether the old
  // one is still in there". Confirmed first — it's not recoverable from here.
  function removeKey() {
    if (keyPending) return;
    if (!window.confirm("Remove the stored OpenRouter key? You'll need to paste it again to use Halo.")) return;
    if (!begin("openrouter_key", "checking…")) return;
    sendSettingsUpdate("openrouter_key", "");
  }

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
    <div className="halo-scroll">
      <div className="settings-content">
        <section className="settings-group">
          <h3 className="halo-group-title settings-group-title">General</h3>
          <div className="settings-row">
            <span className="settings-label">Summon hotkey</span>
            <span className="settings-value">{hotkey ?? (isTauri() ? "…" : "not available in browser preview")}</span>
          </div>
          <div className="settings-row">
            <label className="settings-label" htmlFor="halo-theme">Theme</label>
            <select id="halo-theme" className="halo-input settings-select" value={theme} onChange={(e) => onTheme(e.target.value as Theme)}>
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
          <h3 className="halo-group-title settings-group-title">Voice</h3>
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
          <h3 className="halo-group-title settings-group-title">Models</h3>
          <div className="settings-row">
            <span className="settings-label">Routing</span>
            <span className="settings-value">Selected automatically by the Brain</span>
          </div>
          <div className="settings-row">
            <span className="settings-label">This month</span>
            <span className="settings-value">${spend.monthUsd.toFixed(2)}</span>
          </div>
          <div className="settings-row">
            <span className="settings-label">Tokens this session</span>
            <span className="settings-value">
              {spend.sessionTokens.toLocaleString()}
              {spend.lastTurnTokens > 0 && ` (last turn ${spend.lastTurnTokens.toLocaleString()})`}
            </span>
          </div>
        </section>

        <section className="settings-group">
          <h3 className="halo-group-title settings-group-title">Keys &amp; connections</h3>
          <div className="settings-row">
            <label className="settings-label" htmlFor="halo-openrouter-key">OpenRouter</label>
            <input
              id="halo-openrouter-key"
              className="halo-input"
              type="password"
              placeholder="sk-or-..."
              autoComplete="off"
              value={keyInput}
              disabled={!!keyPending}
              onChange={(e) => setKeyInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && saveKey()}
            />
            <Button variant="ghost" onClick={saveKey} disabled={!!keyPending || !keyInput}>
              {keyPending ? "Checking…" : "Save"}
            </Button>
          </div>
          <div className="settings-row">
            <span className="settings-value" role="status" data-key-status={keyStatus ?? "unknown"}>
              {keyPending ?? (keyStatus ? KEY_STATUS_COPY[keyStatus] : "checking stored status…")}
            </span>
            {keyStatus === "set" && (
              <Button variant="ghost" onClick={removeKey} disabled={!!keyPending}>
                Remove key
              </Button>
            )}
          </div>
          <p className="settings-note">
            Only OpenRouter is used — one key covers every model. It's stored in Windows Credential
            Manager, never on disk in this app and never sent anywhere but OpenRouter.
          </p>
        </section>

        <details className="settings-advanced">
          <summary className="halo-group-title">Advanced</summary>
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
