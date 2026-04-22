export interface ChipOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

interface Props<T extends string> {
  options: ChipOption<T>[];
  selected: T[];
  onChange: (next: T[]) => void;
  label?: string;
  allowAllToggle?: boolean;
}

export function FilterChips<T extends string>({
  options,
  selected,
  onChange,
  label,
  allowAllToggle = true,
}: Props<T>) {
  const allOn = options.length > 0 && selected.length === options.length;

  const toggle = (v: T) =>
    onChange(
      selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v],
    );

  return (
    <div className="chips">
      {label && <span className="chips__label">{label}</span>}
      {allowAllToggle && options.length > 1 && (
        <button
          type="button"
          className={`chips__all ${allOn ? "chips__all--on" : ""}`}
          onClick={() => onChange(allOn ? [] : options.map((o) => o.value))}
        >
          {allOn ? "clear all" : "select all"}
        </button>
      )}
      {options.map((opt) => {
        const on = selected.includes(opt.value);
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => toggle(opt.value)}
            aria-pressed={on}
            className={`chip ${on ? "chip--on" : ""}`}
          >
            {opt.label}
            {opt.count != null && (
              <span className="chip__count">{opt.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
