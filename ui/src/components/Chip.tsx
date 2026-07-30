import type { LucideIcon as LucideIconType } from "lucide-react";
import { Icon } from "./Icon";
import "./primitives.css";

type ChipTone = "default" | "primary" | "tier3" | "destructive" | "success" | "muted";

interface ChipProps {
  icon: LucideIconType;
  label: string;
  tone?: ChipTone;
  className?: string;
}

/**
 * One Chip primitive used for tier, lane, and status pills — always
 * icon + text, never color alone (design-language "never color alone" rule).
 */
export function Chip({ icon, label, tone = "default", className }: ChipProps) {
  const classes = ["halo-chip", `halo-chip-${tone}`, className].filter(Boolean).join(" ");
  return (
    <span className={classes}>
      <Icon icon={icon} size={16} />
      <span>{label}</span>
    </span>
  );
}
