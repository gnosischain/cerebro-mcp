import type { ReactNode } from "react";

interface MaFieldProps {
  /** Leading icon/glyph (decorative). */
  icon?: ReactNode;
  /** Trailing unit/adornment or action button. */
  trailing?: ReactNode;
  children: ReactNode;
  className?: string;
  title?: string;
}

/**
 * Labeled input wrapper — the "pill" affordance the graph topbar uses: leading
 * icon slot + control + trailing unit/action slot, with one border/focus
 * style. Pass the actual `<input>`/`<select>` as children. Token-styled.
 */
export function MaField({ icon, trailing, children, className, title }: MaFieldProps) {
  return (
    <div className={`ma-field${className ? ` ${className}` : ""}`} title={title}>
      {icon != null && <span className="ma-field-icon" aria-hidden>{icon}</span>}
      {children}
      {trailing != null && <span className="ma-field-trailing">{trailing}</span>}
    </div>
  );
}
