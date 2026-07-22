import { useEffect, useRef, useState, type ReactNode } from "react";

// Single-open coordination: opening one popover closes any other. A module
// listener set keeps this dependency-free (no context provider needed).
const closers = new Set<() => void>();

/** Controlled info popover: closes on outside click / Escape, and only one
 * popover is open at a time (the old bare <details> version let popovers
 * stack up and orphan across the page). */
export function InfoPopover({ label = "Info", children }: { label?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    closers.add(close);
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      closers.delete(close);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="cow-info-popover" ref={rootRef} data-open={open ? "true" : undefined}>
      <button
        type="button"
        className="cow-info-popover__summary"
        aria-expanded={open}
        aria-label={label}
        onClick={() => {
          if (!open) {
            for (const close of [...closers]) close();
            setOpen(true);
          } else {
            setOpen(false);
          }
        }}
      >
        ⓘ <span>{label}</span>
      </button>
      {open && <div className="cow-info-popover__panel">{children}</div>}
    </div>
  );
}

/** Standard three-block info body: What this is · How it's computed · Coverage. */
export function InfoBlocks({ what, method, coverage }: {
  what?: string;
  method?: string;
  coverage?: string;
}) {
  return (
    <div className="cow-info-blocks">
      {what && (
        <div>
          <strong>What this is</strong>
          <p>{what}</p>
        </div>
      )}
      {method && (
        <div>
          <strong>How it&apos;s computed</strong>
          <p>{method}</p>
        </div>
      )}
      {coverage && (
        <div>
          <strong>Coverage</strong>
          <p className="cow-info-blocks__coverage">{coverage}</p>
        </div>
      )}
    </div>
  );
}
