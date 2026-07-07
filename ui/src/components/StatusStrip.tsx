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
      <span className="dots" aria-hidden="true">
        <span className="dot-r" />
        <span className="dot-v" />
        <span className="dot-l" />
      </span>
      <span className="whoami">
        <span className="user">cerebro@gnosis</span>
        <span className="sep">:</span>
        <span className="path">~/reports</span>
      </span>
      <span className="spacer" />
      <span className="meta">
        {chartCount} charts · {queryCount} queries · {dateOnly}
      </span>
      <button
        className="term-toggle no-print"
        onClick={toggle}
        title="Toggle theme"
      >
        ◐ {isDark ? "Dark" : "Light"}
      </button>
    </div>
  );
}
