import type { NumberDisplaySpec } from "../types";

interface Props {
  spec: NumberDisplaySpec;
}

export function NumberDisplay({ spec }: Props) {
  const formattedValue = formatScalar(spec.value, spec.format);
  const changeDirection = spec.change?.direction ?? inferDirection(spec.change?.value);
  const formattedChange = spec.change
    ? formatScalar(spec.change.value, spec.change.format, true)
    : "";

  return (
    <div className="number-display">
      {spec.title && <div className="number-display__eyebrow">{spec.title}</div>}
      <div className="number-display__value">{formattedValue}</div>
      {spec.change && (
        <div
          className={[
            "number-display__change",
            `number-display__change--${changeDirection}`,
          ].join(" ")}
        >
          <span className="number-display__change-value">{formattedChange}</span>
          {spec.change.label && (
            <span className="number-display__change-label">{spec.change.label}</span>
          )}
        </div>
      )}
    </div>
  );
}

function formatScalar(
  value: number | string,
  _format?: string,
  signed = false
): string {
  if (typeof value !== "number") {
    return value;
  }

  const maximumFractionDigits = Number.isInteger(value) ? 0 : 2;
  const formatted = value.toLocaleString(undefined, {
    maximumFractionDigits,
  });

  if (signed && value > 0) {
    return `+${formatted}`;
  }

  return formatted;
}

function inferDirection(
  value: number | string | undefined
): "positive" | "negative" | "neutral" {
  if (typeof value === "number") {
    if (value > 0) return "positive";
    if (value < 0) return "negative";
    return "neutral";
  }

  const text = String(value ?? "").trim();
  if (text.startsWith("+")) return "positive";
  if (text.startsWith("-")) return "negative";
  return "neutral";
}
