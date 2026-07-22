import {
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

interface Props {
  label?: string;
  className?: string;
  panelClassName?: string;
  /** Keep secondary controls behind the disclosure on wide screens too. */
  collapsibleOnDesktop?: boolean;
  children: ReactNode;
}

const DESKTOP_QUERY = "(min-width: 900px)";

function desktopMatches(): boolean {
  return typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(DESKTOP_QUERY).matches;
}

/**
 * Structural task filters.
 *
 * Desktop filters are ordinary toolbar content. On compact viewports the same
 * content becomes one controlled disclosure; it never relies on overflow from
 * a collapsed `<details>` box, so visible controls remain inside the toolbar's
 * layout and hit-testing bounds.
 */
export function FilterDrawer({
  label = "Filters",
  className = "",
  panelClassName = "ge-topbar-filters",
  collapsibleOnDesktop = false,
  children,
}: Props) {
  const panelId = useId();
  const toggleRef = useRef<HTMLButtonElement>(null);
  const [desktop, setDesktop] = useState(desktopMatches);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(DESKTOP_QUERY);
    const update = () => {
      setDesktop(media.matches);
      if (media.matches && !collapsibleOnDesktop) setOpen(false);
    };
    update();
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, [collapsibleOnDesktop]);

  useEffect(() => {
    if (!open || (desktop && !collapsibleOnDesktop)) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      window.requestAnimationFrame(() => toggleRef.current?.focus());
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [collapsibleOnDesktop, desktop, open]);

  const forcedOpen = desktop && !collapsibleOnDesktop;
  const expanded = forcedOpen || open;

  return (
    <div
      className={`ge-filter-drawer${open ? " is-open" : ""}${
        collapsibleOnDesktop ? " ge-filter-drawer--desktop-collapsible" : ""
      }${
        className ? ` ${className}` : ""
      }`}
    >
      <button
        ref={toggleRef}
        type="button"
        className="ge-filter-drawer__toggle"
        aria-expanded={expanded}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        {label}
        <span aria-hidden>{open ? " ▴" : " ▾"}</span>
      </button>
      <div
        id={panelId}
        className={panelClassName}
        hidden={!expanded}
      >
        {children}
      </div>
    </div>
  );
}

export default FilterDrawer;
