import type { ReactNode } from "react";
import { useState } from "react";
import { MaThemeToggle } from "./MaThemeToggle";
import "./mini-app-chrome.css";

export interface MiniAppTab {
  id: string;
  label: string;
  /** Dev / vite build target (one `.html` per app). */
  href?: string;
  /** Server-side app id for standalone web delivery (`/app/<appId>`). */
  appId?: string;
}

/** Cross-app navigation. Each mini-app passes `activeTabId` to highlight its slot. */
export const DEFAULT_APP_TABS: MiniAppTab[] = [
  { id: "catalog", label: "Catalog", href: "/data-catalog.html", appId: "data_catalog" },
  { id: "portfolio", label: "Portfolio", href: "/portfolio.html", appId: "portfolio" },
  { id: "contract", label: "Contract", href: "/contract-explorer.html", appId: "contract_explorer" },
  { id: "graph", label: "Graph", href: "/graph-explorer.html", appId: "graph_explorer" },
  { id: "lineage", label: "Lineage", href: "/model-lineage.html", appId: "model_lineage" },
  { id: "metric", label: "Metric Lab", href: "/metric-lab.html", appId: "metric_lab" },
  { id: "reports", label: "Reports", href: "/report-studio.html", appId: "report_studio" },
];

/** Resolve a tab's link. When the bundle is served standalone by our own
 * server (`window.__MINI_APP_API__` is set), cross-app nav must target the
 * `/app/<appId>` route and carry the injected auth token — the dev `.html`
 * filenames only exist under `npm run dev`. */
function tabHref(tab: MiniAppTab): string {
  const api = typeof window !== "undefined" ? window.__MINI_APP_API__ : undefined;
  if (api && tab.appId) {
    const token = window.__MINI_APP_TOKEN__;
    const suffix = token ? `?token=${encodeURIComponent(token)}` : "";
    return `/app/${tab.appId}${suffix}`;
  }
  return tab.href ?? "#";
}

interface MiniAppChromeProps {
  brand?: string;
  tabs?: MiniAppTab[];
  activeTabId?: string;
  onTabClick?: (id: string) => void;
  rightSlot?: ReactNode;
  /** Optional fixed sub-header (e.g. a global search bar) rendered between the
   * app chrome and the scrolling body — always visible, never scrolls. */
  subBar?: ReactNode;
  bodyClassName?: string;
  children: ReactNode;
}

/** True when the bundle is loaded as the top-level document (standalone browser
 * or `npm run dev`). False when iframed inside an MCP host like Claude Desktop —
 * in that case cross-app navigation via plain href would 404 because the host
 * doesn't share our root path. We hide the tabs there; users switch apps via
 * the host's chat (e.g. "open portfolio"). Dev mode always shows tabs.
 */
function isStandaloneContext(): boolean {
  if (typeof window === "undefined") return false;
  if (import.meta.env?.DEV) return true;
  try {
    return window.parent === window;
  } catch {
    // Cross-origin parent access throws — definitely iframed.
    return false;
  }
}

/** Tabs available in this deployment: when the server injected its
 * registered-app list (standalone-served mode), drop tabs whose app is not
 * registered — dev-only apps (portfolio, model lineage) must not render dead
 * 404 tabs. Dev (`npm run dev`) has no injection and keeps the static list. */
function availableTabs(): MiniAppTab[] {
  const registered =
    typeof window !== "undefined" ? window.__MINI_APP_APPS__ : undefined;
  if (!Array.isArray(registered)) return DEFAULT_APP_TABS;
  return DEFAULT_APP_TABS.filter(
    (tab) => !tab.appId || registered.includes(tab.appId),
  );
}

