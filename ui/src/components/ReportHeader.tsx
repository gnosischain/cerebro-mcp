import { Sun, Moon } from "lucide-react";
import { useTheme } from "../hooks/useTheme";

interface Props {
  title: string;
  timestamp: string;
  subtitle?: string;
  fileUri?: string;
}

function splitTitle(t: string) {
  const i = t.indexOf("—");
  if (i === -1) return <>{t}</>;
  return (
    <>
      {t.slice(0, i)}
      <em>{t.slice(i)}</em>
    </>
  );
}

export function ReportHeader({ title, subtitle }: Props) {
  const { isDark, toggle } = useTheme();

  return (
    <header className="report-header">
      <div className="report-header-inner">
        <div className="report-header-meta">
          <span className="tag">Dashboard</span>
          <span>Cerebro · dbt-cerebro</span>
        </div>
        <h1 className="report-header-title">{splitTitle(title)}</h1>
        {subtitle && <p className="report-header-subtitle">{subtitle}</p>}
      </div>
      <div className="report-header-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" fill="none">
          <circle cx="16" cy="16" r="13" strokeWidth="1.4" />
          <circle cx="11.5" cy="14" r="3" strokeWidth="1.4" />
          <circle cx="20.5" cy="14" r="3" strokeWidth="1.4" />
          <circle cx="11.5" cy="14" r="0.9" fill="currentColor" />
          <circle cx="20.5" cy="14" r="0.9" fill="currentColor" />
          <path
            d="M14 19.2 L16 21 L18 19.2"
            strokeWidth="1.4"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <button
        className="theme-toggle no-print"
        onClick={toggle}
        title="Toggle theme"
      >
        {isDark ? <Sun size={18} /> : <Moon size={18} />}
      </button>
    </header>
  );
}
