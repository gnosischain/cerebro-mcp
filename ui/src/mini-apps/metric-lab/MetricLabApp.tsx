// Metric Lab root — thin composition shell:
//   browse  (catalog: sector grid → facet search → detail drawer → basket)
//   workspace (loaded data: chart / table / analysis tabs)
// State rules: the draft QuerySpec never auto-runs (explicit Run button);
// chart config is client-owned and debounced-synced to the server; URL is
// the navigation source of truth (deep-linkable, Back/Forward safe).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MaHelpButton } from "../shared/HelpDialog";
import { METRIC_LAB_HELP } from "../shared/helpContent";
import { MaSearchInput } from "../shared/MaSearchInput";
import {
  MaSkeletonKpiGrid,
  MiniAppChrome,
} from "../shared/MiniAppChrome";
import { WarningBanner } from "../shared/WarningBanner";
import { useDebouncedValue } from "../shared/useDebouncedValue";
import { useMiniApp } from "../shared/useMiniApp";
import { BrowseSection } from "./BrowseSection";
import { numericColumns, timeColumn } from "./catalogSearch";
import { MetricDetailPanel } from "./MetricDetailPanel";
import { QueryBuilder } from "./QueryBuilder";
import { WorkspaceSection } from "./WorkspaceSection";
import { buildMockPayload } from "./devFixture";
import {
  APP_ID,
  DEFAULT_QUERY_SPEC,
  type MetricCatalogEntry,
  type MetricLabState,
  type QuerySpec,
} from "./types";
import { readUrl, writeUrl, type UrlState } from "./urlState";
import { useChartConfigSync } from "./useChartConfigSync";
import { useHydratedRows } from "./useHydratedRows";

