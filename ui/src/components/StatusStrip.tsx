interface Props {
  kind: "dashboard" | "research" | "scrollytelling";
  chartCount: number;
  queryCount: number;
  timestamp: string;
}

const KIND_LABEL: Record<Props["kind"], string> = {
  dashboard: "dashboard report",
  research: "research report",
  scrollytelling: "case study",
};

export function StatusStrip({ kind, chartCount, queryCount, timestamp }: Props) {
  return (
    <div className="report-cli">
      <span className="prompt">cerebro</span>
      <span style={{ opacity: 0.5 }}>/</span>
      <span>{KIND_LABEL[kind]}</span>
      <span className="spacer" />
      <span className="pill">
        <span className="dot" /> {chartCount} charts
      </span>
      <span className="pill">
        <span className="dot" /> {queryCount} queries
      </span>
      <span style={{ opacity: 0.6 }}>{timestamp}</span>
    </div>
  );
}
