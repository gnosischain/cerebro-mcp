import type { ReactNode } from "react";

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  badge?: ReactNode;
  ariaLabel?: string;
}

interface Props<T extends string> {
  options: SegmentOption<T>[];
  value: T;
  onChange: (next: T) => void;
  ariaLabel: string;
  size?: "sm" | "md";
  disabled?: boolean;
}

export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  size = "md",
  disabled,
}: Props<T>) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={`seg seg--${size}`}>
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={opt.ariaLabel}
            disabled={disabled}
            onClick={() => {
              if (!active) onChange(opt.value);
            }}
            className={`seg__btn ${active ? "seg__btn--active" : ""}`}
          >
            {opt.label}
            {opt.badge != null && <span className="seg__badge">{opt.badge}</span>}
          </button>
        );
      })}
    </div>
  );
}
