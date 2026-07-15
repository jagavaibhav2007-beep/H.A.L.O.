import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./primitives.css";

export type ButtonVariant = "primary" | "ghost" | "destructive";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  children: ReactNode;
}

export function Button({ variant = "primary", className, children, ...rest }: ButtonProps) {
  const classes = ["halo-btn", `halo-btn-${variant}`, className].filter(Boolean).join(" ");
  return (
    <button className={classes} {...rest}>
      {children}
    </button>
  );
}
