import { useEffect, useMemo, useState } from "react";
import { TabBar, type TabDef } from "../shared/TabBar";
import {
  KeyVals,
  StatusDot,
  Tags,
  TierBadge,
  TypeBadge,
  fmtInt,
  highlightSql,
  testTone,
} from "./components";
import { SchemaTab } from "./SchemaTab";
import { LineageTab } from "./LineageTab";
import { DataTab } from "./DataTab";
import { QualityTab } from "./QualityTab";
import { RunsTab } from "./RunsTab";
import type { CallTool, CatalogEntity, EntityType, PlatformTab, RunState } from "./types";

interface Props {
  entity: CatalogEntity;
  section: PlatformTab;
  onSelectSection: (s: PlatformTab) => void;
  tab: string;
  onTabChange: (t: string) => void;
  busy: boolean;
  callTool: CallTool;
  onBack: () => void;
  onOpenEntity: (name: string, type: EntityType) => void;
  onPickModule?: (module: string) => void;
}

type ModelTabId =
  | "data" | "schema" | "lineage" | "quality" | "runs" | "relationships" | "metrics" | "definition";

const MODEL_TAB_IDS: ModelTabId[] = [
  "schema", "data", "lineage", "quality", "runs", "relationships", "metrics", "definition",
];

const SECTION_LABEL: Record<PlatformTab, string> = {
  explore: "Explore",
  observability: "Observability",
  governance: "Governance",
};

function LinkChip({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button className="dc-link-chip" type="button" onClick={onClick}>
      {children}
    </button>
  );
}

function FreshnessStrip({ entity, callTool }: { entity: CatalogEntity; callTool: CallTool }) {
  const [runs, setRuns] = useState<RunState | null>(null);
  useEffect(() => {
    let alive = true;
    callTool<RunState>("catalog_run_state", { name: entity.name, history: 1 }).then(
      (r) => alive && setRuns(r),
    );
    return () => {
      alive = false;
    };
  }, [callTool, entity.name]);

  const latest = runs?.available ? runs.latest : null;
  const ageDays = latest?.completed_at
    ? Math.floor((Date.now() - Date.parse(String(latest.completed_at).replace(" ", "T") + "Z")) / 86400000)
    : null;
  // The run-status dot ALWAYS reflects the run status — never recolored by
  // staleness (that hid real errors). Age is shown muted/informational; with
  // Elementary frozen platform-wide a wall-clock "(stale)" flag on every entity
  // is noise, so it's dropped here.
  return (
    <div className="dc-strip">
      {latest ? (
        <>
          <span className="dc-strip-item">
            <StatusDot tone={testTone(latest.status)} /> last run {latest.status}
          </span>
          <span className="dc-strip-item" style={{ color: "var(--text-muted)" }}>
            ◷ {String(latest.completed_at)}{ageDays != null ? ` · ${ageDays}d ago` : ""}
          </span>
          {latest.execution_time != null && <span className="dc-strip-item">⏱ {Number(latest.execution_time).toFixed(2)}s</span>}
          {/* rows WRITTEN by the last run — distinct from the table's total row
              count shown in the Data tab (avoids a "0 rows vs 1,040 rows" clash). */}
          {latest.rows_affected != null && <span className="dc-strip-item">▦ {fmtInt(latest.rows_affected)} rows written</span>}
        </>
      ) : (
        <>
          <span className="dc-strip-item">⚙ {entity.materialization || "—"}</span>
          {entity.tier && <span className="dc-strip-item">tier: {entity.tier}</span>}
          <span className="dc-strip-item" style={{ color: "var(--text-muted)" }}>
            ◷ run state lights up when Elementary is connected
          </span>
        </>
      )}
    </div>
  );
}

