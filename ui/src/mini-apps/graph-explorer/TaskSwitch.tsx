import { useRef } from "react";
import type { GraphMode } from "./types";

export type GraphTask = "relationships" | "money" | "tx";

export const TASK_OF_MODE: Record<GraphMode, GraphTask> = {
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

function shortHash(value: unknown): string {
  const text = String(value ?? "").trim();
  return text ? text.slice(0, 12) : "unknown";
}

interface Props {
  mode: GraphMode;
  onChange: (mode: GraphMode) => void;
  onExportCase?: () => void;
}

export function TaskSwitch({ mode, onChange, onExportCase }: Props) {
  const task = TASK_OF_MODE[mode];
  // Task tabs are navigation, not reset buttons. Remember the last legacy
  // subview used inside each task so Relationships Catalog and Money Trail
  // Over time survive a trip through Transaction Detail.
  const lastModeByTask = useRef<Record<GraphTask, GraphMode>>({ ...DEFAULT_MODE });
  lastModeByTask.current[task] = mode;
  const selectTask = (nextTask: GraphTask) =>
    onChange(lastModeByTask.current[nextTask]);
  const diagnostics =
    typeof window === "undefined" ? undefined : window.__MINI_APP_DIAGNOSTICS__;

  return (
    <nav className="ge-task-nav" aria-label="Graph Explorer task">
      <div className="ge-task-nav__title">
        <span>Graph Explorer</span>
        <small>forensic workspace</small>
      </div>

      <div className="ge-task-tabs" role="tablist" aria-label="Investigation task">
        {(Object.keys(TASK_LABEL) as GraphTask[]).map((candidate) => (
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

      <label className="ge-task-select">
        <span>Task</span>
        <select
          value={task}
          onChange={(event) => selectTask(event.target.value as GraphTask)}
        >
          {(Object.keys(TASK_LABEL) as GraphTask[]).map((candidate) => (
            <option key={candidate} value={candidate}>
              {TASK_LABEL[candidate]}
            </option>
          ))}
        </select>
      </label>

      {task === "relationships" ? (
        <div className="ge-task-subviews" role="group" aria-label="Relationships view">
          <button
            type="button"
            className={mode === "atlas" ? "is-active" : ""}
            onClick={() => onChange("atlas")}
          >
            Catalog
          </button>
          <button
            type="button"
            className={mode === "investigate" ? "is-active" : ""}
            onClick={() => onChange("investigate")}
          >
            Investigate
          </button>
        </div>
      ) : task === "money" ? (
        <div className="ge-task-subviews" role="group" aria-label="Money Trail view">
          <button
            type="button"
            className={mode === "flows" ? "is-active" : ""}
            onClick={() => onChange("flows")}
          >
            Trail
          </button>
          <button
            type="button"
            className={mode === "timeline" ? "is-active" : ""}
            onClick={() => onChange("timeline")}
          >
            Direct activity over time
          </button>
        </div>
      ) : (
        <span className="ge-task-context">Ordered receipt legs</span>
      )}

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
