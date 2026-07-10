// Browse view: a sector-grid landing when idle, and a facet sidebar +
// result cards + load-more once the user searches or filters. Server-backed
// paging via search_metric_catalog when the embedded catalog page is
// truncated (~200 of ~2,100 entries).

import { useEffect, useMemo, useState } from "react";
import { FilterChips } from "../shared/FilterChips";
import { SegmentedControl } from "../shared/SegmentedControl";
import {
  computeFacets,
  entryIsTimeseries,
  filterCatalog,
  Highlight,
  needsServerSearch,
} from "./catalogSearch";
import type {
  CatalogFacets,
  CatalogSearchResponse,
  MetricCatalogEntry,
} from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

const PAGE_SIZE = 30;

interface BrowseSectionProps {
  catalog: MetricCatalogEntry[];
  catalogTotal: number | undefined;
  catalogFacets: CatalogFacets | undefined;
  query: string;
  sector: string;
  layer: string;
  tag: string;
  timeseriesOnly: boolean;
  onFilterChange: (patch: {
    sector?: string;
    layer?: string;
    tag?: string;
    timeseries?: boolean;
  }) => void;
  callTool: CallTool;
  basket: string[];
  /** Full entry objects — server-search results are NOT in the embedded
   * catalog page, so passing bare names would lose dimensions/metadata
   * (the silent scalar-load bug). */
  onAddToBasket: (entry: MetricCatalogEntry) => void;
  onOpenDetail: (name: string) => void;
  onLoadSolo: (entry: MetricCatalogEntry) => void;
}

