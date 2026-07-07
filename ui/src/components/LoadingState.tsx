import type { CSSProperties } from "react";
import { Loader2, AlertCircle } from "lucide-react";

const containerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  minHeight: "60vh",
  gap: "1rem",
  color: "var(--text-muted)",
  padding: "1.5rem",
};

export function LoadingState({ timedOut = false }: { timedOut?: boolean }) {
  if (timedOut) {
    // The host never delivered the report data (e.g. Claude Desktop via an
    // mcp-remote bridge doesn't complete the ext-apps handshake). Show an
    // actionable fallback instead of spinning forever.
    return (
      <div style={containerStyle}>
        <AlertCircle size={32} style={{ color: "var(--text-muted)" }} />
        <p style={{ fontSize: "1rem", fontWeight: 600, margin: 0 }}>
          This client couldn&apos;t load the report inline
        </p>
        <p
          style={{
            fontSize: "0.875rem",
            maxWidth: "440px",
            textAlign: "center",
            lineHeight: 1.55,
            margin: 0,
          }}
        >
          The interactive panel didn&apos;t receive the report data from this
          host. Open the full report from the <strong>Open Report</strong> link
          in the chat message, or run <code>open_report(&quot;&lt;id&gt;&quot;)</code>.
          The report also renders in the Claude Code app or any browser.
        </p>
      </div>
    );
  }

  return (
    <div style={containerStyle}>
      <Loader2
        size={32}
        style={{
          animation: "spin 1s linear infinite",
          color: "var(--primary)",
        }}
      />
      <p style={{ fontSize: "0.875rem" }}>Loading report data...</p>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
