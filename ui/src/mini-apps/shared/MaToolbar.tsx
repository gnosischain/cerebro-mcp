import type { ReactNode } from "react";

interface ToolbarProps {
  children: ReactNode;
  /** Extra class for app-specific spacing tweaks. */
  className?: string;
  /** Pushes the trailing children to the right edge. */
  align?: "start" | "between";
}

/**
 * Standard horizontal control row for mini-app toolbars: consistent height,
 * gap, wrap, and vertical alignment. Hosts search inputs, segmented toggles,
 * and action buttons. Token-styled (no hardcoded colors) so it themes with the
 * rest of the mini-app shell.
 */
export function MaToolbar({ children, className, align = "start" }: ToolbarProps) {
  return (
    <div className={`ma-toolbar ma-toolbar--${align}${className ? ` ${className}` : ""}`}>
      {children}
    </div>
  );
}
