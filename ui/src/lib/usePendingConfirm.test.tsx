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

const strict = { wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode> };

test("rule 3: a key unlocks only when the collection upserts a fresh object reference for it", () => {
  const obj1 = { v: 1 };
  const { result, rerender } = renderHook(
    ({ collection }: { collection: Record<string, { v: number }> }) => usePendingConfirm(collection),
    { initialProps: { collection: { a: obj1 } as Record<string, { v: number }> }, ...strict },
  );

  act(() => {
    expect(result.current.begin("a", "Approving…")).toBe(true);
  });
  expect(result.current.pending.a).toBe("Approving…");

  // An unrelated change that keeps `a`'s reference must NOT unlock it.
  rerender({ collection: { a: obj1, b: { v: 9 } } });
  expect(result.current.pending.a).toBe("Approving…");

  // The Brain's confirming frame = a fresh object for `a`. Now it unlocks --
  // and must survive StrictMode's double-invoked updater.
  rerender({ collection: { a: { v: 2 } } });
  expect(result.current.pending.a).toBeUndefined();
});

test("begin refuses a second lock on an already-pending key", () => {
  const { result } = renderHook(
    ({ collection }: { collection: Record<string, unknown> }) => usePendingConfirm(collection),
    { initialProps: { collection: {} }, ...strict },
  );

  act(() => {
    expect(result.current.begin("a", "first")).toBe(true);
  });
  act(() => {
    expect(result.current.begin("a", "second")).toBe(false);
  });
  expect(result.current.pending.a).toBe("first");
});
