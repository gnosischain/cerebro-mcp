import { useMemo, useState } from "react";
import type { CatalogEntry } from "./types";

interface Props {
  catalog: CatalogEntry[];
  onSeed: (modelName: string) => void;
}

const MAX_ROWS = 300;

/**
 * Start / browse screen for the Model Lineage Explorer.
 *
 * The graph needs a seed model, but users rarely know the exact name of one of
 * ~1000 models. This screen lets them search by name, schema, tag, or
 * description and click any model to open its lineage — no prior knowledge of
 * the catalog required. A free-text box still accepts an exact name.
 */
export function CatalogScreen({ catalog, onSeed }: Props) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const base = needle
      ? catalog.filter((m) => {
          const hay = [m.name, m.schema, m.description, ...(m.tags ?? [])]
            .join(" ")
            .toLowerCase();
          return hay.includes(needle);
        })
      : catalog;
    return base;
  }, [catalog, query]);

  const shown = filtered.slice(0, MAX_ROWS);

  const submitExact = () => {
    const trimmed = query.trim();
    if (trimmed) onSeed(trimmed);
  };

  return (
    <section className="ml-catalog">
      <header className="ml-catalog-head">
        <h2>Model Lineage Explorer</h2>
        <span className="ml-catalog-count">
          {filtered.length} of {catalog.length} models
        </span>
      </header>

      <div className="ml-catalog-search">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitExact();
          }}
          placeholder="Search models by name, schema, tag, or description…"
          autoFocus
        />
        <p className="ml-catalog-hint">
          {catalog.length
            ? "Click a model to open its lineage graph. Press Enter to use the text as an exact model name."
            : "Catalog is loading — or open with a seed model from the tool call."}
        </p>
      </div>

      <ul className="ml-catalog-list">
        {shown.map((m) => (
          <li key={m.name}>
            <button
              type="button"
              className="ml-catalog-row"
              onClick={() => onSeed(m.name)}
              title={m.description || m.name}
            >
              {m.materialized ? (
                <span
                  className={`ml-mat-badge mat-${m.materialized}`}
                >
                  {m.materialized}
                </span>
              ) : (
                <span className="ml-mat-badge">model</span>
              )}
              <span className="ml-catalog-name">{m.name}</span>
              {m.schema ? (
                <span className="ml-catalog-schema">{m.schema}</span>
              ) : null}
              <span className="ml-catalog-tags">
                {(m.tags ?? []).slice(0, 3).map((t) => (
                  <span key={t} className="ml-tag">
                    {t}
                  </span>
                ))}
              </span>
              <span className="ml-catalog-go" aria-hidden>
                →
              </span>
            </button>
          </li>
        ))}
        {!filtered.length ? (
          <li className="ml-catalog-empty">
            No models match "{query}". Clear the search to see all models.
          </li>
        ) : null}
        {filtered.length > MAX_ROWS ? (
          <li className="ml-catalog-more">
            Showing first {MAX_ROWS} of {filtered.length} — refine your search to
            narrow the list.
          </li>
        ) : null}
      </ul>
    </section>
  );
}
