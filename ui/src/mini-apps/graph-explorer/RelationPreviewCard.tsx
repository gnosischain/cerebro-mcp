// Inspect-only card for one relationship profile, rendered inside the picker
// rail at its normal width.
//
// This replaces the old `.ge-relation-preview` panel, which widened the rail
// from 280px to `minmax(520px, 40%)` (100% below 1440px) to show a nine-row
// definition list AND a forty-row source/target/weight table — while the
// canvas beside it was usually empty, because the preview model only had rows
// once a five-way scope agreement held. The evidence a preview needs to answer
// "is this relationship worth adding?" is the shape, the unit and the
// coverage; the forty rows were a table nobody asked for, occupying the space
// the graph should have had. The rows are on the canvas now.

import type { RefObject } from "react";
import { EvidenceTrigger } from "./ForensicScopeDisclosure";
import {
  coverageLabel,
  relationshipTemporalSupport,
  relationshipWeightUnit,
  scopeHorizon,
} from "./model/profileMeta";
import type { ForensicScope, ProfileCard } from "./types";

interface Props {
  profile: ProfileCard;
  scope: ForensicScope | undefined;
  windowDays: number;
  /** True once the preview's rows are the answer to THIS request. */
  ready: boolean;
  /** Set when the preview failed; the card offers a retry. */
  problem: string | null;
  /** True when this profile is already part of the applied graph. */
  applied: boolean;
  onBack: () => void;
  onApply: () => void;
  onRetry: () => void;
  evidenceOpen: boolean;
  onOpenEvidence: () => void;
  evidenceTriggerRef: RefObject<HTMLButtonElement | null>;
  evidenceDatasets: string;
}

export function RelationPreviewCard({
  profile,
  scope,
  windowDays,
  ready,
  problem,
  applied,
  onBack,
  onApply,
  onRetry,
  evidenceOpen,
  onOpenEvidence,
  evidenceTriggerRef,
  evidenceDatasets,
}: Props) {
  const status = problem
    ? "failed"
    : !ready
      ? "loading real sample"
      : (scope?.status ?? "unknown");

  return (
    <section className="ge-relation-preview" aria-label="Relationship preview">
      <div className="ge-relation-preview__head">
        <strong>{profile.profile}</strong>
        <div className="ge-relation-preview__actions">
          <EvidenceTrigger
            scope={scope}
            datasets={evidenceDatasets}
            open={evidenceOpen}
            onOpen={onOpenEvidence}
            buttonRef={evidenceTriggerRef}
          />
          <span className={`ge-quality ge-quality--${profile.semantic_status}`}>
            {profile.quality_tier || profile.semantic_status}
          </span>
          <button type="button" className="ge-btn" onClick={onBack}>
            Back
          </button>
        </div>
      </div>

      {/* The four facts that decide "add this or not", always visible. */}
      <ul className="ge-relation-preview__summary">
        <li>
          <span>Shape</span>
          <strong>{profile.source_kind} → {profile.target_kind}</strong>
        </li>
        <li>
          <span>Weight</span>
          <strong title={profile.weight_unit || profile.weight_column || "edge_count"}>
            {profile.weight_unit || relationshipWeightUnit(profile.weight_column)}
          </strong>
        </li>
        <li>
          <span>Time</span>
          <strong>
            {relationshipTemporalSupport(profile.temporal_semantics, windowDays)}
          </strong>
        </li>
        <li>
          <span>Sample</span>
          <strong>
            {scope
              ? coverageLabel("Edges", scope.coverage?.edges)
              : status === "failed"
                ? "unavailable"
                : "loading"}
          </strong>
        </li>
      </ul>

      <button type="button" className="ge-btn primary" onClick={onApply} disabled={!ready}>
        {applied ? "Already in the graph" : "Add to graph"}
      </button>

      <details className="ge-relation-preview__definition">
        <summary>Definition and evidence</summary>
        <div className="ge-relation-preview__definition-body">
          <p>{profile.description || "No description supplied."}</p>
          <dl>
            <div>
              <dt>Relation</dt>
              <dd title={profile.model_name}>
                {profile.model_name
                  ? `dbt.${profile.model_name.replace(/^dbt\./, "")}`
                  : "unknown"}
              </dd>
            </div>
            <div>
              <dt>Freshness SLA</dt>
              <dd>{profile.freshness_sla || "not declared"}</dd>
            </div>
            <div>
              <dt>Coverage</dt>
              <dd>{profile.coverage_note || "not declared"}</dd>
            </div>
            <div>
              <dt>Preview status</dt>
              <dd>{status}</dd>
            </div>
            <div>
              <dt>Sample coverage</dt>
              <dd>
                {scope
                  ? `${coverageLabel("Edges", scope.coverage?.edges)}; ${coverageLabel("nodes", scope.coverage?.nodes)}`
                  : "awaiting preview"}
              </dd>
            </div>
            <div>
              <dt>Data horizon</dt>
              <dd>{scopeHorizon(scope)}</dd>
            </div>
            <div>
              <dt>Answering source</dt>
              <dd>
                {scope?.sources?.length
                  ? scope.sources.map((source) => (
                      <span key={`${source.role}:${source.name}`}>
                        {source.name} · {source.role} · {source.status}
                        {source.fetched_at ? ` · fetched ${source.fetched_at}` : ""}
                      </span>
                    ))
                  : "awaiting source contract"}
              </dd>
            </div>
          </dl>
        </div>
      </details>

      {problem ? (
        <div className="ge-load-error" role="alert">
          <span>Preview failed: {problem}</span>
          <button type="button" className="ge-btn" onClick={onRetry}>
            Retry preview
          </button>
        </div>
      ) : null}
    </section>
  );
}
