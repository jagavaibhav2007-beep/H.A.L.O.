import { expect, test } from "vitest";
import { fitApprovalWindow } from "./useApprovalWindow";

test("keeps an in-bounds approval pill location unchanged", () => {
  expect(fitApprovalWindow({ x: 20, y: 20 }, { x: 0, y: 0, width: 1920, height: 1040 }, 224)).toEqual({ x: 20, y: 20 });
});

test("moves a bottom-edge pill upward before expanding", () => {
  expect(fitApprovalWindow({ x: 20, y: 980 }, { x: 0, y: 0, width: 1920, height: 1040 }, 224)).toEqual({ x: 20, y: 816 });
});

test("allows the original collapsed location after the shorter restore", () => {
  expect(fitApprovalWindow({ x: 20, y: 980 }, { x: 0, y: 0, width: 1920, height: 1040 }, 52)).toEqual({ x: 20, y: 980 });
});

test("anchors an oversized pill to the work-area origin", () => {
  expect(fitApprovalWindow({ x: 80, y: 80 }, { x: 10, y: 20, width: 320, height: 40 }, 224)).toEqual({ x: 10, y: 20 });
});
