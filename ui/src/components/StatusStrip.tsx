import { useTheme } from "../hooks/useTheme";

interface Props {
  kind: "dashboard" | "research" | "scrollytelling";
  chartCount: number;
  queryCount: number;
  timestamp: string;
}

export function StatusStrip({ chartCount, queryCount, timestamp }: Props) {
  const { isDark, toggle } = useTheme();
  const dateOnly = timestamp.match(/\d{4}-\d{2}-\d{2}/)?.[0] ?? timestamp;

  return (
    <div className="report-cli">
      <span className="meta">
        {chartCount} charts · {queryCount} queries · {dateOnly}
      </span>
      <span className="spacer" />
      <button
        className="term-toggle no-print"
        onClick={toggle}
        title="Toggle theme"
        aria-label="Toggle light or dark theme"
      >
        ◐ {isDark ? "Dark" : "Light"}
      </button>
    </div>
  );
}
