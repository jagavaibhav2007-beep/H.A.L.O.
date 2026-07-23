import { useEffect, useRef, useState } from "react";
import type { ErrorMsg, OperationKind } from "../ipc/contract";
import { operationCorrelationKey } from "../ipc/contract";

/**
 * Rule 3 ("lock on press, unlock only on confirm"): tracks a pending label
 * per key. Each operation supplies a semantic predicate for the exact state
 * it expects; reconnect snapshots and unrelated updates therefore cannot
 * masquerade as confirmation merely by creating a fresh object.
 */
export function usePendingConfirm<T>(
  collection: Record<string, T>,
  operationErrors: Record<string, ErrorMsg> = {},
) {
  type Entry = {
    label: string;
    confirms: (value: T | undefined) => boolean;
    errorKey?: string;
    baselineErrorId?: string;
  };
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [failures, setFailures] = useState<Record<string, string>>({});
  const entriesRef = useRef<Record<string, Entry>>({});

  useEffect(() => {
    let changed = false;
    const next = { ...entriesRef.current };
    for (const [key, entry] of Object.entries(entriesRef.current)) {
      const failure = entry.errorKey ? operationErrors[entry.errorKey] : undefined;
      if (entry.confirms(collection[key]) || (failure && failure.id !== entry.baselineErrorId)) {
        delete next[key];
        if (failure) setFailures((current) => ({ ...current, [key]: failure.message }));
        changed = true;
      }
    }
    if (!changed) return;
    entriesRef.current = next;
    setEntries(next);
  }, [collection, operationErrors]);

  /** Lock `key` with `label` and return true, or return false if already locked. */
  const begin = (
    key: string,
    label: string,
    confirms: Entry["confirms"] = () => false,
    operationKind?: OperationKind,
  ) => {
    if (entriesRef.current[key]) return false;
    const errorKey = operationKind ? operationCorrelationKey(operationKind, key) : undefined;
    const next = {
      ...entriesRef.current,
      [key]: { label, confirms, errorKey, baselineErrorId: errorKey ? operationErrors[errorKey]?.id : undefined },
    };
    setFailures((current) => {
      if (!(key in current)) return current;
      const copy = { ...current };
      delete copy[key];
      return copy;
    });
    entriesRef.current = next;
    setEntries(next);
    return true;
  };

  const pending = Object.fromEntries(Object.entries(entries).map(([key, entry]) => [key, entry.label]));
  return { pending, failures, begin };
}
