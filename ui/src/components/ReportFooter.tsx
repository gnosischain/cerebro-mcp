export function ReportFooter() {
  return (
    <footer
      style={{
        maxWidth: 1280,
        margin: "0 auto",
        padding: "1rem 1.5rem 2rem",
        borderTop: "1px solid var(--border)",
        textAlign: "center",
      }}
    >
      <p
        style={{
          fontSize: "0.6875rem",
          color: "var(--text-muted)",
        }}
      >
        Data sourced from Cerebro &middot; dbt-cerebro models &middot; Gnosis
        Chain ClickHouse
      </p>
    </footer>
  );
}
