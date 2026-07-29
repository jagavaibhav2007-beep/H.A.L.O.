// Rule-3 ("lock on press, unlock only on confirm") FUNCTIONAL guard: begin()
// locks a key and dedups a second press; the key unlocks only when the
// collection upserts a fresh object reference for it (the Brain's confirming
// frame). Rendered under <StrictMode> to match main.tsx.
//
// SCOPE LIMIT (verified, don't over-trust this): this does NOT reproduce the
// original StrictMode double-invoke bug (mem/Bugs.md, "Rule-3 unlock on
// confirm"). Under vitest+jsdom the updater is double-invoked but React commits
// the FIRST invocation and the mount effect runs once, so the ref-mutation-
// inside-updater regression stays green here. Confirmed by reintroducing the
// exact bug: this test still passed. That ordering bug is browser-mount-only
// and remains a live check (./dev.ps1 -Mock, exercise a confirmable control).
// What this DOES catch: a broken/ inverted unlock comparison, a missing clear,
// or a broken dedup -- confirmed to fail when the comparison is inverted.
//
// Run: npx vitest run (from ui/), or npm test.

import { StrictMode, type ReactNode } from "react";
import { act, renderHook } from "@testing-library/react";
import { expect, test } from "vitest";
import { usePendingConfirm } from "./usePendingConfirm";
import type { ErrorMsg } from "../ipc/contract";

const strict = { wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode> };

test("rule 3: a key unlocks only when its operation-specific confirmation matches", () => {
  const obj1 = { v: 1 };
  const { result, rerender } = renderHook(
    ({ collection }: { collection: Record<string, { v: number }> }) => usePendingConfirm(collection),
    { initialProps: { collection: { a: obj1 } as Record<string, { v: number }> }, ...strict },
  );

  act(() => {
    expect(result.current.begin("a", "Pausing…", (value) => value?.v === 2)).toBe(true);
  });
  expect(result.current.pending.a).toBe("Pausing…");

  // An unrelated change that keeps `a`'s reference must NOT unlock it.
  rerender({ collection: { a: obj1, b: { v: 9 } } });
  expect(result.current.pending.a).toBe("Pausing…");

  // A fresh object with the old value can be a reconnect snapshot or unrelated
  // progress. Object identity is not confirmation.
  rerender({ collection: { a: { v: 1 } } });
  expect(result.current.pending.a).toBe("Pausing…");

  // Only the requested semantic state unlocks.
  rerender({ collection: { a: { v: 2 } } });
  expect(result.current.pending.a).toBeUndefined();
});

test("rule 3: no-predicate lock (primitive collection) unlocks on any change to its value", () => {
  // Regression for the Settings-key P0: SettingsView calls begin() with no
  // `confirms` predicate. Before the fix `confirms` defaulted to () => false,
  // so saving/removing the OpenRouter key locked the row FOREVER. The default
  // must be "unlock when collection[key] changes" for primitive collections.
  // Typed separately: renderHook infers Props from `initialProps`, so an inline
  // literal would narrow the collection to {openrouter_key} and reject the
  // second key the "unrelated key" rerender below needs.
  const initialProps: { collection: Record<string, string> } = {
    collection: { openrouter_key: "missing" },
  };
  const { result, rerender } = renderHook(
    ({ collection }: { collection: Record<string, string> }) => usePendingConfirm(collection),
    { initialProps, ...strict },
  );

  act(() => {
    expect(result.current.begin("openrouter_key", "checking…")).toBe(true);
  });
  expect(result.current.pending.openrouter_key).toBe("checking…");

  // An unrelated key changing must not unlock it.
  rerender({ collection: { openrouter_key: "missing", theme: "dark" } });
  expect(result.current.pending.openrouter_key).toBe("checking…");

  // The Brain's settings_state reply changes the status → unlock.
  rerender({ collection: { openrouter_key: "set", theme: "dark" } });
  expect(result.current.pending.openrouter_key).toBeUndefined();
});

test("begin refuses a second lock on an already-pending key", () => {
  const { result } = renderHook(
    ({ collection }: { collection: Record<string, unknown> }) => usePendingConfirm(collection),
    { initialProps: { collection: {} }, ...strict },
  );

  act(() => {
    expect(result.current.begin("a", "first", () => false)).toBe(true);
  });
  act(() => {
    expect(result.current.begin("a", "second", () => true)).toBe(false);
  });
  expect(result.current.pending.a).toBe("first");
});

test("a new exact correlated error releases pending but a stale error does not", () => {
  const stale = {
    type: "error", id: "old", ts: "x", code: "unsupported", message: "old", recoverable: true,
    operation_kind: "task_op", operation_id: "a",
  } as const;
  const { result, rerender } = renderHook(
    ({ errors }: { errors: Record<string, ErrorMsg> }) => usePendingConfirm({ a: { v: 1 } }, errors),
    { initialProps: { errors: { "task_op:a": stale } as Record<string, ErrorMsg> }, ...strict },
  );
  act(() => { result.current.begin("a", "Pausing", () => false, "task_op"); });
  expect(result.current.pending.a).toBe("Pausing");
  rerender({ errors: { "task_op:a": { ...stale, id: "new", message: "Not supported." } } });
  expect(result.current.pending.a).toBeUndefined();
  expect(result.current.failures.a).toBe("Not supported.");
});
