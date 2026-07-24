import type { NumberDisplaySpec } from "../types";

interface Props {
  spec: NumberDisplaySpec;
  /** Title already rendered by the hosting card — suppress the duplicate
   * eyebrow when the spec's title matches it. */
  cardTitle?: string;
}

function sameTitle(a?: string, b?: string): boolean {
  if (!a || !b) return false;
  const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  return norm(a) === norm(b);
}

export function NumberDisplay({ spec, cardTitle }: Props) {
  const formattedValue = formatScalar(spec.value, spec.format);
  const changeDirection = spec.change?.direction ?? inferDirection(spec.change?.value);
  const formattedChange = spec.change
    ? formatScalar(spec.change.value, spec.change.format, true)
    : "";

  const deltaClass =
    changeDirection === "negative"
      ? "nd-delta neg"
      : changeDirection === "neutral"
      ? "nd-delta flat"
      : "nd-delta";

  return (
    <div className="number-display number-display--gnosis">
      {spec.title && !sameTitle(spec.title, cardTitle) && (
        <div className="nd-eyebrow">{spec.title}</div>
      )}
      <div className="nd-value">{formattedValue}</div>
      {spec.change && (
        <div className={deltaClass}>
          <span className="nd-delta-value">{formattedChange}</span>
          {spec.change.label && (
            <span className="nd-delta-label"> {spec.change.label}</span>
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
