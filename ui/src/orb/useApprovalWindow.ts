import { useEffect, useRef } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { currentMonitor, getCurrentWindow, LogicalPosition, LogicalSize } from "@tauri-apps/api/window";

export const PILL_WIDTH = 360;
export const COLLAPSED_HEIGHT = 52;
export const EXPANDED_HEIGHT = 224;

export interface Point { x: number; y: number }
export interface Rect extends Point { width: number; height: number }

export function fitApprovalWindow(position: Point, workArea: Rect, height: number): Point {
  return {
    x: Math.min(Math.max(position.x, workArea.x), Math.max(workArea.x, workArea.x + workArea.width - PILL_WIDTH)),
    y: Math.min(Math.max(position.y, workArea.y), Math.max(workArea.y, workArea.y + workArea.height - height)),
  };
}

export function useApprovalWindow(expanded: boolean) {
  const generationRef = useRef(0);
  const wasExpandedRef = useRef(false);
  const collapsedPositionRef = useRef<Point | null>(null);
  const expandedPositionRef = useRef<Point | null>(null);

  useEffect(() => {
    if (!isTauri()) return;
    const generation = ++generationRef.current;
    void (async () => {
      const window = getCurrentWindow();
      try {
        const scale = await window.scaleFactor();
        const position = (await window.outerPosition()).toLogical(scale);
        const monitor = await currentMonitor();
        if (generation !== generationRef.current) return;
        const workArea = monitor && {
          ...monitor.workArea.position.toLogical(monitor.scaleFactor),
          ...monitor.workArea.size.toLogical(monitor.scaleFactor),
        };

        if (expanded) {
          if (!wasExpandedRef.current) {
            wasExpandedRef.current = true;
            collapsedPositionRef.current = position;
          }
          const target = workArea
            ? fitApprovalWindow(collapsedPositionRef.current ?? position, workArea, EXPANDED_HEIGHT)
            : collapsedPositionRef.current ?? position;
          await window.setSize(new LogicalSize(PILL_WIDTH, EXPANDED_HEIGHT));
          if (generation !== generationRef.current) return;
          await window.setPosition(new LogicalPosition(target.x, target.y));
          if (generation !== generationRef.current) return;
          expandedPositionRef.current = target;
          return;
        }

        const collapsedPosition = collapsedPositionRef.current;
        const expandedPosition = expandedPositionRef.current;
        const restored = collapsedPosition && expandedPosition
          ? { x: collapsedPosition.x + position.x - expandedPosition.x, y: collapsedPosition.y + position.y - expandedPosition.y }
          : position;
        const target = workArea ? fitApprovalWindow(restored, workArea, COLLAPSED_HEIGHT) : restored;
        await window.setSize(new LogicalSize(PILL_WIDTH, COLLAPSED_HEIGHT));
        if (generation !== generationRef.current) return;
        await window.setPosition(new LogicalPosition(target.x, target.y));
        wasExpandedRef.current = false;
        collapsedPositionRef.current = null;
        expandedPositionRef.current = null;
      } catch (error) {
        // Keep the companion alive, but never hide a clipped approval panel
        // behind a swallowed native permission/window error again.
        console.error("halo: failed to resize the approval pill", error);
      }
    })();
  }, [expanded]);
}
