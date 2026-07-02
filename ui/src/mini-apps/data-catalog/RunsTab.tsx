import { useEffect, useState } from "react";
import { AvailabilityNotice, KeyVals, StatusDot, fmtInt, testTone } from "./components";
import type { CallTool, RunConfig, RunState } from "./types";

interface Props {
  entityName: string;
  callTool: CallTool;
}

function asText(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.join(", ");
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

export function RunsTab({ entityName, callTool }: Props) {
  const [config, setConfig] = useState<RunConfig | null>(null);
  const [runs, setRuns] = useState<RunState | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    Promise.all([
      callTool<RunConfig>("catalog_run_config", { name: entityName }),
      callTool<RunState>("catalog_run_state", { name: entityName, history: 10 }),
    ])
      .then(([c, r]) => {
        if (!alive) return;
        setConfig(c);
        setRuns(r);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [callTool, entityName]);

  if (loading) return <div className="dc-empty">Loading run info…</div>;

  return (
    <div className="dc-group">
      <div>
        <div className="dc-group-title">Run configuration</div>
        {config?.available ? (
          <KeyVals
            rows={[
              ["materialization", asText(config.materialization)],
              ["strategy", asText(config.incremental_strategy)],
              ["unique key", asText(config.unique_key)],
              ["partition by", asText(config.partition_by)],
              ["on schema change", asText(config.on_schema_change)],
              ["full refresh", asText(config.full_refresh)],
            ]}
          />
        ) : (
          <div className="dc-empty">Run configuration unavailable.</div>
        )}
      </div>

      <div>
        <div className="dc-group-title">Run history</div>
        {!runs?.available ? (
          <AvailabilityNotice title="Run history unavailable" reason={runs?.reason} />
        ) : (runs.history ?? []).length === 0 ? (
          <div className="dc-empty">No runs recorded.</div>
        ) : (
          <div className="dc-table-wrap">
            <table className="dc-table">
              <thead>
                <tr><th>Status</th><th>Completed</th><th>Duration</th><th>Rows</th><th>Mode</th></tr>
              </thead>
              <tbody>
                {(runs.history ?? []).map((r, i) => (
                  <tr key={i}>
                    <td><StatusDot tone={testTone(r.status)} /> {r.status}</td>
                    <td>{asText(r.completed_at)}</td>
                    <td>{r.execution_time != null ? `${Number(r.execution_time).toFixed(2)}s` : "—"}</td>
                    <td>{fmtInt(r.rows_affected)}</td>
                    <td>{r.full_refresh ? "full" : "incr"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