export function BrowseSection({
  catalog,
  catalogTotal,
  catalogFacets,
  query,
  sector,
  layer,
  tag,
  timeseriesOnly,
  onFilterChange,
  callTool,
  basket,
  onAddToBasket,
  onOpenDetail,
  onLoadSolo,
}: BrowseSectionProps) {
  const [serverResult, setServerResult] = useState<CatalogSearchResponse | null>(null);
  const [serverBusy, setServerBusy] = useState(false);
  const [shown, setShown] = useState(PAGE_SIZE);

  const filtersActive = Boolean(query.trim() || sector || layer || tag);
  const serverNeeded = needsServerSearch(catalog.length, catalogTotal);

  // Server-backed search whenever filters are active AND the embedded page
  // can't answer authoritatively. Falls back to client filtering on error.
  useEffect(() => {
    setShown(PAGE_SIZE);
    if (!filtersActive || !serverNeeded) {
      setServerResult(null);
      return;
    }
    let cancelled = false;
    setServerBusy(true);
    const timer = setTimeout(() => {
      callTool<CatalogSearchResponse>("search_metric_catalog", {
        query,
        sector,
        layer,
        tag,
        timeseries: timeseriesOnly,
        limit: 200,
      })
        .then((res) => {
          if (!cancelled && res && Array.isArray(res.entries)) setServerResult(res);
        })
        .catch(() => {
          if (!cancelled) setServerResult(null); // degrade to client-side
        })
        .finally(() => {
          if (!cancelled) setServerBusy(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query, sector, layer, tag, timeseriesOnly, filtersActive, serverNeeded, callTool]);

  const clientFiltered = useMemo(
    () =>
      filterCatalog(catalog, { query, sector, layer, tag, timeseries: timeseriesOnly }),
    [catalog, query, sector, layer, tag, timeseriesOnly],
  );

  const entries = serverResult?.entries ?? clientFiltered;
  const totalMatching = serverResult?.total_matching ?? clientFiltered.length;
  // Unconditional hook call (rules of hooks) — cheap over ≤500 entries.
  const clientFacets = useMemo(() => computeFacets(catalog, query), [catalog, query]);
  const facets: CatalogFacets = serverResult?.facets ?? catalogFacets ?? clientFacets;

  const sectors = Object.entries(facets.sector ?? {}).sort((a, b) => b[1] - a[1]);
  const layers = facets.layer ?? {};
  const tagOptions = Object.entries(facets.tag ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 16)
    .map(([name, count]) => ({ value: name, label: name, count }));
  // Keep the active tag selectable even when it drops out of the top list.
  if (tag && !tagOptions.some((t) => t.value === tag)) {
    tagOptions.unshift({ value: tag, label: tag, count: totalMatching });
  }

  // ---- Landing: sector grid ----
  if (!filtersActive) {
    const total = catalogTotal ?? catalog.length;
    return (
      <div className="mlab-browse">
        <div className="mlab-stats-row">
          <StatCard label="Models" value={total.toLocaleString()} />
          <StatCard label="api" value={(layers.api ?? 0).toLocaleString()} />
          <StatCard label="fct" value={(layers.fct ?? 0).toLocaleString()} />
          <StatCard label="int" value={(layers.int ?? 0).toLocaleString()} />
          <StatCard
            label="stg + source"
            value={((layers.stg ?? 0) + (layers.source ?? 0)).toLocaleString()}
          />
          <StatCard label="Sectors" value={String(sectors.length)} />
        </div>
        <h3 className="mlab-subhead">Browse by sector</h3>
        <div className="mlab-sector-grid">
          {sectors.map(([name, count]) => (
            <button
              key={name}
              type="button"
              className="mlab-sector-card"
              onClick={() => onFilterChange({ sector: name })}
            >
              <span className="mlab-sector-name">{name}</span>
              <span className="mlab-sector-count">{count.toLocaleString()} models</span>
            </button>
          ))}
        </div>
        <p className="mlab-hint">
          Every dbt model and source under its exact database name. Search above, pick a
          sector, or filter by layer and tag — then load a model and chart its columns.
        </p>
      </div>
    );
  }

  // ---- Results: facet sidebar + cards ----
  return (
    <div className="mlab-results">
      <aside className="mlab-facets">
        <h4>Scope</h4>
        <SegmentedControl<string>
          ariaLabel="Time series scope"
          size="sm"
          value={timeseriesOnly ? "ts" : "all"}
          onChange={(v) => onFilterChange({ timeseries: v === "ts" })}
          options={[
            { value: "ts", label: "Time series" },
            { value: "all", label: "Everything" },
          ]}
        />
        <h4>Layer</h4>
        <FilterChips
          options={["api", "fct", "int", "stg", "source"]
            .filter((l) => (layers[l] ?? 0) > 0 || l === layer)
            .map((l) => ({ value: l, label: l, count: layers[l] ?? 0 }))}
          selected={layer ? [layer] : []}
          onChange={(next) => onFilterChange({ layer: next[next.length - 1] ?? "" })}
          allowAllToggle={false}
        />
        <h4>Sector</h4>
        <FilterChips
          options={sectors.map(([name, count]) => ({ value: name, label: name, count }))}
          selected={sector ? [sector] : []}
          onChange={(next) => onFilterChange({ sector: next[next.length - 1] ?? "" })}
          allowAllToggle={false}
        />
        {tagOptions.length > 0 && (
          <>
            <h4>Tag</h4>
            <FilterChips
              options={tagOptions}
              selected={tag ? [tag] : []}
              onChange={(next) => onFilterChange({ tag: next[next.length - 1] ?? "" })}
              allowAllToggle={false}
            />
          </>
        )}
      </aside>

      <div className="mlab-result-list">
        <div className="mlab-result-count">
          {serverBusy
            ? "Searching…"
            : `${totalMatching.toLocaleString()} matching entr${totalMatching === 1 ? "y" : "ies"}`}
        </div>
        {entries.slice(0, shown).map((e) => (
          <ResultCard
            key={e.name}
            entry={e}
            query={query}
            inBasket={basket.includes(e.name)}
            onAdd={() => onAddToBasket(e)}
            onDetail={() => onOpenDetail(e.name)}
            onLoad={() => onLoadSolo(e)}
            onTagClick={(t) => onFilterChange({ tag: t })}
          />
        ))}
        {entries.length === 0 && !serverBusy && (
          <div className="mlab-empty">
            No entries match. Try fewer words, or clear the sector/tier filters.
          </div>
        )}
        {shown < entries.length && (
          <button
            type="button"
            className="mlab-loadmore"
            onClick={() => setShown((s) => s + PAGE_SIZE)}
          >
            Show more ({entries.length - shown} remaining)
          </button>
        )}
      </div>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="mlab-stat">
      <div className="mlab-stat-value">{value}</div>
      <div className="mlab-stat-label">{label}</div>
    </div>
  );
}

interface ResultCardProps {
  entry: MetricCatalogEntry;
  query: string;
  inBasket: boolean;
  onAdd: () => void;
  onDetail: () => void;
  onLoad: () => void;
  onTagClick: (tag: string) => void;
}

function ResultCard({ entry, query, inBasket, onAdd, onDetail, onLoad, onTagClick }: ResultCardProps) {
  const isTs = entryIsTimeseries(entry);
  const tags = entry.tags ?? [];
  return (
    <article className="mlab-card">
      <header className="mlab-card-head">
        {entry.layer && (
          <span
            className={`mlab-layer mlab-layer--${entry.layer}`}
            title={entry.materialized ? `dbt ${entry.layer} · ${entry.materialized}` : `dbt ${entry.layer}`}
          >
            {entry.layer}
          </span>
        )}
        <button
          type="button"
          className="mlab-card-title mlab-card-title--mono"
          onClick={onDetail}
          title="Open details"
        >
          <Highlight text={entry.name} query={query} />
        </button>
        {isTs ? (
          <span className="mlab-ts" title="Has a date-like column — loads as a time series">
            time series
          </span>
        ) : (
          <span className="mlab-ts mlab-ts--scalar" title="No date-like column — snapshot/aggregate shape">
            snapshot
          </span>
        )}
        {entry.materialized && <span className="mlab-unit">{entry.materialized}</span>}
      </header>
      <div className="mlab-card-path">
        {[entry.sector, entry.subsector].filter(Boolean).join(" › ")}
      </div>
      {entry.description && (
        <p className="mlab-card-desc">
          <Highlight text={entry.description} query={query} />
        </p>
      )}
      {(entry.matched_columns?.length ?? 0) > 0 && (
        <div className="mlab-card-cols" title="Columns matching your search">
          matched columns:{" "}
          {entry.matched_columns!.slice(0, 5).map((c, i) => (
            <span key={c.name}>
              {i > 0 && ", "}
              <code>{c.name}</code>
            </span>
          ))}
        </div>
      )}
      {tags.length > 0 && (
        <div className="mlab-card-tags">
          {tags.slice(0, 8).map((t) => (
            <button
              key={t}
              type="button"
              className="mlab-tag"
              title={`Filter by tag: ${t}`}
              onClick={() => onTagClick(t)}
            >
              {t}
            </button>
          ))}
          {tags.length > 8 && <span className="mlab-dim">+{tags.length - 8}</span>}
        </div>
      )}
      <footer className="mlab-card-actions">
        <button type="button" className="mlab-toggle" onClick={onDetail}>
          Details
        </button>
        <button
          type="button"
          className="mlab-toggle"
          disabled={inBasket}
          onClick={onAdd}
        >
          {inBasket ? "Added" : "+ Compare"}
        </button>
        <button
          type="button"
          className="mlab-toggle is-primary"
          onClick={onLoad}
        >
          Load
        </button>
      </footer>
    </article>
  );
}
