import { useEffect, useState } from "react";
import { useMiniApp } from "../shared/useMiniApp";
import { ToastStack } from "../shared/ToastStack";
import { DatasetTable } from "../shared/DatasetTable";
import { TabBar, type TabDef } from "../shared/TabBar";
import { AsyncButton } from "../shared/AsyncButton";
import { MiniAppChrome, MaIdentity, MaKpiGrid, MaKpi } from "../shared/MiniAppChrome";
import { MaHelpButton } from "../shared/HelpDialog";
import { MaToolbar } from "../shared/MaToolbar";
import { MaSearchInput } from "../shared/MaSearchInput";
import { PORTFOLIO_HELP } from "../shared/helpContent";
import { shortAddr } from "../../utils/format";
import type { DatasetDescriptor, MiniAppPayload } from "../shared/miniAppTypes";

type PortfolioSection = "overview" | "relationships" | "yields" | "gpay" | "circles";
type YieldsTab = "positions" | "cashflow" | "activity";
type GPayTab = "balances" | "payments" | "cashback" | "activity";
type CirclesTab = "identity" | "balances" | "trust" | "mint";

interface PresenceState {
  address: string;
  has_yields?: boolean;
  has_gpay?: boolean;
  is_circles_avatar?: boolean;
  circles_avatar?: string;
  circles_display_name?: string;
  is_safe?: boolean;
  owns_safes?: boolean;
  safe_creation_version?: string;
  safe_current_threshold?: number | null;
  safe_owner_count?: number | null;
  is_gpay_safe?: boolean;
  first_activity_date?: string;
  last_activity_date?: string;
}

interface OverviewState {
  yields_kpis?: Record<string, unknown>;
  gpay_lifetime?: Record<string, unknown>;
  gpay_latest_balance_usd?: number;
  circles_summary?: Record<string, unknown>;
  safe?: Record<string, unknown>;
}

interface PortfolioState {
  current_address: string;
  active_section: PortfolioSection;
  loaded_sections: Record<string, boolean>;
  breadcrumbs: { address: string; label: string }[];
  circles_avatar_override: string;
  effective_circles_avatar: string;
  presence: PresenceState;
  overview: OverviewState;
  section_filters: Record<string, { start_date: string; token: string; action: string }>;
  warnings?: string[];
}

const APP_ID = "portfolio";
const ADDRESS_RE = /^0x[a-fA-F0-9]{40}$/;

const MOCK_LOADED =
  typeof window !== "undefined" &&
  new URLSearchParams(window.location.search).get("demo") === "loaded";
const MOCK_ADDR = "0x1234567890abcdef1234567890abcdef12345678";

const MOCK_PAYLOAD: MiniAppPayload<PortfolioState> = {
  type: "INITIAL_LOAD",
  view_id: "dev-view",
  app_id: APP_ID,
  title: "Portfolio",
  status: "ready",
  summary_cards: MOCK_LOADED
    ? [
        { label: "Address", value: `${MOCK_ADDR.slice(0, 6)}…${MOCK_ADDR.slice(-4)}`, tone: "neutral" },
        { label: "Yields TVL", value: "$12,430", tone: "positive" },
        { label: "Activity", value: "active", tone: "neutral" },
      ]
    : [{ label: "Address", value: "Pick an address", tone: "neutral" }],
  datasets: {},
  view_state: {
    current_address: MOCK_LOADED ? MOCK_ADDR : "",
    active_section: "overview",
    loaded_sections: {
      overview: MOCK_LOADED,
      relationships: false,
      yields: false,
      gpay: false,
      circles: false,
    },
    breadcrumbs: [],
    circles_avatar_override: "",
    effective_circles_avatar: "",
    presence: MOCK_LOADED
      ? {
          address: MOCK_ADDR,
          has_yields: true,
          has_gpay: false,
          is_circles_avatar: false,
          first_activity_date: "2024-01-10",
          last_activity_date: "2026-04-15",
        }
      : { address: "" },
    overview: {},
    section_filters: {
      overview: { start_date: "", token: "", action: "" },
      relationships: { start_date: "", token: "", action: "" },
      yields: { start_date: "", token: "", action: "" },
      gpay: { start_date: "", token: "", action: "" },
      circles: { start_date: "", token: "", action: "" },
    },
  },
  warnings: [],
};

