import { useId, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

interface Props {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  tone?: "default" | "subtle";
  action?: ReactNode;
}

export function CollapsibleSection({
  title,
  children,
  defaultOpen = false,
  tone = "default",
  action,
}: Props) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();
  const Icon = open ? ChevronDown : ChevronRight;

  return (
    <section className={`collapsible collapsible--${tone}`}>
      <header className="collapsible__header">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((o) => !o)}
          className="collapsible__toggle"
        >
          <Icon size={16} aria-hidden />
          <span>{title}</span>
        </button>
        {action && <div className="collapsible__action">{action}</div>}
      </header>
      <div
        id={bodyId}
        className="collapsible__body"
        data-open={open}
        aria-hidden={!open}
      >
        {open && <div>{children}</div>}
      </div>
    </section>
  );
}
