import type { NumberDisplaySpec } from "../types";

interface Props {
  spec: NumberDisplaySpec;
}

export function NumberDisplay({ spec }: Props) {
  const formatted =
    typeof spec.value === "number" ? spec.value.toLocaleString() : spec.value;

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "1.5rem",
        minHeight: "120px",
      }}
    >
      <div style={{ textAlign: "center" }}>
        <div
          style={{
            fontSize: "2.5rem",
            fontWeight: 700,
            color: "var(--primary)",
          }}
        >
          {formatted}
        </div>
        {spec.title && (
          <div
            style={{
              fontSize: "0.875rem",
              color: "var(--text-muted)",
              marginTop: "0.25rem",
            }}
          >
            {spec.title}
          </div>
        )}
      </div>
    </div>
  );
}