function ModelBody({ entity, tab, onTabChange, callTool, onOpenEntity }: {
  entity: CatalogEntity; tab: ModelTabId; onTabChange: (t: ModelTabId) => void;
  callTool: CallTool; onOpenEntity: Props["onOpenEntity"];
}) {
  const tabs = useMemo<TabDef<ModelTabId>[]>(
    () => [
      { id: "schema", label: "Schema", badge: entity.column_count || undefined },
      { id: "data", label: "Data" },
      { id: "lineage", label: "Lineage" },
      { id: "quality", label: "Quality" },
      { id: "runs", label: "Runs" },
      { id: "relationships", label: "Relationships" },
      { id: "metrics", label: "Metrics", badge: entity.metric_count || undefined },
      ...(entity.raw_sql ? [{ id: "definition" as const, label: "Definition" }] : []),
    ],
    [entity],
  );

  return (
    <>
      <TabBar tabs={tabs} active={tab} onChange={onTabChange} ariaLabel="Entity sections" />
      <div className="dc-tab-body">
        {tab === "data" && <DataTab entityName={entity.name} callTool={callTool} />}
        {tab === "schema" && <SchemaTab columns={entity.columns} />}
        {tab === "lineage" && <LineageTab entityName={entity.name} callTool={callTool} onOpenEntity={onOpenEntity} />}
        {tab === "quality" && <QualityTab entity={entity} callTool={callTool} />}
        {tab === "runs" && <RunsTab entityName={entity.name} callTool={callTool} />}
        {tab === "relationships" && (
          <div className="dc-relations">
            <div>
              <div className="dc-group-title">Upstream ({entity.upstream_count})</div>
              <div className="dc-list">
                {entity.upstream.length === 0 && <span className="dc-results-count">None</span>}
                {entity.upstream.map((u) => (
                  <LinkChip key={u} onClick={() => onOpenEntity(u, "model")}>
                    <span className="dc-chip-glyph" aria-hidden>▦</span> {u}
                  </LinkChip>
                ))}
              </div>
            </div>
            <div>
              <div className="dc-group-title">Downstream ({entity.downstream_count})</div>
              <div className="dc-list">
                {entity.downstream.length === 0 && <span className="dc-results-count">None</span>}
                {entity.downstream.map((d) => (
                  <LinkChip key={d} onClick={() => onOpenEntity(d, "model")}>
                    <span className="dc-chip-glyph" aria-hidden>▦</span> {d}
                  </LinkChip>
                ))}
              </div>
            </div>
          </div>
        )}
        {tab === "definition" && (
          entity.raw_sql ? (
            <pre className="dc-sql">{highlightSql(entity.raw_sql)}</pre>
          ) : (
            <div className="dc-empty">No SQL definition available for this model.</div>
          )
        )}
        {tab === "metrics" && (
          entity.metrics.length === 0 ? (
            <div className="dc-empty">No metrics are rooted on this model.</div>
          ) : (
            <div className="dc-card-grid">
              {entity.metrics.map((m) => (
                <button className="dc-mini-card" type="button" key={m.name} onClick={() => onOpenEntity(m.name, "metric")}>
                  <div className="dc-mini-title">∑ {m.label || m.name}</div>
                  <div className="dc-mini-sub">{m.tier || "—"}</div>
                </button>
              ))}
            </div>
          )
        )}
      </div>
    </>
  );
}

function MetricBody({ entity }: { entity: CatalogEntity }) {
  return (
    <div className="dc-tab-body">
      <KeyVals rows={[
        ["measure", entity.measure ?? ""],
        ["metric type", entity.metric_type ?? ""],
        ["semantic status", entity.semantic_status ?? ""],
        ["time grains", (entity.supported_time_grains ?? []).join(", ")],
        ["dimensions", (entity.allowed_dimensions ?? []).join(", ")],
        ["default filters", (entity.default_filters ?? []).join(" · ")],
      ]} />
      {entity.question_synonyms && entity.question_synonyms.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="dc-group-title">Synonyms</div>
          <Tags tags={entity.question_synonyms} max={20} />
        </div>
      )}
    </div>
  );
}

function KpiStrip({ items }: { items: Array<[string, string]> }) {
  return (
    <div className="dc-kpi-grid">
      {items.map(([k, v]) => (
        <div className="dc-kpi" key={k}>
          <div className="dc-kpi-label">{k}</div>
          <div className="dc-kpi-value">{v}</div>
        </div>
      ))}
    </div>
  );
}

function GlossaryBody({ entity, onOpenEntity }: { entity: CatalogEntity; onOpenEntity: Props["onOpenEntity"] }) {
  return (
    <div className="dc-tab-body">
      <KeyVals rows={[
        ["relationship", entity.source_kind && entity.target_kind ? `${entity.source_kind} ${entity.directed ? "→" : "↔"} ${entity.target_kind}` : ""],
        ["directed", entity.directed == null ? "" : entity.directed ? "yes" : "no"],
        ["weight column", entity.weight_column ?? ""],
        ["module", entity.module ?? ""],
        ["backing model", entity.model_name
          ? (<button className="dc-rail-link" type="button" style={{ textAlign: "left" }} onClick={() => onOpenEntity(entity.model_name!, "model")}>{entity.model_name}</button>)
          : ""],
      ]} />
      {entity.question_synonyms && entity.question_synonyms.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div className="dc-group-title">Synonyms</div>
          <Tags tags={entity.question_synonyms} max={20} />
        </div>
      )}
    </div>
  );
}

function MetaRow({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="dc-meta-row">
      <dt>{k}</dt>
      <dd>{v || "—"}</dd>
    </div>
  );
}

