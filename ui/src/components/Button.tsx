import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./primitives.css";

export type ButtonVariant = "primary" | "ghost" | "destructive";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /**
   * Destructive actions must confirm before firing (design-language rule:
   * "destructive buttons are red, physically separated, and confirm").
   * This flag is the visual affordance only — the real hold-to-approve /
   * confirm-dialog wiring is a Step-10 concern; callers gate onClick
   * themselves until then.
   */
  confirm?: boolean;
  children: ReactNode;
}

export function Button({ variant = "primary", confirm, className, children, ...rest }: ButtonProps) {
  const classes = ["halo-btn", `halo-btn-${variant}`, className].filter(Boolean).join(" ");
  return (
    <button className={classes} data-confirm={confirm ? "true" : undefined} {...rest}>
      {children}
    </button>
  );
}
