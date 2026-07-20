import { useEffect, type ReactNode, type RefObject } from "react";

export type TaskAccessoryPanelKind = "evidence" | "details" | null;

interface Props {
  title: string;
  subtitle?: string | null;
  onClose: () => void;
  openerRef?: RefObject<HTMLElement | null>;
  children: ReactNode;
  className?: string;
  ariaLabel?: string;
}

/**
 * One predictable task-level accessory surface. Desktop reserves space for
 * it; compact layouts reserve a bottom sheet. It is deliberately outside the
 * graph stage so evidence and inspectors never cover rendered evidence.
 */
export function TaskAccessoryPanel({
  title,
  subtitle,
  onClose,
  openerRef,
  children,
  className = "",
  ariaLabel,
}: Props) {
  const restoreFocus = () => {
    // The opener is outside the panel and remains mounted, so restore before
    // the closing state update unmounts this component. This also avoids a
    // frame where keyboard focus falls back to <body>.
    openerRef?.current?.focus();
  };
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      restoreFocus();
      onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, openerRef]);

  const close = () => {
    restoreFocus();
    onClose();
  };

  return (
    <aside
      className={`ge-task-accessory-panel ${className}`.trim()}
      role="dialog"
      aria-modal="false"
      aria-label={ariaLabel || title}
    >
      <header className="ge-task-accessory-panel__header">
        <div>
          <strong>{title}</strong>
          {subtitle ? <span>{subtitle}</span> : null}
        </div>
        <button
          type="button"
          className="ge-icon-btn"
          onClick={close}
          aria-label={`Close ${title.toLowerCase()}`}
        >
          ×
        </button>
      </header>
      <div className="ge-task-accessory-panel__body">{children}</div>
    </aside>
  );
}
