import { useCallback, useEffect, useRef, useState } from "react";
import { MiniAppChrome } from "../shared/MiniAppChrome";
import { MaSearchInput } from "../shared/MaSearchInput";
import { WarningBanner } from "../shared/WarningBanner";
import { useMiniApp } from "../shared/useMiniApp";
import { useDebouncedValue } from "../shared/useDebouncedValue";
import { SkeletonBlock } from "./components";
import { ExploreSection } from "./ExploreSection";
import { EntityProfile } from "./EntityProfile";
import { GovernanceSection } from "./GovernanceSection";
import { ObservabilitySection } from "./ObservabilitySection";
import type {
  CatalogEntity,
  CatalogFilters,
  CatalogGovernance,
  CatalogObservability,
  CatalogOverview,
  CatalogPayload,
  CatalogSearchResult,
  EntityType,
  PlatformTab,
} from "./types";

const APP_ID = "data_catalog";
const EMPTY_FILTERS: CatalogFilters = { entityTypes: [], module: "", tier: "all", tags: [], owner: "" };
const URL_KEYS = ["section", "q", "module", "tier", "tags", "entity", "etype", "tab"];
const DEFAULT_TAB = "schema"; // land on the registry-only Schema (no CH round-trip), not Data

const PLATFORM_OPTS = [
  { value: "explore", label: "Explore" },
  { value: "observability", label: "Observability" },
  { value: "governance", label: "Governance" },
] as const;

const SECTION_LABEL: Record<PlatformTab, string> = {
  explore: "Explore",
  observability: "Observability",
  governance: "Governance",
};

function filtersActive(query: string, f: CatalogFilters): boolean {
  return (
    query.trim().length > 0 ||
    !!f.module ||
    !!f.owner ||
    f.tags.length > 0 ||
    f.entityTypes.length > 0 ||
    (!!f.tier && f.tier !== "all")
  );
}

interface UrlState {
  section: PlatformTab;
  q: string;
  module: string;
  tier: string;
  tags: string[];
  entity: string;
  etype: EntityType;
  tab: string;
}

function readUrl(): UrlState {
  const p = new URLSearchParams(window.location.search);
  return {
    section: (p.get("section") as PlatformTab) || "explore",
    q: p.get("q") || "",
    module: p.get("module") || "",
    tier: p.get("tier") || "all",
    tags: (p.get("tags") || "").split(",").filter(Boolean),
    entity: p.get("entity") || "",
    etype: (p.get("etype") as EntityType) || "model",
    tab: p.get("tab") || DEFAULT_TAB,
  };
}

// Preserve unmanaged params (e.g. ?token=) while rewriting nav state.
function writeUrl(s: UrlState, push: boolean) {
  const p = new URLSearchParams(window.location.search);
  URL_KEYS.forEach((k) => p.delete(k));
  if (s.section && s.section !== "explore") p.set("section", s.section);
  // ALWAYS carry the search/filter context (even when an entity is open) so
  // Back/reload from an asset restores the prior result set instead of resetting
  // to the home — losing the result set on Back was the #1 navigation complaint.
  if (s.q.trim()) p.set("q", s.q.trim());
  if (s.module) p.set("module", s.module);
  if (s.tier && s.tier !== "all") p.set("tier", s.tier);
  if (s.tags.length) p.set("tags", s.tags.join(","));
  if (s.entity) {
    p.set("entity", s.entity);
    if (s.etype) p.set("etype", s.etype);
    if (s.tab && s.tab !== DEFAULT_TAB) p.set("tab", s.tab);
  }
  const qs = p.toString();
  const url = window.location.pathname + (qs ? "?" + qs : "") + window.location.hash;
  if (push) window.history.pushState({}, "", url);
  else window.history.replaceState({}, "", url);
}

