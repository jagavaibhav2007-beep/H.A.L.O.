import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";

describe("approval notifications", () => {
  test("the floating pill is the only approval notification surface", () => {
    const source = readFileSync("src/workspace/WorkspaceRoot.tsx", "utf8");

    expect(source).not.toContain("sendNotification(");
    expect(source).not.toContain("requestPermission(");
  });
});
