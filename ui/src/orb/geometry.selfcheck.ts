import { nextOrbSize } from "./geometry.ts";

function assert(actual: number, expected: number, label: string) {
  if (actual !== expected) throw new Error(`${label}: expected ${expected}, got ${actual}`);
}

assert(nextOrbSize(64, -10, 0), 54, "horizontal shrink");
assert(nextOrbSize(64, 0, -10), 54, "vertical shrink");
assert(nextOrbSize(64, 20, 5), 84, "dominant horizontal growth");
assert(nextOrbSize(64, 5, 20), 84, "dominant vertical growth");
assert(nextOrbSize(64, -100, 0), 48, "minimum clamp");
assert(nextOrbSize(64, 100, 0), 128, "maximum clamp");

console.log("[orb.geometry.selfcheck] OK");
