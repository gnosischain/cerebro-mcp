import type { ReactNode } from "react";
import { MaField } from "./MaField";

interface MaSearchInputProps {
  value: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  placeholder?: string;
  actionLabel?: ReactNode;
  icon?: ReactNode;
  disabled?: boolean;
  /** Disable only the trailing action button (input stays editable). */
  actionDisabled?: boolean;
  busy?: boolean;
  /** Constrain the input width; defaults to flexible. */
  className?: string;
  inputMode?: "text" | "search";
  ariaLabel?: string;
}

/**
 * Standardized search/text input built on MaField with a trailing primary
 * action button (Load / Explore / Search). Submits on Enter or button click.
 * Token-styled so it matches across every mini-app.
 */
export function MaSearchInput({
  value,
  onChange,
  onSubmit,
  placeholder,
  actionLabel = "Go",
  icon,
  disabled,
  actionDisabled,
  busy,
  className,
  ariaLabel,
}: MaSearchInputProps) {
  return (
    <MaField
      icon={icon}
      className={`ma-search${className ? ` ${className}` : ""}`}
      trailing={
        <button
          type="button"
          className="ma-search-action"
          onClick={onSubmit}
          disabled={disabled || actionDisabled || busy}
        >
          {busy ? "…" : actionLabel}
        </button>
      }
    >
      <input
        type="text"
        aria-label={ariaLabel}
        className="ma-search-input"
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onSubmit();
          }
        }}
      />
    </MaField>
  );
}
