import type { LucideIcon as LucideIconType } from "lucide-react";
import "./primitives.css";

type IconSize = 16 | 20 | 24;

interface IconProps {
  icon: LucideIconType;
  size?: IconSize;
  className?: string;
}

/** Thin wrapper over lucide-react: locks stroke-width to 1.5 and size to 16/20/24. */
export function Icon({ icon: LucideGlyph, size = 20, className }: IconProps) {
  return <LucideGlyph size={size} strokeWidth={1.5} className={className} aria-hidden="true" />;
}
