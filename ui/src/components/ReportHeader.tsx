import { useState } from "react";
import { Sun, Moon, ExternalLink, Copy, Check } from "lucide-react";
import { useTheme } from "../hooks/useTheme";
import { WATERMARK_LIGHT, WATERMARK_DARK } from "../assets/watermark";

interface Props {
  title: string;
  timestamp: string;
  subtitle?: string;
  fileUri?: string;
}

export function ReportHeader({ title, timestamp, subtitle, fileUri }: Props) {
  const { isDark, toggle } = useTheme();
  const [copied, setCopied] = useState(false);

  const logoSrc = isDark ? WATERMARK_DARK : WATERMARK_LIGHT;
  const isFileUri = fileUri?.startsWith("file://");

  return (
    <header className="report-header">
      <div className="report-header-inner">
        <div className="report-header-titleblock">
          <img
            src={logoSrc}
            alt="Gnosis"
            className="report-header-logo"
          />
          <div className="report-header-text">
            <h1 className="report-header-title">{title}</h1>
            {subtitle && (
              <p className="report-header-subtitle">{subtitle}</p>
            )}
            <p className="report-header-meta">
              {timestamp} &middot; Cerebro / dbt-cerebro
            </p>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.25rem" }}>
          {fileUri && isFileUri && (
            <button
              onClick={() => {
                navigator.clipboard.writeText(fileUri);
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              }}
              className="no-print theme-toggle"
              title="Copy report path"
            >
              {copied ? <Check size={16} /> : <Copy size={16} />}
            </button>
          )}
          {fileUri && !isFileUri && (
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