function isAddress(value: string): boolean {
  return ADDRESS_RE.test(value.trim());
}

function money(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "—";
  return `$${numeric.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

function number(value: unknown, digits = 2): string {
  const numeric = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(numeric)) return "—";
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function datasetRows(dataset?: DatasetDescriptor): Record<string, unknown>[] {
  if (!dataset) return [];
  return dataset.preview_rows.map((row) =>
    Object.fromEntries(dataset.columns.map((column, index) => [column.name, row[index]])),
  );
}

function OverviewPanel({ state }: { state: PortfolioState }) {
  const presence = state.presence ?? {};
  const overview = state.overview ?? {};
  const yields = overview.yields_kpis ?? {};
  const gpay = overview.gpay_lifetime ?? {};
  const circles = overview.circles_summary ?? {};
  const safe = overview.safe ?? {};

  return (
    <div className="mini-app-detail-stack">
      <div className="mini-app-card-grid">
        <div className="mini-app-data-card">
          <span>Address</span>
          <strong>{presence.address ? shortAddr(presence.address) : "—"}</strong>
          <small>{presence.circles_display_name || "Canonical wallet view"}</small>
        </div>
        <div className="mini-app-data-card">
          <span>First activity</span>
          <strong>{presence.first_activity_date || "—"}</strong>
          <small>Last: {presence.last_activity_date || "—"}</small>
        </div>
        <div className="mini-app-data-card">
          <span>Domains present</span>
          <strong>
            {[
              presence.has_yields ? "Yields" : null,
              presence.has_gpay ? "Gnosis Pay" : null,
              presence.is_circles_avatar ? "Circles" : null,
              presence.is_safe ? "Safe" : null,
            ].filter(Boolean).join(" · ") || "No matches"}
          </strong>
          <small>{presence.owns_safes ? "Owns current Safes" : "No owned Safes"}</small>
        </div>
        <div className="mini-app-data-card">
          <span>Gnosis Pay balance</span>
          <strong>{money(overview.gpay_latest_balance_usd ?? 0)}</strong>
          <small>{number((gpay as Record<string, unknown>).total_payment_count ?? 0, 0)} payments</small>
        </div>
      </div>

      <div className="mini-app-overview-grid">
        <div className="mini-app-data-card">
          <span>Yield LP fees</span>
          <strong>{money((yields as Record<string, unknown>).total_lp_fees_usd ?? 0)}</strong>
          <small>{number((yields as Record<string, unknown>).active_lp_positions ?? 0, 0)} LP positions</small>
        </div>
        <div className="mini-app-data-card">
          <span>Yield lending balance</span>
          <strong>{money((yields as Record<string, unknown>).total_lending_balance_usd ?? 0)}</strong>
          <small>{number((yields as Record<string, unknown>).active_lending_positions ?? 0, 0)} lending positions</small>
        </div>
        <div className="mini-app-data-card">
          <span>Safe threshold</span>
          <strong>{safe.current_threshold ? number(safe.current_threshold, 0) : "—"}</strong>
          <small>{safe.owner_count ? `${number(safe.owner_count, 0)} owners` : "No Safe record"}</small>
        </div>
        <div className="mini-app-data-card">
          <span>Circles summary</span>
          <strong>{number(circles.tokens_held_count ?? 0, 0)} tokens held</strong>
          <small>
            {number(circles.trusts_given_count ?? 0, 0)} trusts given · {number(circles.trusts_received_count ?? 0, 0)} received
          </small>
        </div>
      </div>
    </div>
  );
}

function RelationshipsPanel({
  dataset,
  onNavigate,
}: {
  dataset?: DatasetDescriptor;
  onNavigate: (address: string) => Promise<void>;
}) {
  const rows = datasetRows(dataset);
  if (!rows.length) {
    return <div className="mini-app-unavailable">No immediate Safe or owner relationships were found for this address.</div>;
  }

  const groups = {
    owner_safe: rows.filter((row) => row.relation_type === "owner_safe"),
    safe_owner: rows.filter((row) => row.relation_type === "safe_owner"),
  };

  return (
    <div className="mini-app-detail-stack">
      {(["owner_safe", "safe_owner"] as const).map((relationType) => (
        <section key={relationType} className="mini-app-panel">
          <div className="mini-app-panel__header">
            <h2>{relationType === "owner_safe" ? "Owned Safes" : "Current owners"}</h2>
            <span>{groups[relationType].length}</span>
          </div>
          <div className="mini-app-card-grid">
            {groups[relationType].map((row) => (
              <button
                key={`${relationType}-${String(row.related_address)}`}
                type="button"
                className="mini-app-relation-card"
                onClick={() => void onNavigate(String(row.related_address))}
              >
                <strong>{String(row.label || row.related_address)}</strong>
                <span>{shortAddr(String(row.related_address))}</span>
                <small>Since {String(row.became_related_at || "—")}</small>
                <small>Threshold {number(row.threshold ?? 0, 0)} · {number(row.owner_count ?? 0, 0)} owners</small>
                <div className="mini-app-badge-row">
                  {row.related_is_safe ? <span className="mini-app-pill">Safe</span> : null}
                  {row.related_is_gpay_safe ? <span className="mini-app-pill">GPay</span> : null}
                  {row.related_has_yields ? <span className="mini-app-pill">Yields</span> : null}
                  {row.related_has_gpay ? <span className="mini-app-pill">Pay</span> : null}
                  {row.related_is_circles_avatar ? <span className="mini-app-pill">Circles</span> : null}
                </div>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function SectionFilters({
  section,
  filters,
  onPatch,
}: {
  section: PortfolioSection;
  filters: { start_date: string; token: string; action: string };
  onPatch: (section: PortfolioSection, next: { start_date: string; token: string; action: string }) => Promise<void>;
}) {
  const [draft, setDraft] = useState(filters);

  useEffect(() => {
    setDraft(filters);
  }, [filters]);

  return (
    <div className="mini-app-inline-toolbar">
      <label className="mini-app-inline-field">
        <span>Start date</span>
        <input type="date" value={draft.start_date} onChange={(event) => setDraft((current) => ({ ...current, start_date: event.target.value }))} />
      </label>
      <label className="mini-app-inline-field">
        <span>Token</span>
        <input value={draft.token} onChange={(event) => setDraft((current) => ({ ...current, token: event.target.value }))} placeholder="Optional token" />
      </label>
      <label className="mini-app-inline-field">
        <span>Action</span>
        <input value={draft.action} onChange={(event) => setDraft((current) => ({ ...current, action: event.target.value }))} placeholder="Optional action" />
      </label>
      <AsyncButton variant="secondary" onClick={() => onPatch(section, draft)} loadingLabel="Applying">
        Apply
      </AsyncButton>
    </div>
  );
}

export default function PortfolioApp() {
  const { view, callTool, fetchRows } = useMiniApp<PortfolioState>({
    appId: APP_ID,
    mockPayload: MOCK_PAYLOAD,
  });
  const [addressInput, setAddressInput] = useState("");
  const [circlesOverride, setCirclesOverride] = useState("");
  const [activeSection, setActiveSection] = useState<PortfolioSection>("overview");
  const [yieldsTab, setYieldsTab] = useState<YieldsTab>("positions");
  const [gpayTab, setGPayTab] = useState<GPayTab>("balances");
  const [circlesTab, setCirclesTab] = useState<CirclesTab>("identity");
  // Stage-1 retrofit: split a single `pending` flag into (a) address-scoped
  // and (b) section-scoped so switching sections doesn't freeze the whole
  // page and unrelated panels stay interactive.
  const [loadingAddress, setLoadingAddress] = useState(false);
  const [loadingSection, setLoadingSection] = useState<PortfolioSection | null>(null);
  const pending = loadingAddress || loadingSection !== null;

  const state = view?.view_state;
  const datasets = view?.datasets ?? {};

  useEffect(() => {
    setActiveSection(state?.active_section ?? "overview");
    setAddressInput(state?.current_address ?? "");
    setCirclesOverride(state?.circles_avatar_override ?? "");
  }, [state?.active_section, state?.current_address, state?.circles_avatar_override]);

  if (!view || !state) {
    return <div className="mini-app-loading">Loading Portfolio…</div>;
  }

  const loadAddress = async (address: string, override = "") => {
    setLoadingAddress(true);
    try {
      await callTool("load_portfolio_address", {
        view_id: view.view_id,
        address,
        circles_avatar_override: override,
      });
    } finally {
      setLoadingAddress(false);
    }
  };

  const openSection = async (section: PortfolioSection) => {
    setActiveSection(section);
    setLoadingSection(section);
    try {
      if (!state.loaded_sections?.[section] && section !== "overview" && section !== "relationships") {
        await callTool("load_portfolio_section", {
          view_id: view.view_id,
          section,
        });
      } else {
        await callTool("update_portfolio_focus", {
          view_id: view.view_id,
          section,
          start_date: state.section_filters?.[section]?.start_date ?? "",
          token: state.section_filters?.[section]?.token ?? "",
          action: state.section_filters?.[section]?.action ?? "",
        });
      }
    } finally {
      setLoadingSection((cur) => (cur === section ? null : cur));
    }
  };

  const patchSectionFilters = async (
    section: PortfolioSection,
    next: { start_date: string; token: string; action: string },
  ) => {
    setLoadingSection(section);
    try {
      await callTool("update_portfolio_focus", {
        view_id: view.view_id,
        section,
        start_date: next.start_date,
        token: next.token,
        action: next.action,
      });
    } finally {
      setLoadingSection((cur) => (cur === section ? null : cur));
    }
  };

  const hasAddress = Boolean(state.current_address);

  // Domain-aware sections: hide Yields / Gnosis Pay / Circles when the address
  // has no presence there, instead of rendering empty $0.00 cards and dead
  // tabs. Overview and Relationships always show.
  const presence = state.presence ?? {};
  const visibleSections: PortfolioSection[] = [
    "overview",
    "relationships",
    ...(presence.has_yields ? (["yields"] as const) : []),
    ...(presence.has_gpay ? (["gpay"] as const) : []),
    ...(presence.is_circles_avatar ? (["circles"] as const) : []),
  ];
  // If the active section is no longer available (new address with different
  // presence), fall back to overview so the body never renders a hidden tab.
  const effectiveSection: PortfolioSection = visibleSections.includes(activeSection)
    ? activeSection
    : "overview";

  const sectionFilters = state.section_filters?.[effectiveSection] ?? { start_date: "", token: "", action: "" };

  // Drop the redundant "Address" summary card — MaIdentity already shows it.
  const otherCards = (view.summary_cards ?? []).filter(
    (c) => c.label?.toLowerCase() !== "address",
  );

  return (
    <MiniAppChrome activeTabId="portfolio" rightSlot={<MaHelpButton content={PORTFOLIO_HELP} />}>
    <div className="mini-app-root">
      {hasAddress && state.current_address ? (
        <MaIdentity
          label="Address"
          value={shortAddr(state.current_address, 10, 8)}
          onCopy={() => navigator.clipboard?.writeText(state.current_address)}
          rightSlot={
            pending ? (
              <span
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--warning)",
                  marginLeft: 8,
                }}
              >
                Updating…
              </span>
            ) : null
          }
        />
      ) : null}

      <ToastStack warnings={view.warnings ?? []} />

      {otherCards.length > 0 ? (
        <MaKpiGrid>
          {otherCards.map((card, i) => (
            <MaKpi
              key={i}
              label={card.label}
              value={String(card.value)}
              deltaTone={
                card.tone === "positive"
                  ? "positive"
                  : card.tone === "negative"
                    ? "negative"
                    : "neutral"
              }
            />
          ))}
        </MaKpiGrid>
      ) : null}

      <MaToolbar className="mini-app-controls">
        <MaSearchInput
          value={addressInput}
          onChange={setAddressInput}
          onSubmit={() => {
            if (isAddress(addressInput)) void loadAddress(addressInput);
          }}
          placeholder="0x… paste a Gnosis Chain address"
          actionLabel="Load portfolio"
          ariaLabel="Portfolio address"
          actionDisabled={!isAddress(addressInput)}
          busy={pending}
        />
      </MaToolbar>

      {!hasAddress ? (
        <section className="mini-app-picker mini-app-picker--compact">
          <div className="mini-app-picker__head">
            <h2 className="mini-app-picker__title">Address portfolio</h2>
            <p className="mini-app-picker__subtitle">
              Load any wallet or Safe address to inspect overview, relationships, and the domain-specific sections.
            </p>
          </div>
          {!isAddress(addressInput) && addressInput ? (
            <div className="mini-app-picker__error">Enter a valid 0x-prefixed address to continue.</div>
          ) : null}
        </section>
      ) : (
        <>
          {state.breadcrumbs?.length ? (
            <div className="mini-app-breadcrumbs">
              {state.breadcrumbs.map((crumb) => (
                <button
                  key={crumb.address}
                  type="button"
                  className="mini-app-breadcrumbs__item"
                  onClick={() => void loadAddress(crumb.address)}
                >
                  {crumb.label || shortAddr(crumb.address)}
                </button>
              ))}
              <span className="mini-app-breadcrumbs__current">{shortAddr(state.current_address)}</span>
            </div>
          ) : null}

          <TabBar<PortfolioSection>
            ariaLabel="Portfolio section"
            active={effectiveSection}
            onChange={(section) => void openSection(section)}
            tabs={visibleSections.map((id) => ({
              id,
              label: id === "gpay" ? "Gnosis Pay" : id.charAt(0).toUpperCase() + id.slice(1),
              badge: loadingSection === id ? "…" : undefined,
            }) satisfies TabDef<PortfolioSection>)}
          />

          {effectiveSection !== "overview" && effectiveSection !== "relationships" ? (
            <SectionFilters section={effectiveSection} filters={sectionFilters} onPatch={patchSectionFilters} />
          ) : null}

          {effectiveSection === "overview" ? <OverviewPanel state={state} /> : null}

          {effectiveSection === "relationships" ? (
            <RelationshipsPanel
              dataset={datasets.relationships}
              onNavigate={async (address) => {
                setLoadingAddress(true);
                try {
                  await callTool("navigate_portfolio_relation", {
                    view_id: view.view_id,
                    related_address: address,
                  });
                } finally {
                  setLoadingAddress(false);
                }
              }}
            />
          ) : null}

          {effectiveSection === "yields" ? (
            <div className="mini-app-detail-stack">
              <div className="mini-app-analysis__tabs">
                {(["positions", "cashflow", "activity"] as YieldsTab[]).map((tab) => (
                  <button key={tab} type="button" className={`mini-app-analysis__tab ${yieldsTab === tab ? "is-active" : ""}`} onClick={() => setYieldsTab(tab)}>
                    {tab === "cashflow" ? "Cashflow" : tab.charAt(0).toUpperCase() + tab.slice(1)}
                  </button>
                ))}
              </div>

              {yieldsTab === "positions" ? (
                <div className="mini-app-comparison-grid">
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>LP</h2>
                    </div>
                    <DatasetTable dataset={datasets.yields_lp_positions} datasetKey="yields_lp_positions" emptyLabel="No LP positions found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Lending</h2>
                    </div>
                    <DatasetTable dataset={datasets.yields_lending_positions} datasetKey="yields_lending_positions" emptyLabel="No lending positions found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                </div>
              ) : null}

              {yieldsTab === "cashflow" ? (
                <div className="mini-app-comparison-grid">
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Fee collections</h2>
                    </div>
                    <DatasetTable dataset={datasets.yields_fee_collections} datasetKey="yields_fee_collections" emptyLabel="No fee collections found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Lending balances</h2>
                    </div>
                    <DatasetTable dataset={datasets.yields_lending_balances} datasetKey="yields_lending_balances" emptyLabel="No lending balance history found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                </div>
              ) : null}

              {yieldsTab === "activity" ? (
                <DatasetTable dataset={datasets.yields_activity} datasetKey="yields_activity" emptyLabel="No yield activity found." viewId={view.view_id} fetchRows={fetchRows} />
              ) : null}
            </div>
          ) : null}

          {effectiveSection === "gpay" ? (
            <div className="mini-app-detail-stack">
              <div className="mini-app-analysis__tabs">
                {(["balances", "payments", "cashback", "activity"] as GPayTab[]).map((tab) => (
                  <button key={tab} type="button" className={`mini-app-analysis__tab ${gpayTab === tab ? "is-active" : ""}`} onClick={() => setGPayTab(tab)}>
                    {tab.charAt(0).toUpperCase() + tab.slice(1)}
                  </button>
                ))}
              </div>

              {gpayTab === "balances" ? (
                <div className="mini-app-comparison-grid">
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Latest balances</h2>
                    </div>
                    <DatasetTable dataset={datasets.gpay_balances_latest} datasetKey="gpay_balances_latest" emptyLabel="No balance composition found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Balance history</h2>
                    </div>
                    <DatasetTable dataset={datasets.gpay_balances_daily} datasetKey="gpay_balances_daily" emptyLabel="No balance history found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                </div>
              ) : null}

              {gpayTab === "payments" ? (
                <DatasetTable dataset={datasets.gpay_payments} datasetKey="gpay_payments" emptyLabel="No payments found." viewId={view.view_id} fetchRows={fetchRows} />
              ) : null}
              {gpayTab === "cashback" ? (
                <DatasetTable dataset={datasets.gpay_cashback} datasetKey="gpay_cashback" emptyLabel="No cashback found." viewId={view.view_id} fetchRows={fetchRows} />
              ) : null}
              {gpayTab === "activity" ? (
                <DatasetTable dataset={datasets.gpay_activity} datasetKey="gpay_activity" emptyLabel="No Gnosis Pay activity found." viewId={view.view_id} fetchRows={fetchRows} />
              ) : null}
            </div>
          ) : null}

          {effectiveSection === "circles" ? (
            <div className="mini-app-detail-stack">
              {/* Stage-1 retrofit: always render the override input; disable
                  (rather than unmount) when the address is already a Circles
                  avatar so users see *why* it's unavailable. */}
              <div className="mini-app-inline-toolbar">
                <label className="mini-app-inline-field mini-app-inline-field--wide">
                  <span>Circles avatar override</span>
                  <input
                    value={circlesOverride}
                    onChange={(event) => setCirclesOverride(event.target.value)}
                    placeholder="Optional avatar address"
                    disabled={state.presence?.is_circles_avatar}
                    title={
                      state.presence?.is_circles_avatar
                        ? "This address is already a Circles avatar"
                        : undefined
                    }
                  />
                </label>
                <AsyncButton
                  variant="secondary"
                  disabled={
                    state.presence?.is_circles_avatar ||
                    !circlesOverride ||
                    !isAddress(circlesOverride)
                  }
                  loadingLabel="Loading"
                  onClick={() => loadAddress(state.current_address, circlesOverride)}
                >
                  Load avatar
                </AsyncButton>
              </div>

              <div className="mini-app-analysis__tabs">
                {(["identity", "balances", "trust", "mint"] as CirclesTab[]).map((tab) => (
                  <button key={tab} type="button" className={`mini-app-analysis__tab ${circlesTab === tab ? "is-active" : ""}`} onClick={() => setCirclesTab(tab)}>
                    {tab.charAt(0).toUpperCase() + tab.slice(1)}
                  </button>
                ))}
              </div>

              {circlesTab === "identity" ? (
                <DatasetTable dataset={datasets.circles_metadata} datasetKey="circles_metadata" emptyLabel="No Circles identity found." viewId={view.view_id} fetchRows={fetchRows} />
              ) : null}
              {circlesTab === "balances" ? (
                <div className="mini-app-comparison-grid">
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Balances</h2>
                    </div>
                    <DatasetTable dataset={datasets.circles_balances} datasetKey="circles_balances" emptyLabel="No Circles balances found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Distribution</h2>
                    </div>
                    <DatasetTable dataset={datasets.circles_distribution} datasetKey="circles_distribution" emptyLabel="No token distribution found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                </div>
              ) : null}
              {circlesTab === "trust" ? (
                <div className="mini-app-comparison-grid">
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Trust summary</h2>
                    </div>
                    <DatasetTable dataset={datasets.circles_trusts_summary} datasetKey="circles_trusts_summary" emptyLabel="No trust summary found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                  <section className="mini-app-panel">
                    <div className="mini-app-panel__header">
                      <h2>Trust relations</h2>
                    </div>
                    <DatasetTable dataset={datasets.circles_trust_relations} datasetKey="circles_trust_relations" emptyLabel="No trust relations found." viewId={view.view_id} fetchRows={fetchRows} />
                  </section>
                </div>
              ) : null}
              {circlesTab === "mint" ? (
                <DatasetTable dataset={datasets.circles_mint_activity} datasetKey="circles_mint_activity" emptyLabel="No mint activity found." viewId={view.view_id} fetchRows={fetchRows} />
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </div>
    </MiniAppChrome>
  );
}
