// Compact multi-column picker: selected columns as chips + a checkbox
// dropdown. Selection preserves click order (the first pick is the primary
// measure / leading projection column).

import { useEffect, useRef, useState } from "react";
import { MaField } from "../shared/MaField";

interface ColumnMultiSelectProps {
  label: string;
  title: string;
  options: string[];
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  /** Hard cap on picks (extra checkboxes disable once reached). */
  maxSelections?: number;
  disabled?: boolean;
}

export function ColumnMultiSelect({
  label,
  title,
  options,
  value,
  onChange,
  placeholder = "all columns",
  maxSelections,
  disabled,
}: ColumnMultiSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = (name: string) => {
    if (value.includes(name)) {
      onChange(value.filter((v) => v !== name));
    } else {
      if (maxSelections !== undefined && value.length >= maxSelections) return;
      onChange([...value, name]); // append -> click order preserved
    }
  };

  const capReached =
    maxSelections !== undefined && value.length >= maxSelections;

  return (
    <MaField className="mlab-field mlab-colpick" title={title}>
      <label className="mlab-field-label">{label}</label>
      <div className="mlab-colpick-control" ref={rootRef}>
        <button
          type="button"
          className="mlab-colpick-trigger"
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {value.length === 0 ? (
            <span className="mlab-colpick-placeholder">{placeholder}</span>
          ) : (
            <span className="mlab-colpick-chips">
              {value.map((v) => (
                <span key={v} className="mlab-colpick-chip">
                  {v}
                  <span
                    role="button"
                    tabIndex={-1}
                    className="mlab-colpick-x"
                    aria-label={`Remove ${v}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      toggle(v);
                    }}
                  >
                    ×
                  </span>
                </span>
              ))}
            </span>
          )}
          <span className="mlab-colpick-caret" aria-hidden>
            ▾
          </span>
        </button>
        {open && !disabled && (
          <div className="mlab-colpick-menu" role="listbox" aria-label={label}>
            {options.map((name) => {
              const checked = value.includes(name);
              return (
                <label
                  key={name}
                  className={`mlab-colpick-option${checked ? " is-checked" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!checked && capReached}
                    onChange={() => toggle(name)}
                  />
                  <span className="mlab-colpick-name">{name}</span>
                </label>
              );
            })}
            {options.length === 0 && (
              <div className="mlab-colpick-empty">no columns</div>
            )}
          </div>
        )}
      </div>
    </MaField>
  );
}
