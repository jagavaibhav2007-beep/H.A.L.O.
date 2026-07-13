const MIN_ORB_PX = 48;
const MAX_ORB_PX = 128;

export function nextOrbSize(startSize: number, dx: number, dy: number): number {
  const delta = Math.abs(dx) >= Math.abs(dy) ? dx : dy;
  return Math.round(Math.min(MAX_ORB_PX, Math.max(MIN_ORB_PX, startSize + delta)));
}
