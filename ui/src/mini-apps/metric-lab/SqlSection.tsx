// SQL tab: view / edit / re-run the SQL behind each attached dataset.
// The generated SQL is always visible (learning affordance); edits re-run
// through the app-only run_metric_lab_sql tool (full server guard stack).
// One draft per dataset, reseeded whenever that dataset's REVISION changes
// (a builder Run replaced it) — not on SQL text.

import { useEffect, useState } from "react";
import { AsyncButton } from "../shared/AsyncButton";
import { CollapsibleSection } from "../shared/CollapsibleSection";
import type { DatasetDescriptor } from "../shared/miniAppTypes";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

interface SqlSectionProps {
  viewId: string | undefined;
  descriptors: Record<string, DatasetDescriptor>;
  revisions: Record<string, number>;
  allowedDatabases: string[];
  callTool: CallTool;
}

interface Draft {
  text: string;
  database: string;
  seededRevision: number;
}

export function SqlSection({
  viewId,
  descriptors,
  revisions,
  allowedDatabases,
  callTool,
}: SqlSectionProps) {
  const keys = Object.keys(descriptors);
  const [activeKey, setActiveKey] = useState(keys[0] ?? "primary");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const key = keys.includes(activeKey) ? activeKey : (keys[0] ?? "primary");
  const descriptor = descriptors[key];
  const revision = revisions[key] ?? 0;

  // Reseed the draft when the dataset is replaced (builder Run / rerun).
  useEffect(() => {
    if (!descriptor) return;
    setDrafts((prev) => {
      const existing = prev[key];
      if (existing && existing.seededRevision === revision) return prev;
      return {
        ...prev,
        [key]: {
          text: descriptor.sql ?? "",
          database: descriptor.database ?? "dbt",
          seededRevision: revision,
        },
      };
    });
    setError("");
  }, [key, revision, descriptor]);

  if (!descriptor) return null;
  const draft = drafts[key] ?? {
    text: descriptor.sql ?? "",
    database: descriptor.database ?? "dbt",
    seededRevision: revision,
  };
  const dirty = draft.text.trim() !== (descriptor.sql ?? "").trim();

  const run = async () => {
    if (!viewId) return;
    setRunning(true);
    setError("");
    try {
      await callTool("run_metric_lab_sql", {
        view_id: viewId,
        sql: draft.text,
        database: draft.database,
        dataset_key: key,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "SQL run failed");
    } finally {
      setRunning(false);
    }
  };

  return (
    <CollapsibleSection
      title={`SQL${dirty ? " •" : ""}`}
      tone="subtle"
    >
      <div className="mlab-sql">
        <div className="mlab-sql-bar">
          {keys.length > 1 && (
            <select
              className="mlab-sql-dataset"
              title="Dataset whose SQL to view/edit"
              value={key}
              onChange={(e) => setActiveKey(e.target.value)}
            >
              {keys.map((k) => (
                <option key={k} value={k}>
                  {descriptors[k]?.title || k}
                </option>
              ))}
            </select>
          )}
          <select
            className="mlab-sql-db"
            title="Database to run against"
            value={draft.database}
            onChange={(e) =>
              setDrafts((prev) => ({
                ...prev,
                [key]: { ...draft, database: e.target.value },
              }))
            }
          >
            {(allowedDatabases.length ? allowedDatabases : [draft.database]).map(
              (db) => (
                <option key={db} value={db}>
                  {db}
                </option>
              ),
            )}
          </select>
          {dirty && <span className="mlab-dirty">edited — Run to apply</span>}
          <AsyncButton onClick={run} loadingLabel="Running" disabled={running}>
            Run SQL
          </AsyncButton>
        </div>
        <textarea
          className="mlab-sql-editor"
          spellCheck={false}
          value={draft.text}
          rows={Math.min(16, Math.max(5, draft.text.split("\n").length + 1))}
          onChange={(e) =>
            setDrafts((prev) => ({
              ...prev,
              [key]: { ...draft, text: e.target.value },
            }))
          }
        />
        {error && <div className="mlab-error mlab-sql-error">{error}</div>}
        <p className="mlab-sql-hint">
          SELECT/WITH only — the query runs read-only inside the bounded
          sampler. Edited SQL cannot keep bound placeholders like{" "}
          <code>{"{flt:String}"}</code>; inline literal values instead.
        </p>
      </div>
    </CollapsibleSection>
  );
}
