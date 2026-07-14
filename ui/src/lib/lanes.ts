import { ShieldAlert, Users, Zap, type LucideIcon } from "lucide-react";

export const LANE_LABEL: Record<1 | 2 | 3, string> = { 1: "Fast", 2: "Takeover", 3: "Sandbox" };
export const LANE_ICON: Record<1 | 2 | 3, LucideIcon> = { 1: Zap, 2: Users, 3: ShieldAlert };