function MetaRail({ entity, callTool, onOpenEntity, onPickModule }: {
  entity: CatalogEntity; callTool: CallTool;
  onOpenEntity: Props["onOpenEntity"]; onPickModule?: Props["onPickModule"];
}) {
  const isModel = entity.type === "model";
  return (
    <aside className="dc-entity-rail">
      {isModel && <FreshnessStrip entity={entity} callTool={callTool} />}
      <div className="dc-rail-card">
        <div className="dc-rail-h">Details</div>
        <dl className="dc-meta">
          <MetaRow k="Owner" v={entity.owner} />
          <MetaRow k="Semantic tier" v={entity.tier} />
          {isModel && <MetaRow k="Quality tier" v={entity.quality_tier} />}
          {isModel && <MetaRow k="Materialization" v={entity.materialization} />}
          <MetaRow
            k="Module"
            v={onPickModule && entity.module
              ? <button className="dc-rail-link" type="button" onClick={() => onPickModule(entity.module)}>{entity.module}</button>
              : entity.module}
          />
          {isModel && <MetaRow k="Relation" v={entity.relation_name} />}
          {/* Metric-only fields — never render them (blank) on a glossary entity. */}
          {entity.type === "metric" && <MetaRow k="Metric type" v={entity.metric_type} />}
          {entity.type === "metric" && entity.root_model && (
            <MetaRow k="Root model" v={<button className="dc-rail-link" type="button" onClick={() => onOpenEntity(entity.root_model!, "model")}>{entity.root_model}</button>} />
          )}
        </dl>
      </div>
      {entity.tags && entity.tags.length > 0 && (
        <div className="dc-rail-card">
          <div className="dc-rail-h">Tags</div>
          <Tags tags={entity.tags} max={20} />
        </div>
      )}
    </aside>
  );
}

export function EntityProfile({
  entity, section, onSelectSection, tab, onTabChange,
  busy, callTool, onBack, onOpenEntity, onPickModule,
}: Props) {
  const crumbRoot = (
    <button type="button" onClick={() => onSelectSection(section)}>{SECTION_LABEL[section]}</button>
  );
  if (entity.error) {
    return (
      <div className="dc-root">
        <div className="dc-breadcrumb">
          <button type="button" onClick={onBack}>← Back</button>
          <span>·</span>
          {crumbRoot}
        </div>
        <div className="dc-error">
          <p>{entity.error}</p>
          {entity.suggestions && entity.suggestions.length > 0 && (
            <div className="dc-list" style={{ marginTop: 10 }}>
              {entity.suggestions.map((s) => (
                <LinkChip key={s} onClick={() => onOpenEntity(s, "model")}>{s}</LinkChip>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  const isModel = entity.type === "model";
  const modelTab = (MODEL_TAB_IDS.includes(tab as ModelTabId) ? tab : "schema") as ModelTabId;
  return (
    <div className="dc-root">
      <div className="dc-breadcrumb">
        <button type="button" onClick={onBack}>← Back</button>
        <span>·</span>
        {crumbRoot}
        <span>/</span>
        {entity.module && (
          <>
            {onPickModule ? (
              <button type="button" onClick={() => onPickModule(entity.module)}>{entity.module}</button>
            ) : (
              <span>{entity.module}</span>
            )}
            <span>/</span>
          </>
        )}
        <span>{entity.name}</span>
      </div>

      <header className="dc-profile-header">
        <div className="dc-profile-title-row">
          <h1 className="dc-profile-title">{entity.label || entity.name}</h1>
          <TypeBadge type={entity.type} />
          {entity.tier && <TierBadge tier={entity.tier} />}
          {busy && <span className="dc-results-count">Loading…</span>}
        </div>
        {entity.fqn && <span className="dc-profile-fqn">{entity.fqn}</span>}
        {entity.description && <p className="dc-profile-desc">{entity.description}</p>}
      </header>

      {isModel && <KpiStrip items={[
        ["Columns", String(entity.column_count)],
        ["Upstream", String(entity.upstream_count)],
        ["Downstream", String(entity.downstream_count)],
        ["Tests", String(entity.test_count ?? 0)],
        ["Metrics", String(entity.metric_count)],
      ]} />}
      {entity.type === "metric" && <KpiStrip items={[
        ["Metric type", entity.metric_type || "—"],
        ["Time grains", String((entity.supported_time_grains ?? []).length)],
        ["Dimensions", String((entity.allowed_dimensions ?? []).length)],
        ["Synonyms", String((entity.question_synonyms ?? []).length)],
      ]} />}
      {entity.type === "glossary" && <KpiStrip items={[
        ["Source kind", entity.source_kind || "—"],
        ["Target kind", entity.target_kind || "—"],
        ["Directed", entity.directed == null ? "—" : entity.directed ? "yes" : "no"],
        ["Synonyms", String((entity.question_synonyms ?? []).length)],
      ]} />}

      <div className="dc-entity-grid">
        <div className="dc-entity-main">
          {isModel ? (
            <ModelBody entity={entity} tab={modelTab} onTabChange={onTabChange} callTool={callTool} onOpenEntity={onOpenEntity} />
          ) : entity.type === "glossary" ? (
            <div className="dc-entity-body-narrow"><GlossaryBody entity={entity} onOpenEntity={onOpenEntity} /></div>
          ) : (
            <div className="dc-entity-body-narrow"><MetricBody entity={entity} /></div>
          )}
        </div>
        <MetaRail entity={entity} callTool={callTool} onOpenEntity={onOpenEntity} onPickModule={onPickModule} />
      </div>
    </div>
  );
}
