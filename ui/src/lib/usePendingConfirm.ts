import { useEffect, useRef, useState } from "react";

/**
 * Rule 3 ("lock on press, unlock only on confirm"): tracks a pending label
 * per key, cleared only when `collection` upserts a NEW object for that key —
 * a changed reference is the Brain's confirming frame (every *_state upserts
 * a fresh object). Never resolves optimistically.
 *
 * `prev` is captured BEFORE mutating the ref so the updater passed to
 * setPending is a pure function of its closure — React StrictMode
 * double-invokes function-form updaters in dev specifically to catch side
 * effects like mutating a ref inside one (see mem/Bugs.md, "Rule-3 unlock
 * on confirm").
 */
export function usePendingConfirm<T>(collection: Record<string, T>) {
  const [pending, setPending] = useState<Record<string, string>>({});
  const prevRefs = useRef<Record<string, T>>({});
  useEffect(() => {
    const prev = prevRefs.current;
    prevRefs.current = collection;
    setPending((p) => {
      let changed = false;
      const next = { ...p };
      for (const key of Object.keys(p)) {
        if (collection[key] !== prev[key]) {
          delete next[key];
          changed = true;
        }
      }
      return changed ? next : p;
    });
  }, [collection]);

  /** Lock `key` with `label` and return true, or return false if already locked. */
  const begin = (key: string, label: string) => {
    if (pending[key]) return false;
    setPending((p) => ({ ...p, [key]: label }));
    return true;
  };

  return { pending, begin };
}
