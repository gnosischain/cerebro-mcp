import type { SummaryCard } from "./miniAppTypes";

interface Props {
  cards: SummaryCard[];
}

export function SummaryCards({ cards }: Props) {
  if (!cards.length) return null;
  return (
    <section className="mini-app-summary-grid">
      {cards.map((card, index) => (
        <div
          key={`${index}-${card.label}`}
          className={`mini-app-summary-card tone-${card.tone ?? "neutral"}`}
        >
          <div className="mini-app-summary-label">{card.label}</div>
          <div className="mini-app-summary-value">{card.value}</div>
          {card.delta ? <div className="mini-app-summary-delta">{card.delta}</div> : null}
        </div>
      ))}
    </section>
  );
}