export function MiniAppChrome({
  brand = "CEREBRO ◇ GNOSIS",
  tabs,
  activeTabId,
  onTabClick,
  rightSlot,
  subBar,
  bodyClassName,
  children,
}: MiniAppChromeProps) {
  // If caller passes explicit tabs, honor them. Otherwise default to the
  // cross-app nav when standalone, or no tabs at all in MCP host context.
  const effectiveTabs =
    tabs !== undefined
      ? tabs
      : isStandaloneContext()
        ? availableTabs()
        : [];

  // Cross-app nav is a subordinate APP-SWITCHER (one control under the brand),
  // not a row of tab-styled links — so each app's in-body section nav reads as
  // the primary navigation spine instead of competing with the chrome.
  const [appMenuOpen, setAppMenuOpen] = useState(false);
  const current = effectiveTabs.find((t) => t.id === activeTabId);

  return (
    <div className="ma-chrome mini-app-scope">
      <nav className="ma-bar">
        <span className="ma-brand">{brand}</span>
        {effectiveTabs.length > 0 && (
          <div className="ma-appsw">
            <button
              type="button"
              className="ma-appsw-btn"
              aria-haspopup="menu"
              aria-expanded={appMenuOpen}
              onClick={() => setAppMenuOpen((o) => !o)}
            >
              <span className="ma-appsw-glyph" aria-hidden>⊞</span>
              <span className="ma-appsw-current">{current?.label ?? "Apps"}</span>
              <span className="ma-appsw-caret" aria-hidden>▾</span>
            </button>
            {appMenuOpen && (
              <>
                <div className="ma-appsw-scrim" onClick={() => setAppMenuOpen(false)} />
                <div className="ma-appsw-menu" role="menu">
                  <div className="ma-appsw-head">Apps</div>
                  {effectiveTabs.map((t) => (
                    <a
                      key={t.id}
                      role="menuitem"
                      href={tabHref(t)}
                      className={`ma-appsw-item${t.id === activeTabId ? " is-active" : ""}`}
                      onClick={(e) => {
                        if (onTabClick) {
                          e.preventDefault();
                          onTabClick(t.id);
                        }
                        setAppMenuOpen(false);
                      }}
                    >
                      {t.label}
                    </a>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
        <span className="ma-bar-right">
          {rightSlot}
          <MaThemeToggle />
        </span>
      </nav>
      {subBar && <div className="ma-subbar">{subBar}</div>}
      <div className={`ma-body${bodyClassName ? ` ${bodyClassName}` : ""}`}>
        {children}
      </div>
    </div>
  );
}

interface MaIdentityProps {
  label: string;
  value: string;
  onCopy?: () => void;
  rightSlot?: ReactNode;
}

export function MaIdentity({ label, value, onCopy, rightSlot }: MaIdentityProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    if (!onCopy) return;
    onCopy();
    setCopied(true);
    setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="ma-identity">
      <div className="ma-identity-text">
        <div className="ma-identity-label">{label}</div>
        <div className="ma-identity-value">{value}</div>
      </div>
      {onCopy && (
        <button className="ma-identity-copy" onClick={handleCopy} type="button">
          {copied ? "✓ Copied" : "⎘ Copy"}
        </button>
      )}
      {rightSlot}
    </div>
  );
}

export function MaKpiGrid({ children }: { children: ReactNode }) {
  return <div className="ma-kpi-grid">{children}</div>;
}

interface MaKpiProps {
  label: string;
  value: string;
  delta?: string;
  deltaTone?: "positive" | "negative" | "neutral";
}

export function MaKpi({ label, value, delta, deltaTone = "neutral" }: MaKpiProps) {
  return (
    <div className="ma-kpi">
      <div className="ma-kpi-label">{label}</div>
      <div className="ma-kpi-value">{value}</div>
      {delta && <div className={`ma-kpi-delta ma-kpi-delta--${deltaTone}`}>{delta}</div>}
    </div>
  );
}

interface MaSectionProps {
  index?: string;
  title: string;
  meta?: string;
  children: ReactNode;
}

export function MaSection({ index, title, meta, children }: MaSectionProps) {
  return (
    <section className="ma-section">
      <header className="ma-section-head">
        {index && <span className="ma-section-index">{index}</span>}
        <h2 className="ma-section-title">{title}</h2>
        {meta && <span className="ma-section-meta">{meta}</span>}
      </header>
      <div className="ma-section-body">{children}</div>
    </section>
  );
}

/** Single shimmering skeleton block. Defaults to a KPI-sized box; override via className. */
export function MaSkeleton({ className = "" }: { className?: string }) {
  return <div className={`ma-skeleton ${className}`} />;
}

/** A 4-up KPI grid skeleton — drop in while waiting for KPI data. */
export function MaSkeletonKpiGrid({ count = 4 }: { count?: number }) {
  return (
    <div className="ma-skeleton-kpi-grid">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="ma-skeleton ma-skeleton-kpi" />
      ))}
    </div>
  );
}

/** Stacked rows for a table-shaped loading state. */
export function MaSkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <div>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="ma-skeleton ma-skeleton-row" />
      ))}
    </div>
  );
}

export { MaThemeToggle };