export default function DataCatalogApp() {
  const { view, callTool: rawCallTool } = useMiniApp<Record<string, unknown>>({ appId: APP_ID });
  const payload = view as CatalogPayload | null;

  // useMiniApp recreates `callTool` every render — a stable ref wrapper keeps
  // downstream hooks from re-firing (and hammering the server) every render.
  const callToolRef = useRef(rawCallTool);
  callToolRef.current = rawCallTool;
  const callTool = useCallback(
    <T,>(name: string, args: Record<string, unknown>): Promise<T | null> =>
      callToolRef.current<T>(name, args),
    [],
  );

  const [platformTab, setPlatformTab] = useState<PlatformTab>("explore");
  const [mode, setMode] = useState<"browse" | "entity">("browse");
  const [entityTab, setEntityTab] = useState<string>(DEFAULT_TAB);
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const [filters, setFilters] = useState<CatalogFilters>(EMPTY_FILTERS);
  const [overview, setOverview] = useState<CatalogOverview | null>(null);
  const [governance, setGovernance] = useState<CatalogGovernance | null>(null);
  const [observability, setObservability] = useState<CatalogObservability | null>(null);
  const [result, setResult] = useState<CatalogSearchResult | null>(null);
  const [entity, setEntity] = useState<CatalogEntity | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [limit, setLimit] = useState(30);

  const seededRef = useRef(false);
  const triedOverviewRef = useRef(false);
  const applyingUrlRef = useRef(false);          // suppress URL writes while applying popstate
  const prevKeyRef = useRef("explore|");          // major-nav identity for push-vs-replace
  const curEntityRef = useRef<{ name: string; type: EntityType } | null>(null);

  // ---- entity loader (no direct URL writes — the sync effect handles URL) ----
  const loadEntity = useCallback(
    async (name: string, type: EntityType) => {
      // Do NOT force platformTab here — keep the section the user came from so the
      // left rail tells the truth (an entity opened from Observability stays under
      // Observability, and Back returns there). Entity mode takes render priority.
      curEntityRef.current = { name, type };
      setMode("entity");
      setEntity(null);
      setBusy(true);
      setError("");
      try {
        const res = await callTool<CatalogEntity>("get_catalog_entity", { name, entity_type: type });
        if (res) setEntity(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load entity");
      } finally {
        setBusy(false);
      }
    },
    [callTool],
  );

  // Open an entity. Each open is a pushState (sync effect below), so the browser
  // history IS the navigation stack — no parallel bespoke stack to desync.
  const openEntity = useCallback(
    (name: string, type: EntityType) => {
      setEntityTab(DEFAULT_TAB); // fresh in-app open lands on the default tab
      void loadEntity(name, type);
    },
    [loadEntity],
  );

  // Back = browser Back; the popstate handler rebuilds state from the URL. If
  // there is no in-app history to pop (deep-linked straight in), fall to browse.
  const goBack = useCallback(() => {
    if (window.history.length > 1) {
      window.history.back();
    } else {
      curEntityRef.current = null;
      setMode("browse");
      setEntity(null);
    }
  }, []);

  // Switching section always leaves entity mode AND resets the search/filters, so
  // clicking a rail item lands on that section's pristine home (clicking
  // "Explore" mid-search reliably returns to the Explore home).
  const selectSection = useCallback((s: PlatformTab) => {
    curEntityRef.current = null;
    setMode("browse");
    setEntity(null);
    setQuery("");
    setFilters(EMPTY_FILTERS);
    setResult(null);
    setPlatformTab(s);
  }, []);

  // Global search lives in the app shell (present on every section). Any input
  // routes the user into Explore results.
  const globalSearch = useCallback((q: string) => {
    setPlatformTab("explore");
    curEntityRef.current = null;
    setMode("browse");
    setLimit(30);
    setQuery(q);
  }, []);

  // Typing in the shell search only LIVE-searches when already in Explore browse
  // (the effect is gated to that). From Governance/Observability/an entity it
  // just holds the query — the section switch happens on submit — so a stray
  // keystroke no longer teleports the user out of where they are.
  const onSearchType = useCallback((q: string) => {
    setQuery(q);
    setLimit(30);
  }, []);

  // Drill into a filtered Explore view — the single entry point used by the
  // home stat cards, governance panels, and domain tiles.
  const applyExplore = useCallback((partial: Partial<CatalogFilters>) => {
    setPlatformTab("explore");
    curEntityRef.current = null;
    setMode("browse");
    setEntity(null);
    setLimit(30);
    setQuery("");
    setFilters({ ...EMPTY_FILTERS, ...partial });
  }, []);

  const pickModule = useCallback((m: string) => applyExplore({ module: m }), [applyExplore]);
  const pickType = useCallback((t: EntityType) => applyExplore({ entityTypes: [t] }), [applyExplore]);

  const clearAll = useCallback(() => {
    setLimit(30);
    setQuery("");
    setFilters(EMPTY_FILTERS);
    setResult(null);
  }, []);

  // ---- Seed from injected payload + URL (deep-link) once ----
  useEffect(() => {
    if (seededRef.current || !payload) return;
    seededRef.current = true;
    if (payload.overview) {
      setOverview(payload.overview);
      triedOverviewRef.current = true;
    }
    if (payload.governance) setGovernance(payload.governance);
    if (payload.observability) setObservability(payload.observability);

    const u = readUrl();
    setPlatformTab(u.section);
    if (u.entity) {
      curEntityRef.current = { name: u.entity, type: u.etype };
      setEntityTab(u.tab);
      // Restore the browse filter context behind the entity so leaving it returns
      // to the prior result set rather than the home.
      setQuery(u.q);
      setFilters({ entityTypes: [], module: u.module, tier: u.tier, tags: u.tags, owner: "" });
      void loadEntity(u.entity, u.etype);
    } else if (payload.view === "entity" && payload.entity) {
      curEntityRef.current = { name: payload.entity.name, type: payload.entity.type };
      setEntityTab(u.tab);
      setEntity(payload.entity);
      setMode("entity");
    } else {
      setQuery(u.q || payload.query || "");
      setFilters({ entityTypes: [], module: u.module, tier: u.tier, tags: u.tags, owner: "" });
    }
    prevKeyRef.current = `${u.section}|${u.entity || (payload.view === "entity" && payload.entity ? payload.entity.name : "")}`;
  }, [payload, loadEntity]);

  // ---- Overview fallback (if not injected) ----
  useEffect(() => {
    if (overview || triedOverviewRef.current) return;
    triedOverviewRef.current = true;
    let alive = true;
    callTool<CatalogOverview>("catalog_overview", {})
      .then((o) => alive && o && setOverview(o))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [callTool, overview]);

  // ---- URL sync: push on major nav (section/entity), replace on tweaks ----
  useEffect(() => {
    if (applyingUrlRef.current) return;
    const entName = mode === "entity" ? curEntityRef.current?.name || entity?.name || "" : "";
    const etype = curEntityRef.current?.type || "model";
    const s: UrlState = {
      section: platformTab, q: debouncedQuery, module: filters.module,
      tier: filters.tier, tags: filters.tags, entity: entName, etype, tab: entityTab,
    };
    // Tab is intentionally NOT part of the major key — switching sub-tabs is a
    // replaceState (no new history entry), entity/section changes push.
    const key = `${s.section}|${s.entity}`;
    const major = key !== prevKeyRef.current;
    prevKeyRef.current = key;
    writeUrl(s, major);
  }, [platformTab, debouncedQuery, filters, mode, entity, entityTab]);

  // ---- Back/Forward → apply URL to state ----
  useEffect(() => {
    const onPop = () => {
      applyingUrlRef.current = true;
      const u = readUrl();
      setPlatformTab(u.section);
      if (u.entity) {
        curEntityRef.current = { name: u.entity, type: u.etype };
        setEntityTab(u.tab);
        setQuery(u.q);
        setFilters({ entityTypes: [], module: u.module, tier: u.tier, tags: u.tags, owner: "" });
        void loadEntity(u.entity, u.etype);
      } else {
        curEntityRef.current = null;
        setMode("browse");
        setEntity(null);
        setQuery(u.q);
        setFilters({ entityTypes: [], module: u.module, tier: u.tier, tags: u.tags, owner: "" });
      }
      prevKeyRef.current = `${u.section}|${u.entity || ""}`;
      setTimeout(() => {
        applyingUrlRef.current = false;
      }, 0);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [loadEntity]);

  const runSearch = useCallback(
    async (q: string, f: CatalogFilters, lim: number) => {
      setBusy(true);
      setError("");
      try {
        const res = await callTool<CatalogSearchResult>("catalog_search", {
          query: q, entity_types: f.entityTypes, module: f.module, tier: f.tier, tags: f.tags, owner: f.owner, limit: lim,
        });
        if (res) setResult(res);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Search failed");
      } finally {
        setBusy(false);
      }
    },
    [callTool],
  );

  // Search-as-you-type + filter + load-more (browse mode only). A bare 1-char
  // query is ignored (it would full-scan the whole catalog); a 2+ char query OR
  // any active facet triggers a search.
  useEffect(() => {
    if (mode !== "browse" || platformTab !== "explore") return;
    const q = debouncedQuery.trim();
    const facetActive = filtersActive("", filters);
    if (q.length < 2 && !facetActive) {
      setResult(null);
      return;
    }
    void runSearch(q, filters, limit);
  }, [debouncedQuery, filters, limit, mode, platformTab, runSearch]);

  // Filter change resets the page size.
  const onFiltersChange = useCallback((f: CatalogFilters) => {
    setLimit(30);
    setFilters(f);
  }, []);
  const loadMore = useCallback(() => setLimit((l) => l + 30), []);

  const RAIL_ICON: Record<PlatformTab, string> = { explore: "▦", observability: "◉", governance: "❖" };

  return (
    <MiniAppChrome
      activeTabId="catalog"
      subBar={
        <div className="dc-topbar-inner">
          <MaSearchInput
            value={query}
            onChange={onSearchType}
            onSubmit={() => globalSearch(query)}
            placeholder="Search models, metrics, glossary…"
            actionLabel="Search"
            busy={busy && platformTab === "explore"}
            ariaLabel="Search the data platform"
          />
        </div>
      }
    >
      <div className="dc-shell">
        <aside className="dc-rail">
          <div className="dc-rail-label">Platform</div>
          {PLATFORM_OPTS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`dc-rail-item${platformTab === o.value ? " is-active" : ""}`}
              onClick={() => selectSection(o.value)}
              aria-current={platformTab === o.value ? "page" : undefined}
            >
              <span aria-hidden>{RAIL_ICON[o.value]}</span> {o.label}
            </button>
          ))}
        </aside>

        <div className="dc-main">
          {error && <WarningBanner warnings={[error]} />}

          {mode !== "entity" && (
            <nav className="dc-breadcrumb" aria-label="Breadcrumb">
              <button type="button" onClick={clearAll}>Catalog</button>
              <span>/</span>
              {platformTab === "explore" && (query.trim().length >= 2 || filtersActive("", filters)) ? (
                <>
                  <button type="button" onClick={clearAll}>Explore</button>
                  <span>/</span>
                  <span>{query.trim() ? `Results for “${query.trim()}”` : filters.module ? filters.module : "Filtered"}</span>
                </>
              ) : (
                <span>{SECTION_LABEL[platformTab]}</span>
              )}
            </nav>
          )}

          {mode === "entity" ? (
            entity ? (
              <EntityProfile
                entity={entity}
                section={platformTab}
                onSelectSection={selectSection}
                tab={entityTab}
                onTabChange={setEntityTab}
                busy={busy}
                callTool={callTool}
                onBack={goBack}
                onOpenEntity={openEntity}
                onPickModule={pickModule}
              />
            ) : (
              <div className="dc-root"><div className="dc-skel dc-skel-line" style={{ width: "40%", height: 26 }} /><SkeletonBlock height={120} /><SkeletonBlock height={300} /></div>
            )
          ) : platformTab === "governance" ? (
            <GovernanceSection
              callTool={callTool}
              injected={governance}
              onPickModule={pickModule}
              onExplore={applyExplore}
              onOpenEntity={openEntity}
            />
          ) : platformTab === "observability" ? (
            <ObservabilitySection callTool={callTool} injected={observability} onOpenEntity={openEntity} />
          ) : (
            <ExploreSection
              overview={overview}
              result={result}
              query={query}
              busy={busy}
              filters={filters}
              onFiltersChange={onFiltersChange}
              onOpenEntity={openEntity}
              onPickModule={pickModule}
              onPickType={pickType}
              onGotoSection={selectSection}
              onClearAll={clearAll}
              onLoadMore={loadMore}
            />
          )}
        </div>
      </div>
    </MiniAppChrome>
  );
}
