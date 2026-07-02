import { useMemo, useRef } from "react";
import { FilterChips, type ChipOption } from "../shared/FilterChips";
import { SegmentedControl } from "../shared/SegmentedControl";
import { WarningBanner } from "../shared/WarningBanner";
import { TierBadge, TypeIcon, TypeBadge, Tags, StatusDot, SkeletonRows } from "./components";
import type {
  CatalogFilters,
  CatalogHit,
  CatalogOverview,
  CatalogSearchResult,
  EntityType,
} from "./types";

interface Props {
  overview: CatalogOverview | null;
  result: CatalogSearchResult | null;
  query: string;
  busy: boolean;
  filters: CatalogFilters;
  onFiltersChange: (next: CatalogFilters) => void;
  onOpenEntity: (name: string, type: EntityType) => void;
  onPickModule: (module: string) => void;
  onPickType: (type: EntityType) => void;
  onGotoSection: (section: "governance" | "observability") => void;
  onClearAll: () => void;
  onLoadMore: () => void;
}

const TIER_SEGMENTS = [
  { value: "all", label: "All" },
  { value: "approved", label: "Approved" },
  { value: "candidate", label: "Candidate" },
  { value: "docs_only", label: "Docs" },
] as const;

function facetOptions<T extends string>(facet: Record<string, number>, max = 100): ChipOption<T>[] {
  return Object.entries(facet)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, max)
    .map(([value, count]) => ({ value: value as T, label: value, count }));
}

// A domain with no approved models is UNCURATED, not unhealthy — so it's a
// neutral muted dot, never an alarming amber. Green = has approved assets.
function domainHealthTone(d: { approved: number; total: number }): "ok" | "muted" {
  return d.approved > 0 ? "ok" : "muted";
}

function pctTone(pct: number): "ok" | "warn" | "bad" {
  if (pct >= 80) return "ok";
  if (pct >= 60) return "warn";
  return "bad";
}

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function Highlight({ text, query }: { text: string; query: string }) {
  const tokens = query.trim().toLowerCase().split(/\s+/).filter((t) => t.length >= 2);
  if (!tokens.length) return <>{text}</>;
  const re = new RegExp(`(${tokens.map(escapeRe).join("|")})`, "gi");
  const parts = text.split(re);
  return (
    <>
      {parts.map((p, i) =>
        tokens.includes(p.toLowerCase()) ? <mark key={i} className="dc-mark">{p}</mark> : p,
      )}
    </>
  );
}

function ResultCard({ hit, query, onOpen }: { hit: CatalogHit; query: string; onOpen: () => void }) {
  return (
    <button className={`dc-card dc-card--${hit.type}`} type="button" onClick={onOpen}>
      <TypeIcon type={hit.type} />
      <span className="dc-card-body">
        <span className="dc-card-title-row">
          <span className="dc-card-title"><Highlight text={hit.title} query={query} /></span>
          <TypeBadge type={hit.type} />
          {hit.tier && <TierBadge tier={hit.tier} />}
        </span>
        {hit.fqn && <span className="dc-card-fqn">{hit.fqn}</span>}
        {hit.description && <span className="dc-card-desc">{hit.description}</span>}
        {hit.tags.length > 0 && <Tags tags={hit.tags} />}
      </span>
      <span className="dc-card-meta">
        {hit.module && <span className="dc-badge">{hit.module}</span>}
        {hit.owner && <span className="dc-card-fqn">{hit.owner}</span>}
      </span>
    </button>
  );
}

