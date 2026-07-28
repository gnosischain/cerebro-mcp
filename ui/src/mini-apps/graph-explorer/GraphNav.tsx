// Two-level task navigation. Row 1 = the three investigation tasks + an
// end slot (used by Transaction Detail for its chain picker). Row 2 = the
// active task's subviews, rendered ONLY when the task has more than one —
// which today means Money Trail alone. Relationships collapsed to a single
// section, so its Catalog/Investigate pair is gone.
//
// Shaped after cow-explorer/components/CowNav.tsx: clicking a task jumps to
// its last-visited subview, tracked in an effect so navigations that did not
// come through this component (deep links, cross-mode handoffs) still count.

import { useEffect, useRef, type ReactNode } from "react";
import type { GraphMode } from "./types";

export type GraphTask = "relationships" | "money" | "tx";

export const TASK_OF_MODE: Record<GraphMode, GraphTask> = {
  // "atlas" is unreachable at runtime (graphReducer normalizes it away) but
  // remains in GraphMode as a wire value, so the map stays total.
  atlas: "relationships",
  investigate: "relationships",
  flows: "money",
  timeline: "money",
  transactions: "tx",
};

const DEFAULT_MODE: Record<GraphTask, GraphMode> = {
  relationships: "investigate",
  money: "flows",
  tx: "transactions",
};

const TASK_LABEL: Record<GraphTask, string> = {
  relationships: "Relationships",
  money: "Money Trail",
  tx: "Transaction Detail",
};

const TASK_ORDER: GraphTask[] = ["relationships", "money", "tx"];

/** Subviews per task. A task with fewer than two never renders a sub-row. */
const SUBVIEWS: Record<GraphTask, { mode: GraphMode; label: string }[]> = {
  relationships: [],
  money: [
    { mode: "flows", label: "Trail" },
    { mode: "timeline", label: "Over time" },
  ],
  tx: [],
};

function shortHash(value: unknown): string {
  const text = String(value ?? "").trim();
  return text ? text.slice(0, 12) : "unknown";
}

interface Props {
  mode: GraphMode;
  onChange: (mode: GraphMode) => void;
  onExportCase?: () => void;
  /** Rendered at the end of row 1. Transaction Detail puts its chain picker here. */
  endSlot?: ReactNode;
}

export function GraphNav({ mode, onChange, onExportCase, endSlot }: Props) {
  const task = TASK_OF_MODE[mode];
  // Task tabs are navigation, not reset buttons: remember the last subview
  // used inside each task so Money Trail's Over time survives a trip through
  // Transaction Detail.
  const lastVisitedRef = useRef<Record<GraphTask, GraphMode>>({ ...DEFAULT_MODE });
  useEffect(() => {
    lastVisitedRef.current[task] = mode;
  }, [mode, task]);

  const selectTask = (nextTask: GraphTask) =>
    onChange(lastVisitedRef.current[nextTask] ?? DEFAULT_MODE[nextTask]);
  const subviews = SUBVIEWS[task];
  const diagnostics =
    typeof window === "undefined" ? undefined : window.__MINI_APP_DIAGNOSTICS__;

  return (
    <nav className="ge-task-nav" aria-label="Graph Explorer task">
      <div className="ge-task-nav__title">
        <span>Graph Explorer</span>
        <small>forensic workspace</small>
      </div>

      <div className="ge-task-tabs" role="tablist" aria-label="Investigation task">
        {TASK_ORDER.map((candidate) => (
          <button
            key={candidate}
            type="button"
            role="tab"
            aria-selected={candidate === task}
            className={candidate === task ? "is-active" : ""}
            onClick={() => selectTask(candidate)}
          >
            {TASK_LABEL[candidate]}
          </button>
        ))}
      </div>

      {/* Narrow viewports collapse the tab strip to a select (the tabs are
          hidden by CSS below 900px, not unmounted, so both stay in the a11y
          tree exactly as before). */}
      <label className="ge-task-select">
        <span>Task</span>
        <select
          value={task}
          onChange={(event) => selectTask(event.target.value as GraphTask)}
        >
          {TASK_ORDER.map((candidate) => (
            <option key={candidate} value={candidate}>
              {TASK_LABEL[candidate]}
            </option>
          ))}
        </select>
      </label>

      {subviews.length > 1 ? (
        <div
          className="ge-task-subviews"
          role="tablist"
          aria-label={`${TASK_LABEL[task]} view`}
        >
          {subviews.map((entry) => (
            <button
              key={entry.mode}
              type="button"
              role="tab"
              aria-selected={entry.mode === mode}
              className={entry.mode === mode ? "is-active" : ""}
              onClick={() => onChange(entry.mode)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      ) : null}

      {endSlot}

      <details className="ge-task-diagnostics">
        <summary>Advanced</summary>
        <div className="ge-task-diagnostics__panel">
          <strong>Diagnostics</strong>
          <span title={String(diagnostics?.app_commit ?? "")}>app {shortHash(diagnostics?.app_commit)}</span>
          <span title={String(diagnostics?.bundle_sha256 ?? "")}>bundle {shortHash(diagnostics?.bundle_sha256)}</span>
          <span title={String(diagnostics?.dbt_manifest_sha256 ?? "")}>dbt {shortHash(diagnostics?.dbt_manifest_sha256)}</span>
          <span>built {String(diagnostics?.bundle_mtime ?? "unknown")}</span>
          {onExportCase ? (
            <button type="button" className="ge-btn" onClick={onExportCase}>
              Export forensic case
            </button>
          ) : null}
        </div>
      </details>
    </nav>
  );
}
