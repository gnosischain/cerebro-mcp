// Detail drawer for one catalog entry — fetches the full record (untruncated
// description, synonyms, time grains, root-model columns) via the app-only
// get_metric_catalog_entry tool.

import { useEffect, useState } from "react";
import type { CatalogEntryDetail, MetricCatalogEntry } from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

interface MetricDetailPanelProps {
  name: string;
  /** Embedded list entry — instant fallback while the detail loads. */
  fallback: MetricCatalogEntry | null;
  callTool: CallTool;
  inBasket: boolean;
  /** Full entry objects — keeps dimensions/metadata for entries that are
   * not in the embedded catalog page (server-search / detail-only). */
  onAddToBasket: (entry: MetricCatalogEntry) => void;
  onLoadSolo: (entry: MetricCatalogEntry) => void;
  onClose: () => void;
}

export function MetricDetailPanel({
  name,
  fallback,
  callTool,
  inBasket,
  onAddToBasket,
  onLoadSolo,
  onClose,
}: MetricDetailPanelProps) {
  const [detail, setDetail] = useState<CatalogEntryDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setError("");
    callTool<CatalogEntryDetail>("get_metric_catalog_entry", { name })
      .then((res) => {
        if (!cancelled && res && res.name) setDetail(res);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load detail");
      });
    return () => {
      cancelled = true;
    };
  }, [name, callTool]);

  const entry: CatalogEntryDetail | MetricCatalogEntry | null = detail ?? fallback;
  if (!entry) return null;

  const grains = entry.supported_time_grains ?? [];
  const synonyms = (entry as CatalogEntryDetail).question_synonyms ?? [];
  const cols = entry.columns ?? [];

  return (
    <aside className="mlab-detail" role="dialog" aria-label={`Details for ${entry.name}`}>
      <div className="mlab-detail-head">
        <div>
          {entry.layer && (
            <span className={`mlab-layer mlab-layer--${entry.layer}`}>{entry.layer}</span>
          )}
          {entry.materialized && <span className="mlab-unit">{entry.materialized}</span>}
        </div>
        <button type="button" className="mlab-close" onClick={onClose} aria-label="Close details">
          ×
        </button>
      </div>

      {/* Exact DB/dbt name is the identity — the human label is secondary. */}
      <h2 className="mlab-detail-title mlab-detail-title--mono">{entry.name}</h2>
      {entry.label && entry.label !== entry.name && (
        <div className="mlab-detail-label">{entry.label}</div>
      )}
      {(entry as CatalogEntryDetail).relation_name && (
        <code className="mlab-detail-name">
          {(entry as CatalogEntryDetail).relation_name}
        </code>
      )}

      <p className="mlab-detail-desc">
        {entry.description || <em>No description recorded.</em>}
        {!detail && !error && <span className="mlab-dim"> (loading full detail…)</span>}
      </p>
      {error && <p className="mlab-error">{error}</p>}

      <dl className="mlab-detail-meta">
        <div>
          <dt>Sector</dt>
          <dd>{entry.sector || entry.module || "—"}</dd>
        </div>
        {entry.unit && (
          <div>
            <dt>Unit</dt>
            <dd>{entry.unit}</dd>
          </div>
        )}
        {(entry as CatalogEntryDetail).measure && (
          <div>
            <dt>Measure</dt>
            <dd>
              <code>{(entry as CatalogEntryDetail).measure}</code>
            </dd>
          </div>
        )}
        {grains.length > 0 && (
          <div>
            <dt>Time grains</dt>
            <dd>{grains.join(", ")}</dd>
          </div>
        )}
      </dl>

      {(entry.tags ?? []).length > 0 && (
        <div className="mlab-detail-block">
          <h4>Tags</h4>
          <div className="mlab-card-tags">
            {(entry.tags ?? []).map((t) => (
              <span key={t} className="mlab-tag mlab-tag--static">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {entry.allowed_dimensions.length > 0 && (
        <div className="mlab-detail-block">
          <h4>Dimensions</h4>
          <div className="mlab-dim-chips">
            {entry.allowed_dimensions.map((d) => (
              <span
                key={d}
                className={`mlab-dimchip${entry.default_dimensions.includes(d) ? " is-default" : ""}`}
                title={entry.default_dimensions.includes(d) ? "default dimension" : undefined}
              >
                {d}
              </span>
            ))}
          </div>
        </div>
      )}

      {synonyms.length > 0 && (
        <div className="mlab-detail-block">
          <h4>Also known as</h4>
          <p className="mlab-dim">{synonyms.join(" · ")}</p>
        </div>
      )}

      {cols.length > 0 && (
        <div className="mlab-detail-block">
          <h4>Columns</h4>
          <div className="mini-app-table-wrap mlab-detail-cols">
            <table>
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Type</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {cols.map((c) => (
                  <tr key={c.name}>
                    <td>
                      <code>{c.name}</code>
                    </td>
                    <td>{c.type}</td>
                    <td>{c.description || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="mlab-detail-actions">
        <button
          type="button"
          className="btn btn--secondary"
          disabled={inBasket}
          onClick={() => onAddToBasket(entry)}
        >
          {inBasket ? "In compare set" : "Add to compare"}
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => onLoadSolo(entry)}
        >
          Load
        </button>
      </div>
    </aside>
  );
}