function Home({
  overview,
  onPickModule,
  onPickType,
  onOpenEntity,
  onGotoSection,
}: {
  overview: CatalogOverview | null;
  onPickModule: (m: string) => void;
  onPickType: (t: EntityType) => void;
  onOpenEntity: (name: string, type: EntityType) => void;
  onGotoSection: (section: "governance" | "observability") => void;
}) {
  const domainsRef = useRef<HTMLDivElement>(null);
  const stats = overview?.stats;
  const total = stats ? stats.models + stats.metrics + stats.glossary : 0;
  const cards = stats
    ? [
        { label: "Models", value: stats.models.toLocaleString(), onClick: () => onPickType("model") },
        { label: "Metrics", value: stats.metrics.toLocaleString(), onClick: () => onPickType("metric") },
        { label: "Glossary", value: stats.glossary.toLocaleString(), onClick: () => onPickType("glossary") },
        { label: "Domains", value: stats.domains.toLocaleString(), onClick: () => domainsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }) },
        { label: "Owned", value: `${stats.owned_pct}%`, tone: pctTone(stats.owned_pct), onClick: () => onGotoSection("governance") },
        { label: "Documented", value: `${stats.doc_coverage_pct}%`, tone: pctTone(stats.doc_coverage_pct), onClick: () => onGotoSection("governance") },
      ]
    : [];
  const entryPoints = overview?.entry_points ?? [];
  const topMetrics = overview?.top_metrics ?? [];
  const glossary = overview?.glossary_terms ?? [];

  return (
    <div>
      <div className="dc-hero">
        <h2 className="dc-hero-title">
          {total > 0 ? `Search ${total.toLocaleString()} data assets` : "Search the data platform"}
        </h2>
        <p className="dc-hero-sub">Use the search bar above, browse by domain, or jump into a curated data product.</p>
      </div>

      {cards.length > 0 && (
        <div className="dc-stat-grid" style={{ marginBottom: 22 }}>
          {cards.map((c) => {
            const toneCls =
              "tone" in c && c.tone ? ` is-${c.tone === "bad" ? "bad" : c.tone === "warn" ? "warn" : "ok"}` : "";
            const inner = (
              <>
                <div className="dc-stat-label">{c.label}</div>
                <div className={`dc-stat-value${toneCls}`}>{c.value}</div>
              </>
            );
            return c.onClick ? (
              <button className="dc-stat dc-stat--action" type="button" key={c.label} onClick={c.onClick}>
                {inner}
              </button>
            ) : (
              <div className="dc-stat" key={c.label}>{inner}</div>
            );
          })}
        </div>
      )}

      <div className="dc-section-title dc-section-title--legend" ref={domainsRef} style={{ scrollMarginTop: 64 }}>
        <span>Browse by domain</span>
        <span className="dc-legend" aria-hidden>
          <span className="dc-legend-item"><span className="dc-legend-dot" style={{ background: "var(--success)" }} />approved</span>
          <span className="dc-legend-item"><span className="dc-legend-dot" style={{ background: "var(--warning)" }} />candidate</span>
          <span className="dc-legend-item"><span className="dc-legend-dot" style={{ background: "var(--text-muted)" }} />docs</span>
        </span>
      </div>
      <div className="dc-domain-grid" style={{ marginBottom: 24 }}>
        {(overview?.domains ?? []).map((d) => (
          <button className="dc-domain" type="button" key={d.module} onClick={() => onPickModule(d.module)}>
            <span className="dc-domain-head">
              <span className="dc-domain-name">
                <StatusDot tone={domainHealthTone(d)} /> {d.module}
              </span>
              <span className="dc-domain-count">{d.total}</span>
            </span>
            <span className="dc-tierbar">
              <span style={{ flex: Math.max(d.approved, 0.001) }} title={`approved: ${d.approved}`} />
              <span style={{ flex: Math.max(d.candidate, 0.001) }} title={`candidate: ${d.candidate}`} />
              <span style={{ flex: Math.max(d.docs_only, 0.001) }} title={`docs only: ${d.docs_only}`} />
            </span>
            <span className="dc-domain-legend">{d.approved} approved · {d.candidate} candidate · {d.docs_only} docs</span>
          </button>
        ))}
        {!overview && <div className="dc-empty">Loading overview…</div>}
      </div>

      <div className="dc-home-split">
        {entryPoints.length > 0 && (
          <div>
            <div className="dc-section-title">Key data products</div>
            <div className="dc-list">
              {entryPoints.map((m) => (
                <button className="dc-row" type="button" key={m.name} onClick={() => onOpenEntity(m.name, "model")}>
                  <span aria-hidden style={{ color: "var(--accent-text)" }}>▦</span>
                  <span className="dc-row-name" title={m.name}>{m.name}</span>
                  {m.tier && <TierBadge tier={m.tier} />}
                  <span className="dc-results-count" title="downstream models">↓ {m.downstream_count}</span>
                  <i className="dc-row-chevron" aria-hidden>›</i>
                </button>
              ))}
            </div>
          </div>
        )}

        {topMetrics.length > 0 && (
          <div>
            <div className="dc-section-title">Top metrics</div>
            <div className="dc-list">
              {topMetrics.map((m) => (
                <button className="dc-row" type="button" key={m.name} onClick={() => onOpenEntity(m.name, "metric")}>
                  <span aria-hidden style={{ color: "var(--success)" }}>∑</span>
                  <span className="dc-row-name" title={m.label || m.name}>{m.label || m.name}</span>
                  {m.tier && <TierBadge tier={m.tier} />}
                  <i className="dc-row-chevron" aria-hidden>›</i>
                </button>
              ))}
              <button className="dc-link-btn" type="button" style={{ alignSelf: "flex-start", marginTop: 4 }} onClick={() => onPickType("metric")}>
                Browse all metrics →
              </button>
            </div>
          </div>
        )}
      </div>

      {glossary.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div className="dc-section-title">Business glossary</div>
          <div className="dc-tags">
            {glossary.map((g) => (
              <button className="dc-tag dc-tag--link" type="button" key={g.name} title={g.module ? `${g.name} · ${g.module}` : g.name} onClick={() => onOpenEntity(g.name, "glossary")}>
                {g.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function ExploreSection({
  overview,
  result,
  query,
  busy,
  filters,
  onFiltersChange,
  onOpenEntity,
  onPickModule,
  onPickType,
  onGotoSection,
  onClearAll,
  onLoadMore,
}: Props) {
  const facets = result?.facets;
  const typeOptions = useMemo<ChipOption<EntityType>[]>(() => facetOptions<EntityType>(facets?.type ?? {}), [facets]);
  const moduleOptions = useMemo<ChipOption<string>[]>(() => facetOptions(facets?.module ?? {}, 30), [facets]);
  const ownerOptions = useMemo<ChipOption<string>[]>(() => facetOptions(facets?.owner ?? {}, 20), [facets]);
  const tagOptions = useMemo<ChipOption<string>[]>(() => facetOptions(facets?.tags ?? {}, 18), [facets]);
  // Wire live tier facet counts into the segmented control labels.
  const tierFacet = facets?.tier ?? {};
  const tierSegments = useMemo(
    () => TIER_SEGMENTS.map((s) =>
      s.value === "all" ? { value: s.value, label: s.label }
        : { value: s.value, label: tierFacet[s.value] != null ? `${s.label} (${tierFacet[s.value]})` : s.label },
    ),
    [tierFacet],
  );

  const anyFilter =
    !!filters.module ||
    !!filters.owner ||
    filters.tags.length > 0 ||
    filters.entityTypes.length > 0 ||
    (!!filters.tier && filters.tier !== "all");
  const searching = query.trim().length >= 2 || anyFilter;
  const hits = result?.hits ?? [];

  return (
    <div className="dc-root">

      {!searching ? (
        <Home
          overview={overview}
          onPickModule={onPickModule}
          onPickType={onPickType}
          onOpenEntity={onOpenEntity}
          onGotoSection={onGotoSection}
        />
      ) : (
        <div className="dc-layout">
          <aside className="dc-sidebar">
            <div className="dc-facet">
              <span className="dc-facet-title">Quality tier</span>
              <SegmentedControl
                options={tierSegments}
                value={filters.tier}
                onChange={(tier) => onFiltersChange({ ...filters, tier })}
                ariaLabel="Filter by quality tier"
                size="sm"
              />
            </div>
            {typeOptions.length > 0 && (
              <div className="dc-facet">
                <span className="dc-facet-title">Entity type</span>
                <FilterChips<EntityType>
                  options={typeOptions}
                  selected={filters.entityTypes}
                  onChange={(entityTypes) => onFiltersChange({ ...filters, entityTypes })}
                  allowAllToggle
                />
              </div>
            )}
            {moduleOptions.length > 0 && (
              <div className="dc-facet">
                <span className="dc-facet-title">Module (one)</span>
                <FilterChips<string>
                  options={moduleOptions}
                  selected={filters.module ? [filters.module] : []}
                  onChange={(next) => onFiltersChange({ ...filters, module: next.length ? next[next.length - 1] : "" })}
                  allowAllToggle={false}
                />
              </div>
            )}
            {ownerOptions.length > 0 && (
              <div className="dc-facet">
                <span className="dc-facet-title">Owner (one)</span>
                <FilterChips<string>
                  options={ownerOptions}
                  selected={filters.owner ? [filters.owner] : []}
                  onChange={(next) => onFiltersChange({ ...filters, owner: next.length ? next[next.length - 1] : "" })}
                  allowAllToggle={false}
                />
              </div>
            )}
            {tagOptions.length > 0 && (
              <div className="dc-facet">
                <span className="dc-facet-title">Tags (match all)</span>
                <FilterChips<string>
                  options={tagOptions}
                  selected={filters.tags}
                  onChange={(tags) => onFiltersChange({ ...filters, tags })}
                  allowAllToggle={false}
                />
              </div>
            )}
          </aside>

          <main>
            <div className="dc-results-head">
              <span className="dc-results-count">
                {result
                  ? `Showing ${hits.length.toLocaleString()} of ${result.total.toLocaleString()}` +
                    (query.trim() ? ` for “${query.trim()}”` : filters.module ? ` in ${filters.module}` : "")
                  : busy
                    ? "Searching…"
                    : ""}
              </span>
              <button className="dc-link-btn" type="button" onClick={onClearAll}>← Overview</button>
            </div>
            {anyFilter && (
              <div className="dc-applied-filters">
                {filters.module && (
                  <button className="dc-filter-chip" type="button" onClick={() => onFiltersChange({ ...filters, module: "" })}>module: {filters.module} ✕</button>
                )}
                {filters.owner && (
                  <button className="dc-filter-chip" type="button" onClick={() => onFiltersChange({ ...filters, owner: "" })}>owner: {filters.owner} ✕</button>
                )}
                {filters.tier !== "all" && (
                  <button className="dc-filter-chip" type="button" onClick={() => onFiltersChange({ ...filters, tier: "all" })}>tier: {filters.tier} ✕</button>
                )}
                {filters.entityTypes.map((t) => (
                  <button key={t} className="dc-filter-chip" type="button" onClick={() => onFiltersChange({ ...filters, entityTypes: filters.entityTypes.filter((x) => x !== t) })}>{t} ✕</button>
                ))}
                {filters.tags.map((t) => (
                  <button key={t} className="dc-filter-chip" type="button" onClick={() => onFiltersChange({ ...filters, tags: filters.tags.filter((x) => x !== t) })}>{t} ✕</button>
                ))}
              </div>
            )}
            {result && result.warnings && result.warnings.length > 0 && (
              <WarningBanner warnings={result.warnings} />
            )}
            {hits.length === 0 ? (
              busy ? (
                <SkeletonRows count={6} />
              ) : (
                <div className="dc-empty">
                  <div>No entities match{query.trim() ? ` “${query.trim()}”` : " the current filters"}.</div>
                  {result?.suggestions && result.suggestions.length > 0 && (
                    <div className="dc-empty-block">
                      <div className="dc-empty-label">Did you mean</div>
                      <div className="dc-chip-row">
                        {result.suggestions.map((s) => (
                          <button className="dc-chip" type="button" key={s.name} onClick={() => onOpenEntity(s.name, s.type)} title={`Open ${s.name}`}>
                            <TypeIcon type={s.type} /> {s.title || s.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {(overview?.entry_points ?? []).length > 0 && (
                    <div className="dc-empty-block">
                      <div className="dc-empty-label">Popular data products</div>
                      <div className="dc-chip-row">
                        {(overview?.entry_points ?? []).slice(0, 6).map((m) => (
                          <button className="dc-chip" type="button" key={m.name} onClick={() => onOpenEntity(m.name, "model")} title={`Open ${m.name}`}>
                            <span aria-hidden style={{ color: "var(--accent-text)" }}>▦</span> {m.name}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  <div style={{ marginTop: 16 }}>
                    <button className="dc-link-btn" type="button" onClick={onClearAll}>← Clear all filters and return to overview</button>
                  </div>
                </div>
              )
            ) : (
              <>
                <div className="dc-results">
                  {hits.map((hit) => (
                    <ResultCard key={hit.id} hit={hit} query={query} onOpen={() => onOpenEntity(hit.name, hit.type)} />
                  ))}
                </div>
                {result && result.total > hits.length && (
                  <div style={{ display: "flex", justifyContent: "center", marginTop: 14 }}>
                    <button className="dc-loadmore" type="button" onClick={onLoadMore} disabled={busy}>
                      {busy ? "Loading…" : `Load more (${(result.total - hits.length).toLocaleString()} more)`}
                    </button>
                  </div>
                )}
              </>
            )}
          </main>
        </div>
      )}
    </div>
  );
}
