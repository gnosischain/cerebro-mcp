import { Sun, Moon, ExternalLink } from "lucide-react";
import { useTheme } from "../hooks/useTheme";
import { WATERMARK_LIGHT, WATERMARK_DARK } from "../assets/watermark";

interface Props {
  title: string;
  timestamp: string;
  fileUri?: string;
}

export function ReportHeader({ title, timestamp, fileUri }: Props) {
  const { isDark, toggle } = useTheme();

  // Use the owl icon as the header logo — light theme gets dark icon, dark theme gets white icon
  const logoSrc = isDark ? WATERMARK_DARK : WATERMARK_LIGHT;

  return (
    <header className="report-header">
      <div className="report-header-inner">
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <img
            src={logoSrc}
            alt="Gnosis"
            style={{ width: 28, height: 28 }}
          />
          <div>
            <h1
              style={{
                fontSize: "1.125rem",
                fontWeight: 600,
                letterSpacing: "-0.01em",
                margin: 0,
                color: "var(--text-primary)",
              }}
            >
              {title}
            </h1>
            <p
              style={{
                color: "var(--text-muted)",
                fontSize: "0.75rem",
                margin: 0,
              }}
            >
              {timestamp} &middot; Cerebro / dbt-cerebro
            </p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}>
          {fileUri && (
            <a
              href={fileUri}
              target="_blank"
              rel="noopener noreferrer"
              className="no-print theme-toggle"
              title="Open in browser"
            >
              <ExternalLink size={16} />
            </a>
          )}
          <button
            className="no-print theme-toggle"
            onClick={toggle}
            title="Toggle theme"
          >
            {isDark ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </div>
    </header>
  );
}
