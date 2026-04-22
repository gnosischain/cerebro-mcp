import { useEffect, useRef } from "react";

export interface TabDef<T extends string> {
  id: T;
  label: string;
  badge?: string | number;
  disabled?: boolean;
}

interface Props<T extends string> {
  tabs: TabDef<T>[];
  active: T;
  onChange: (id: T) => void;
  scrollOnChange?: boolean;
  ariaLabel?: string;
}

export function TabBar<T extends string>({
  tabs,
  active,
  onChange,
  scrollOnChange = true,
  ariaLabel,
}: Props<T>) {
  const prev = useRef(active);
  useEffect(() => {
    if (scrollOnChange && prev.current !== active) {
      window.requestAnimationFrame(() =>
        window.scrollTo({ top: 0, behavior: "smooth" }),
      );
    }
    prev.current = active;
  }, [active, scrollOnChange]);

  return (
    <div className="tabbar" role="tablist" aria-label={ariaLabel}>
      {tabs.map((t) => {
        const isActive = t.id === active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            disabled={t.disabled}
            onClick={() => {
              if (!isActive && !t.disabled) onChange(t.id);
            }}
            className={`tabbar__tab ${isActive ? "tabbar__tab--active" : ""}`}
          >
            {t.label}
            {t.badge != null && t.badge !== 0 && (
              <span className="tabbar__badge">{t.badge}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