export default function MetricLabApp() {
  const mock = useMemo(
    () => (import.meta.env.DEV ? buildMockPayload() : undefined),
    [],
  );
  const { view, callTool: rawCallTool, fetchRows: rawFetchRows, updateModelContext } =
    useMiniApp<MetricLabState>({ appId: APP_ID, mockPayload: mock });

  // useMiniApp recreates callTool/fetchRows every render — stable wrappers
  // keep every downstream hook honest about deps (this single pattern
  // replaces the five documented ref workarounds of the old app).
  const callToolRef = useRef(rawCallTool);
  callToolRef.current = rawCallTool;
  const callTool = useCallback(
    <T,>(name: string, args: Record<string, unknown>): Promise<T | null> =>
      callToolRef.current<T>(name, args),
    [],
  );
  const fetchRowsRef = useRef(rawFetchRows);
  fetchRowsRef.current = rawFetchRows;
  const fetchRows = useCallback(
    (viewId: string, datasetKey: string, pageToken?: string) =>
      fetchRowsRef.current(viewId, datasetKey, pageToken),
    [],
  );

  const state = view?.view_state ?? null;
  const catalog = state?.metric_catalog ?? [];
  const hasData = Boolean(view?.datasets?.primary && state?.mode === "loaded");

  // ---- URL-seeded navigation state ----
  const seedRef = useRef<UrlState | null>(null);
  if (seedRef.current === null && typeof window !== "undefined") {
    seedRef.current = readUrl();
  }
  const seed = seedRef.current!;

  // Land on browse; the adoption effect below flips to workspace as soon as
  // a loaded dataset arrives (agent-driven open or a Run completing).
  const [screen, setScreen] = useState<"browse" | "workspace">("browse");
  const [query, setQuery] = useState(seed.q);
  const debouncedQuery = useDebouncedValue(query, 250);
  const [sector, setSector] = useState(seed.sector);
  const [layerFilter, setLayerFilter] = useState(seed.layer);
  const [tagFilter, setTagFilter] = useState(seed.tag);
  const [timeseriesOnly, setTimeseriesOnly] = useState(seed.ts);
  const [detail, setDetail] = useState(seed.detail);
  const [spec, setSpec] = useState<QuerySpec>({
    ...DEFAULT_QUERY_SPEC,
    metrics: seed.metrics,
    dimensions: seed.dims,
    limit: seed.limit,
    windowDays: seed.window,
    mode: (seed.mode as QuerySpec["mode"]) || "raw",
    aggX: seed.x,
    aggY: seed.y,
    aggFn: (seed.agg as QuerySpec["aggFn"]) || "sum",
    aggSeries: seed.series,
    aggTopN: seed.topn,
    filterCol: seed.fcol,
    filterOp: (seed.fop as QuerySpec["filterOp"]) || "=",
    filterValue: seed.fval,
  });
  const [lastRunSpec, setLastRunSpec] = useState<string>("");
  const [runError, setRunError] = useState("");
  const [loading, setLoading] = useState(false);

  const applyingUrlRef = useRef(false);

  // Once a loaded view arrives (agent-driven open or Run), show the workspace
  // and adopt its selection into the draft.
  const adoptedViewRef = useRef("");
  useEffect(() => {
    if (!hasData || !state) return;
    const identity = `${view?.view_id}|${(state.selected_metrics ?? []).join(",")}|${view?.datasets?.primary?.sql ?? ""}`;
    if (adoptedViewRef.current === identity) return;
    adoptedViewRef.current = identity;
    setScreen("workspace");
    const metrics = (state.selected_metrics ?? []).filter(Boolean);
    const aggCfg = (state.aggregate_config ?? {}) as Record<string, string | number>;
    const next: QuerySpec = {
      ...spec,
      metrics: metrics.length ? metrics : [state.selected_metric].filter(Boolean),
      dimensions: state.selected_dimensions ?? [],
      limit: state.selected_limit ?? 2000,
      // Adopt the server's echo so agent-driven aggregate loads rehydrate
      // the builder controls correctly.
      mode: state.load_mode === "aggregate" ? "aggregate" : spec.mode,
      aggX: String(aggCfg.x ?? spec.aggX),
      aggY: String(aggCfg.y ?? spec.aggY),
      aggFn: (aggCfg.agg as QuerySpec["aggFn"]) ?? spec.aggFn,
      aggSeries: String(aggCfg.series ?? spec.aggSeries),
      orderByField: "",
      orderDir: "desc",
    };
    setSpec(next);
    setLastRunSpec(JSON.stringify(next));
    setRunError("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasData, state, view]);

  // ---- URL sync (replace on tweaks; push on screen-level nav) ----
  const prevMajorRef = useRef("");
  useEffect(() => {
    if (typeof window === "undefined" || applyingUrlRef.current) return;
    const urlState: UrlState = {
      q: debouncedQuery,
      sector,
      layer: layerFilter,
      tag: tagFilter,
      window: spec.windowDays,
      ts: timeseriesOnly,
      metrics: spec.metrics,
      dims: spec.dimensions,
      limit: spec.limit,
      order: "",
      tab: "chart",
      chart: "",
      mode: spec.mode === "aggregate" ? "aggregate" : "",
      x: spec.aggX,
      y: spec.aggY,
      agg: spec.aggFn,
      series: spec.aggSeries,
      topn: spec.aggTopN,
      fcol: spec.filterCol,
      fop: spec.filterOp,
      fval: spec.filterValue,
      detail,
    };
    const major = `${screen}|${detail}`;
    writeUrl(urlState, prevMajorRef.current !== "" && major !== prevMajorRef.current);
    prevMajorRef.current = major;
  }, [debouncedQuery, sector, layerFilter, tagFilter, timeseriesOnly, spec, detail, screen]);

  // Back/Forward rebuilds filter state from the URL.
  useEffect(() => {
    const onPop = () => {
      applyingUrlRef.current = true;
      const s = readUrl();
      setQuery(s.q);
      setSector(s.sector);
      setLayerFilter(s.layer);
      setTagFilter(s.tag);
      setTimeseriesOnly(s.ts);
      setDetail(s.detail);
      // Restore the draft but never auto-run a query from history.
      setSpec((prev) => ({
        ...prev,
        metrics: s.metrics,
        dimensions: s.dims,
        limit: s.limit,
        windowDays: s.window,
        mode: (s.mode as QuerySpec["mode"]) || "raw",
        aggX: s.x,
        aggY: s.y,
        aggFn: (s.agg as QuerySpec["aggFn"]) || "sum",
        aggSeries: s.series,
        aggTopN: s.topn,
        filterCol: s.fcol,
        filterOp: (s.fop as QuerySpec["filterOp"]) || "=",
        filterValue: s.fval,
      }));
      window.setTimeout(() => {
        applyingUrlRef.current = false;
      }, 0);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // ---- Hydration + chart config ----
  const primaryDescriptor = view?.datasets?.primary;
  const secondaryDescriptor = view?.datasets?.secondary;
  const primary = useHydratedRows(view?.view_id, primaryDescriptor, fetchRows);
  const secondaryHydrated = useHydratedRows(view?.view_id, secondaryDescriptor, fetchRows);
  const secondary = secondaryDescriptor ? secondaryHydrated : null;

  const datasetEpoch = `${view?.view_id ?? ""}|${primaryDescriptor?.sql ?? ""}`;
  const [config, updateConfig] = useChartConfigSync(
    view?.view_id,
    state?.chart,
    datasetEpoch,
    callTool,
    hasData,
  );

  // ---- Model context (what the agent sees about this view) ----
  useEffect(() => {
    if (!state) return;
    updateModelContext({
      mode: state.mode,
      screen,
      metrics: (state.selected_metrics ?? []).join(",") || state.selected_metric || "none",
      chart_type: config.chartType,
      x_field: config.xField,
      y_field: config.yField,
      group_by: config.groupBy || "none",
      rows_loaded: primary.rows.length,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state?.mode, screen, config, primary.rows.length]);

  // ---- Catalog lookups ----
  // Entries selected from SERVER search results are not in the embedded
  // catalog page — remember every entry the user acted on, or dimension
  // seeding / basket chips silently degrade (the scalar-load bug).
  const [extraEntries, setExtraEntries] = useState<Map<string, MetricCatalogEntry>>(
    () => new Map(),
  );
  const rememberEntry = useCallback((entry: MetricCatalogEntry) => {
    setExtraEntries((prev) => {
      if (prev.has(entry.name)) return prev;
      const next = new Map(prev);
      next.set(entry.name, entry);
      return next;
    });
  }, []);

  const catalogByName = useMemo(() => {
    const m = new Map<string, MetricCatalogEntry>(extraEntries);
    for (const e of catalog) m.set(e.name, e);
    return m;
  }, [catalog, extraEntries]);

  const dirty = useMemo(
    () => spec.metrics.length > 0 && JSON.stringify(spec) !== lastRunSpec,
    [spec, lastRunSpec],
  );

  // URL-seeded baskets (?metrics=…) bypass addToBasket, so apply the smart
  // Aggregate default once the catalog entry resolves — a shared deep link
  // must chart correctly out of the box, same as a clicked add.
  const urlModeDefaulted = useRef(false);
  useEffect(() => {
    if (urlModeDefaulted.current || seed.mode || hasData) return;
    if (spec.metrics.length !== 1 || spec.mode !== "raw") return;
    const entry = catalogByName.get(spec.metrics[0]);
    if (!entry) return; // catalog page not loaded yet
    urlModeDefaulted.current = true;
    const t = timeColumn(entry);
    const nums = numericColumns(entry);
    if (t && nums.length > 0) {
      setSpec((prev) => ({
        ...prev,
        mode: "aggregate",
        aggX: prev.aggX || t,
        aggY: prev.aggY || nums[0],
      }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalogByName, spec.metrics, spec.mode, hasData]);

  // ---- Actions ----
  const addToBasket = useCallback(
    (entry: MetricCatalogEntry) => {
      rememberEntry(entry);
      setSpec((prev) => {
        if (prev.metrics.includes(entry.name)) return prev;
        const next = { ...prev, metrics: [...prev.metrics, entry.name] };
        // First pick: default to Aggregate when the model has a date +
        // numeric column — the only correct way to chart big panels.
        if (prev.metrics.length === 0) {
          const t = timeColumn(entry);
          const nums = numericColumns(entry);
          if (t && nums.length > 0) {
            next.mode = "aggregate";
            next.aggX = next.aggX || t;
            next.aggY = next.aggY || nums[0];
          }
        }
        return next;
      });
    },
    [rememberEntry],
  );

  const runSpec = useCallback(
    async (specToRun: QuerySpec) => {
      if (!view?.view_id || specToRun.metrics.length === 0) return;
      setLoading(true);
      setRunError("");
      try {
        const aggregate = specToRun.mode === "aggregate" && specToRun.metrics.length === 1;
        await callTool("load_metric_lab_metric", {
          view_id: view.view_id,
          metric:
            specToRun.metrics.length === 1 ? specToRun.metrics[0] : specToRun.metrics,
          limit: specToRun.limit,
          window_days: specToRun.windowDays,
          mode: aggregate ? "aggregate" : "raw",
          ...(aggregate
            ? {
                x: specToRun.aggX,
                y: specToRun.aggY,
                agg: specToRun.aggFn,
                series: specToRun.aggSeries,
                series_top_n: specToRun.aggTopN,
                filter_col: specToRun.filterCol,
                filter_op: specToRun.filterOp,
                filter_value: specToRun.filterValue,
              }
            : {}),
        });
        setLastRunSpec(JSON.stringify(specToRun));
        setScreen("workspace");
        setDetail("");
      } catch (err) {
        setRunError(err instanceof Error ? err.message : "Query failed");
      } finally {
        setLoading(false);
      }
    },
    [view?.view_id, callTool],
  );

  const loadSolo = useCallback(
    (entry: MetricCatalogEntry) => {
      rememberEntry(entry);
      const t = timeColumn(entry);
      const nums = numericColumns(entry);
      const aggregate = Boolean(t && nums.length > 0);
      const soloSpec: QuerySpec = {
        ...DEFAULT_QUERY_SPEC,
        metrics: [entry.name],
        limit: spec.limit,
        windowDays: spec.windowDays,
        mode: aggregate ? "aggregate" : "raw",
        aggX: aggregate ? (t as string) : "",
        aggY: aggregate ? nums[0] : "",
      };
      setSpec(soloSpec);
      void runSpec(soloSpec);
    },
    [rememberEntry, runSpec, spec.limit, spec.windowDays],
  );

  // ---- Render ----
  const detailEntry = detail ? (catalogByName.get(detail) ?? null) : null;

  return (
    <MiniAppChrome
      activeTabId="metric"
      subBar={
        <div className="mlab-subbar">
          <MaSearchInput
            value={query}
            onChange={setQuery}
            onSubmit={() => setScreen("browse")}
            placeholder="Search metrics and tables — e.g. bridge volume, validators, gpay…"
            actionLabel="Search"
            ariaLabel="Search the metric catalog"
          />
          {hasData && (
            <button
              type="button"
              className="mlab-toggle"
              onClick={() => setScreen(screen === "browse" ? "workspace" : "browse")}
            >
              {screen === "browse" ? "→ workspace" : "← catalog"}
            </button>
          )}
        </div>
      }
      rightSlot={<MaHelpButton content={METRIC_LAB_HELP} />}
    >
      <div className="mini-app-root mlab-root">
        {!view && <MaSkeletonKpiGrid />}

        {view && (
          <>
            <WarningBanner warnings={view.warnings ?? []} />

            <QueryBuilder
              spec={spec}
              onSpecChange={(patch) => setSpec((prev) => ({ ...prev, ...patch }))}
              catalogByName={catalogByName}
              dirty={dirty}
              loading={loading}
              error={runError}
              onRun={() => runSpec(spec)}
            />

            {screen === "browse" && (
              <div className="mlab-browse-wrap">
                <BrowseSection
                  catalog={catalog}
                  catalogTotal={state?.catalog_total}
                  catalogFacets={state?.catalog_facets}
                  query={debouncedQuery}
                  sector={sector}
                  layer={layerFilter}
                  tag={tagFilter}
                  timeseriesOnly={timeseriesOnly}
                  onFilterChange={(patch) => {
                    if (patch.sector !== undefined) setSector(patch.sector);
                    if (patch.layer !== undefined) setLayerFilter(patch.layer);
                    if (patch.tag !== undefined) setTagFilter(patch.tag);
                    if (patch.timeseries !== undefined) setTimeseriesOnly(patch.timeseries);
                  }}
                  callTool={callTool}
                  basket={spec.metrics}
                  onAddToBasket={addToBasket}
                  onOpenDetail={setDetail}
                  onLoadSolo={loadSolo}
                />
                {detail && (
                  <MetricDetailPanel
                    name={detail}
                    fallback={detailEntry}
                    callTool={callTool}
                    inBasket={spec.metrics.includes(detail)}
                    onAddToBasket={(entry) => {
                      addToBasket(entry);
                      setDetail("");
                    }}
                    onLoadSolo={loadSolo}
                    onClose={() => setDetail("")}
                  />
                )}
              </div>
            )}

            {screen === "workspace" && hasData && state && (
              <WorkspaceSection
                state={state}
                summaryCards={view.summary_cards ?? []}
                primary={primary}
                primaryDescriptor={primaryDescriptor}
                secondary={secondary}
                secondaryDescriptor={secondaryDescriptor}
                config={config}
                onConfigChange={updateConfig}
                datasetEpoch={datasetEpoch}
              />
            )}

            {screen === "workspace" && !hasData && (
              <div className="mlab-empty">
                No data loaded yet — pick a metric in the catalog and press Run.
              </div>
            )}
          </>
        )}
      </div>
    </MiniAppChrome>
  );
}
