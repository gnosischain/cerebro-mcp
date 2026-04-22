import type { MouseEventHandler, ReactNode } from "react";

type Tone = "positive" | "warning" | "negative" | "neutral";

export interface DataCardDelta {
  pct: number;
  absolute?: ReactNode;
  reference?: string;
}

interface Props {
  label: string;
  value: ReactNode;
  delta?: DataCardDelta;
  tone?: Tone;
  onClick?: MouseEventHandler<HTMLButtonElement>;
  footer?: ReactNode;
  className?: string;
}

function inferTone(delta: DataCardDelta | undefined, isNew: boolean): Tone {
  if (delta == null || isNew) return "neutral";
  if (delta.pct >= 0.05) return "positive";
  if (delta.pct <= -0.05) return "negative";
  return "warning";
}

export function DataCard({
  label,
  value,
  delta,
  tone,
  onClick,
  footer,
  className,
}: Props) {
  // Refinement (plan §3.4, §11b item 2): when `prior` was NULL upstream the
  // backend omits delta or passes a non-finite pct. We render "N/A (New)"
  // instead of Infinity/NaN.
  const isNew =
    delta != null &&
    (!Number.isFinite(delta.pct) || Number.isNaN(delta.pct));
  const appliedTone = tone ?? inferTone(delta, isNew);

  const classes = `data-card data-card--${appliedTone} ${
    onClick ? "data-card--clickable" : ""
  } ${className ?? ""}`.trim();

  const content = (
    <>
      <div className="data-card__label">{label}</div>
      <div className="data-card__value">{value}</div>
      {delta != null && !isNew && (
        <div className="data-card__delta">
          <span>
            {delta.pct >= 0 ? "▲" : "▼"} {(delta.pct * 100).toFixed(1)}%
          </span>
          {delta.absolute != null && (
            <span className="data-card__delta-abs">{delta.absolute}</span>
          )}
          {delta.reference && (
            <span className="data-card__delta-ref">vs {delta.reference}</span>
          )}
        </div>
      )}
      {isNew && (
        <div className="data-card__delta data-card__delta--new">N/A (New)</div>
      )}
      {footer && <div className="data-card__footer">{footer}</div>}
    </>
  );

  if (onClick) {
    return (
      <button type="button" className={classes} onClick={onClick}>
        {content}
      </button>
    );
  }
  return <div className={classes}>{content}</div>;
}
