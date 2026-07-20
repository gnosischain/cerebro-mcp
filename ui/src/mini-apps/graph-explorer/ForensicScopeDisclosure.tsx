import {
  useRef,
  useState,
  type RefObject,
} from "react";
import { TaskAccessoryPanel } from "./TaskAccessoryPanel";
import type { ForensicCoverageCount, ForensicScope } from "./types";

interface BaseProps {
  scope: ForensicScope | undefined;
  datasets: string;
  summary?: string | null;
  bound?: string | null;
  statusLabel?: string | null;
}

export interface EvidenceTriggerProps extends BaseProps {
  open?: boolean;
  onOpen: () => void;
  buttonRef?: RefObject<HTMLButtonElement | null>;
}

export interface EvidencePanelProps extends BaseProps {
  onClose: () => void;
  openerRef?: RefObject<HTMLElement | null>;
}

function coverage(value: ForensicCoverageCount | undefined): string {
  if (!value || value.shown == null) return "unknown";
  return value.total == null
    ? `${value.shown.toLocaleString()} shown · total unknown`
    : `${value.shown.toLocaleString()} of ${value.total.toLocaleString()}`;
}

function observedThrough(scope: ForensicScope): string | null {
  const explicit = scope.result_observed_through;
  if (explicit != null && String(explicit)) return String(explicit);
  return scope.data_horizon != null && String(scope.data_horizon)
    ? String(scope.data_horizon)
    : null;
}

function usd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(Number(value))) return "unknown";
  return `$${Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

export function EvidenceTrigger({
  scope,
  datasets,
  statusLabel,
  open = false,
  onOpen,
  buttonRef,
}: EvidenceTriggerProps) {
  if (!scope?.scope_id || !scope.status) return null;
  const label = statusLabel || scope.status.toUpperCase();
  const sourceCount = scope.sources?.length ?? 0;
  return (
    <button
      ref={buttonRef}
      type="button"
      className={`ge-evidence-trigger ge-evidence-trigger--${scope.status}`}
      aria-label={`Evidence: ${label}, ${sourceCount} source${sourceCount === 1 ? "" : "s"} for ${datasets}`}
      aria-expanded={open}
      title={`${label} · View evidence`}
      onClick={onOpen}
    >
      <span className="ge-evidence-trigger__icon" aria-hidden>ⓘ</span>
      <span className="ge-evidence-trigger__status" aria-hidden />
    </button>
  );
}

export function EvidencePanel({
  scope,
  datasets,
  summary,
  bound,
  statusLabel,
  onClose,
  openerRef,
}: EvidencePanelProps) {
  if (!scope?.scope_id || !scope.status) return null;
  const sources = scope.sources ?? [];
  const warnings = scope.warnings ?? [];
  const residuals = scope.residuals ?? [];
  const verification = scope.verification?.status ?? "unverified";
  const activity = observedThrough(scope);
  const unknownUsdRows = Number(scope.coverage?.usd?.unknown_rows ?? 0);

  return (
    <TaskAccessoryPanel
      title="Evidence"
      subtitle={`${statusLabel || scope.status.toUpperCase()} · ${datasets}`}
      onClose={onClose}
      openerRef={openerRef}
      ariaLabel={`Evidence for ${datasets}`}
    >
      <div className="ge-evidence-panel__details">
        {summary || bound ? (
          <div className="ge-evidence-panel__summary">
            {summary ? <strong>{summary}</strong> : null}
            {bound ? <span>{bound}</span> : null}
          </div>
        ) : null}
        <dl className="ge-evidence-panel__meta">
          <dt>Datasets</dt><dd>{datasets}</dd>
          <dt>Scope</dt><dd title={scope.scope_id}>{scope.scope_id}</dd>
          <dt>Verification</dt>
          <dd>{verification}{scope.verification?.method ? ` · ${scope.verification.method}` : ""}</dd>
          <dt>Applied window</dt>
          <dd>
            {scope.window?.t0 == null && scope.window?.t1 == null
              ? "not time constrained"
              : `${scope.window?.t0 ?? "unknown"} → ${scope.window?.t1 ?? "unknown"}`}
            {scope.window?.source ? ` · ${scope.window.source}` : ""}
          </dd>
          <dt>Observed activity</dt><dd>{activity ?? "unknown"}</dd>
          <dt>App commit</dt><dd>{scope.app_commit ?? "unknown"}</dd>
          <dt>DBT manifest</dt><dd>{scope.dbt_manifest_sha256 ?? "unknown"}</dd>
          <dt>Result hash</dt><dd>{scope.result_row_hash ?? "available per dataset descriptor"}</dd>
        </dl>
        <div className="ge-evidence-panel__coverage" aria-label="Coverage">
          <span>rows {coverage(scope.coverage?.rows)}</span>
          <span>nodes {coverage(scope.coverage?.nodes)}</span>
          <span>edges {coverage(scope.coverage?.edges)}</span>
          <span>
            USD known {usd(scope.coverage?.usd?.known)} · total {usd(scope.coverage?.usd?.total)}
            {unknownUsdRows ? ` · ${unknownUsdRows.toLocaleString()} unpriced` : ""}
          </span>
        </div>
        {scope.truncation?.truncated || scope.truncation?.rule ? (
          <p className="ge-evidence-panel__truncation">
            <strong>Admission bound:</strong>{" "}
            {scope.truncation?.rule || "The returned dataset is truncated."}
          </p>
        ) : null}
        <div className="ge-evidence-panel__sources">
          <strong>Answering sources</strong>
          {sources.length ? sources.map((source, index) => (
            <span key={`${source.role}:${source.name}:${index}`}>
              <b>{source.name}</b> · {source.kind} · {source.role} · {source.status}
              {source.horizon != null ? ` · source watermark ${String(source.horizon)}` : " · watermark unknown"}
              {source.fetched_at ? ` · checked ${source.fetched_at}` : " · checked unknown"}
              {source.error ? ` · ${source.error}` : ""}
            </span>
          )) : <span>No answering source was reported.</span>}
        </div>
        {warnings.length ? (
          <div className="ge-evidence-panel__warnings">
            <strong>Limitations</strong>
            {warnings.map((warning, index) => <span key={`${warning}:${index}`}>{warning}</span>)}
          </div>
        ) : null}
        {residuals.length ? (
          <div className="ge-evidence-panel__residuals">
            <strong>Known residuals</strong>
            {residuals.map((residual, index) => <span key={`${residual}:${index}`}>{residual}</span>)}
          </div>
        ) : null}
      </div>
    </TaskAccessoryPanel>
  );
}

/** Transitional convenience for surfaces without a competing inspector. */
export function ForensicScopeDisclosure(props: BaseProps) {
  const [open, setOpen] = useState(false);
  const openerRef = useRef<HTMLButtonElement>(null);
  if (!props.scope?.scope_id || !props.scope.status) return null;
  return (
    <>
      <EvidenceTrigger
        {...props}
        open={open}
        onOpen={() => setOpen(true)}
        buttonRef={openerRef}
      />
      {open ? (
        <EvidencePanel
          {...props}
          onClose={() => setOpen(false)}
          openerRef={openerRef}
        />
      ) : null}
    </>
  );
}
