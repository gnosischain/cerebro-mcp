import { Loader2 } from "lucide-react";

export function LoadingState() {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minHeight: "60vh",
        gap: "1rem",
        color: "var(--text-muted)",
      }}
    >
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
