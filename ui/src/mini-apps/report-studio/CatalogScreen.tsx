// Template catalog: browsable cards of benchmarked instruction templates.
// Pure presentation over the compiled CATALOG — no tool calls.

import { useMemo, useState } from "react";
import {
  CATALOG,
  TIER_LABELS,
  formatCost,
  formatDuration,
  modelLabel,
  type InstructionTemplate,
} from "./model/catalog";

const CATEGORY_LABELS: Record<string, string> = {
  answer: "Answers",
  chart: "Charts",
  sector_health: "Sector health",
  deep_dive: "Deep dives",
  narrative: "Narratives",
  attribution: "Attribution",
  forecast: "Forecasts",
  governance: "Governance",
  utility: "Utilities",
};

type SortKey = "tier" | "fastest" | "cheapest";

function bestMeasurement(template: InstructionTemplate) {
  const entries = Object.entries(template.measurements);
  if (entries.length === 0) return null;
  // Prefer the cheapest measured model for the card's headline numbers.
  entries.sort((a, b) => (a[1].cost_usd?.median ?? 1e9) - (b[1].cost_usd?.median ?? 1e9));
  return { model: entries[0][0], data: entries[0][1] };
}

function CardMetrics({ template }: { template: InstructionTemplate }) {
  const models = Object.entries(template.measurements);
  if (models.length === 0) {
    return <span className="rst-tcard-badge rst-tcard-badge--unmeasured">not yet measured</span>;
  }
  return (
    <span className="rst-tcard-metrics">
      {models.map(([model, m]) => (
        <span key={model} className="rst-tcard-metric" title={`measured on ${model}`}>
          <span className="rst-tcard-metric__model">{modelLabel(model)}</span>
          {m.duration_ms ? formatDuration(m.duration_ms.median) : "—"}
          {" · "}
          {m.cost_usd ? formatCost(m.cost_usd.median) : "—"}
          {m.review_total ? ` · review ${m.review_passed}/${m.review_total}` : ""}
        </span>
      ))}
    </span>
  );
}

export function CatalogScreen({ onOpen }: { onOpen: (id: string) => void }) {
  const [category, setCategory] = useState<string>("all");
  const [sort, setSort] = useState<SortKey>("tier");

  const categories = useMemo(
    () => [...new Set(CATALOG.map((t) => t.category))],
    [],
  );

  const visible = useMemo(() => {
    const filtered = category === "all"
      ? [...CATALOG]
      : CATALOG.filter((t) => t.category === category);
    if (sort === "fastest") {
      filtered.sort(
        (a, b) =>
          (bestMeasurement(a)?.data.duration_ms?.median ?? Infinity)
          - (bestMeasurement(b)?.data.duration_ms?.median ?? Infinity),
      );
    } else if (sort === "cheapest") {
      filtered.sort(
        (a, b) =>
          (bestMeasurement(a)?.data.cost_usd?.median ?? Infinity)
          - (bestMeasurement(b)?.data.cost_usd?.median ?? Infinity),
      );
    }
    return filtered;
  }, [category, sort]);

  return (
    <div className="rst-catalog">
      <div className="rst-catalog-head">
        <p className="rst-catalog-blurb">
          Copy a ready-made instruction, fill the placeholders, and hand it to the
          agent. Every template is benchmarked: real runs, measured delivery time,
          tokens, cost, and an adversarial quality review.
        </p>
        <div className="rst-catalog-controls">
          <div className="rst-chips" role="group" aria-label="Category filter">
            <button
              type="button"
              className={category === "all" ? "is-active" : ""}
              onClick={() => setCategory("all")}
            >
              All
            </button>
            {categories.map((c) => (
              <button
                key={c}
                type="button"
                className={category === c ? "is-active" : ""}
                onClick={() => setCategory(c)}
              >
                {CATEGORY_LABELS[c] ?? c}
              </button>
            ))}
          </div>
          <select
            aria-label="Sort templates"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
          >
            <option value="tier">By tier (light to heavy)</option>
            <option value="fastest">Fastest first</option>
            <option value="cheapest">Cheapest first</option>
          </select>
        </div>
      </div>

      <div className="rst-tcard-grid">
        {visible.map((t) => (
          <button
            key={t.id}
            type="button"
            className="rst-tcard"
            onClick={() => onOpen(t.id)}
          >
            <div className="rst-tcard-top">
              <span className={`rst-tier rst-tier--${t.tier}`}>{TIER_LABELS[t.tier]}</span>
              {t.personas.length > 0 && (
                <span className="rst-tcard-personas" title={t.personas.join(", ")}>
                  {t.personas.length === 1 ? t.personas[0] : `${t.personas.length} personas`}
                </span>
              )}
            </div>
            <h3>{t.label}</h3>
            <p>{t.purpose}</p>
            <CardMetrics template={t} />
          </button>
        ))}
      </div>
    </div>
  );
}
