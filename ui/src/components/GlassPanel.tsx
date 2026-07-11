import type { HTMLAttributes, ReactNode } from "react";
import "../styles/glass.css";
import "./primitives.css";

export type Elevation = "companion" | "panel" | "card" | "modal";

interface GlassPanelProps extends HTMLAttributes<HTMLDivElement> {
  elevation?: Elevation;
  children: ReactNode;
}

/** Glass surface at one of the four elevation steps (companion < panel < card < modal). */
export function GlassPanel({ elevation = "panel", className, children, ...rest }: GlassPanelProps) {
  const classes = ["glass", "halo-panel", `halo-elevation-${elevation}`, className].filter(Boolean).join(" ");
  return (
    <div className={classes} {...rest}>
      {children}
    </div>
  );
}
