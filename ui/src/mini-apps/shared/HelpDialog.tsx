import { useEffect, useState, type ReactNode } from "react";
import { HelpCircle, X } from "lucide-react";

/** A single labelled block of help copy. `body` may be a string or rich JSX. */
export interface HelpSection {
  heading: string;
  body: ReactNode;
}

/** Per-app usage guide rendered inside the help dialog. */
export interface HelpContent {
  /** Dialog title — usually the app name. */
  title: string;
  /** One-line summary shown under the title. */
  intro: string;
  /** Ordered "how to use this" sections. */
  sections: HelpSection[];
}

interface MaHelpButtonProps {
  content: HelpContent;
}

/**
 * Standard info affordance for every mini-app. Renders an ⓘ button (mount it in
 * `MiniAppChrome`'s `rightSlot`) that opens a modal explaining what the app
 * shows and how to drive its controls. Closes on Escape, backdrop click, or ✕.
 */
export function MaHelpButton({ content }: MaHelpButtonProps) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      <button
        className="ma-help-btn"
        onClick={() => setOpen(true)}
        title={`How to use ${content.title}`}
        aria-label={`How to use ${content.title}`}
        type="button"
      >
        <HelpCircle size={14} />
      </button>
      {open && (
        <div
          className="ma-help-overlay mini-app-scope"
          role="dialog"
          aria-modal="true"
          aria-label={`${content.title} help`}
          onClick={() => setOpen(false)}
        >
          <div className="ma-help-dialog" onClick={(e) => e.stopPropagation()}>
            <header className="ma-help-head">
              <div>
                <h2 className="ma-help-title">{content.title}</h2>
                <p className="ma-help-intro">{content.intro}</p>
              </div>
              <button
                className="ma-help-close"
                onClick={() => setOpen(false)}
                aria-label="Close help"
                type="button"
              >
                <X size={16} />
              </button>
            </header>
            <div className="ma-help-body">
              {content.sections.map((s) => (
                <section key={s.heading} className="ma-help-section">
                  <h3 className="ma-help-section-heading">{s.heading}</h3>
                  <div className="ma-help-section-body">{s.body}</div>
                </section>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
