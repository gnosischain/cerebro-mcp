// Native in-app report preview: renders the embedded report-data payload
// (charts as ECharts, prose as HTML) — works identically over the ext-apps
// bridge and the standalone HTTP mode, unlike an iframe to /reports/{id}.
//
// case_study payloads are NOT run through parseHtmlSections — their
// scene/visual wrappers produce malformed fragments when split naively; they
// get a flattened chart-list preview with a prominent "open full report".

import { useMemo, useState } from "react";
import { ChartCard } from "../../components/ChartCard";
import { ReportContent } from "../../components/ReportContent";
import { parseHtmlSections } from "../../utils/parseHtmlSections";
import type { ReportData } from "../../types";
import type { ReportEntry } from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

interface ExportInfo {
  ok: boolean;
  error?: string;
  download_url?: string | null;
  path?: string;
  hint?: string;
}

interface ReportPreviewProps {
  entry: ReportEntry;
  mutationsEnabled: boolean;
  callTool: CallTool;
  openLink: (url: string) => Promise<boolean>;
  onBack: () => void;
  /** After a successful delete: go back + refresh the archive. */
  onDeleted: () => void;
  /** After a successful rename: reload this entry + refresh the archive. */
  onRenamed: (reportRef: string) => void;
}

/** Best URL for opening the full report. In standalone mode derive a
 * same-origin /reports/{id} URL (the server-side link helper falls back to
 * file:// on loopback hosts, which a browser tab can't always open). */
function openUrl(entry: ReportEntry): string {
  if (typeof window !== "undefined" && window.__MINI_APP_API__) {
    const token = window.__MINI_APP_TOKEN__;
    return (
      `${window.location.origin}/reports/${entry.id}` +
      (token ? `?token=${encodeURIComponent(token)}` : "")
    );
  }
  return entry.file.link;
}

export function ReportPreview({
  entry,
  mutationsEnabled,
  callTool,
  openLink,
  onBack,
  onDeleted,
  onRenamed,
}: ReportPreviewProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameTo, setRenameTo] = useState(entry.title);
  const [confirmingDelete, setConfirmingDelete] = useState<{
    id: string;
    filename: string;
  } | null>(null);
  const [exportInfo, setExportInfo] = useState<ExportInfo | null>(null);
  const [linkShown, setLinkShown] = useState("");

  const isCaseStudy = entry.kind === "case_study";
  const sections = useMemo(
    () => (isCaseStudy ? [] : parseHtmlSections(entry.sections_html || "")),
    [entry.sections_html, isCaseStudy],
  );
  const reportData: ReportData = useMemo(
    () => ({
      title: entry.title,
      timestamp: entry.timestamp ?? "",
      subtitle: entry.subtitle,
      charts: entry.charts ?? {},
      sections_html: entry.sections_html ?? "",
      queries: entry.queries,
    }),
    [entry],
  );

  const doOpen = async () => {
    const url = openUrl(entry);
    const opened = await openLink(url);
    if (!opened) setLinkShown(url); // host can't open links -> copyable
  };

  const doExportInfo = async () => {
    setError("");
    try {
      const info = await callTool<ExportInfo>("get_report_export_info", {
        report_ref: entry.id,
      });
      setExportInfo(info);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Export info failed");
    }
  };

  const doDelete = async () => {
    setBusy(true);
    setError("");
    try {
      if (!confirmingDelete) {
        const first = await callTool<{
          ok: boolean;
          needs_confirm?: boolean;
          id?: string;
          filename?: string;
          error?: string;
        }>("delete_report_archive_entry", { report_ref: entry.id });
        if (first?.needs_confirm && first.id) {
          setConfirmingDelete({ id: first.id, filename: first.filename ?? "" });
        } else if (first && !first.ok) {
          setError(first.error ?? "Delete failed");
        }
        return;
      }
      // Confirm with the FULL resolved id from the first call.
      const second = await callTool<{ ok: boolean; error?: string }>(
        "delete_report_archive_entry",
        { report_ref: confirmingDelete.id, confirm: true },
      );
      if (second?.ok) {
        onDeleted();
      } else {
        setError(second?.error ?? "Delete failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  };

  const doRename = async () => {
    setBusy(true);
    setError("");
    try {
      const result = await callTool<{ ok: boolean; id?: string; error?: string }>(
        "rename_report_archive_entry",
        { report_ref: entry.id, title: renameTo },
      );
      if (result?.ok && result.id) {
        setRenaming(false);
        onRenamed(result.id);
      } else {
        setError(result?.error ?? "Rename failed");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rename failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="rst-preview" aria-label="Report preview">
      <div className="rst-preview-bar">
        <button type="button" className="rst-toggle" onClick={onBack}>
          ← archive
        </button>
        <div className="rst-preview-actions">
          <button type="button" className="rst-toggle" onClick={doOpen}>
            Open full report ↗
          </button>
          <button type="button" className="rst-toggle" onClick={doExportInfo}>
            Export…
          </button>
          {mutationsEnabled && (
            <>
              <button
                type="button"
                className="rst-toggle"
                onClick={() => {
                  setRenaming((r) => !r);
                  setRenameTo(entry.title);
                }}
              >
                Rename
              </button>
              <button
                type="button"
                className="rst-toggle rst-toggle--danger"
                disabled={busy}
                onClick={doDelete}
              >
                Delete
              </button>
            </>
          )}
        </div>
      </div>

      {error && <div className="rst-error">{error}</div>}
      {linkShown && (
        <div className="rst-hint">
          This host cannot open links — copy it: <code>{linkShown}</code>
        </div>
      )}

      {confirmingDelete && (
        <div className="rst-confirm" role="alertdialog">
          Delete <code>{confirmingDelete.filename}</code> permanently?
          <button
            type="button"
            className="rst-toggle rst-toggle--danger"
            disabled={busy}
            onClick={doDelete}
          >
            Yes, delete
          </button>
          <button
            type="button"
            className="rst-toggle"
            onClick={() => setConfirmingDelete(null)}
          >
            Cancel
          </button>
        </div>
      )}

      {renaming && (
        <div className="rst-rename">
          <input
            value={renameTo}
            maxLength={200}
            onChange={(e) => setRenameTo(e.target.value)}
            placeholder="New report title"
          />
          <button
            type="button"
            className="rst-toggle"
            disabled={busy || !renameTo.trim()}
            onClick={doRename}
          >
            Save
          </button>
        </div>
      )}

      {exportInfo && (
        <div className="rst-hint">
          {exportInfo.ok ? (
            <>
              {exportInfo.download_url ? (
                <>
                  Download: <code>{exportInfo.download_url}</code> ·{" "}
                </>
              ) : null}
              Path: <code>{exportInfo.path}</code> · {exportInfo.hint}
            </>
          ) : (
            exportInfo.error
          )}
        </div>
      )}

      <header className="rst-preview-head">
        <span className={`rst-kind rst-kind--${entry.kind}`}>
          {entry.kind === "case_study" ? "case study" : entry.kind}
        </span>
        <h1>{entry.title}</h1>
        {entry.subtitle && <p className="rst-preview-deck">{entry.subtitle}</p>}
        {entry.timestamp && <p className="rst-preview-ts">{entry.timestamp}</p>}
      </header>

      {entry.kind !== "report" && (
        <div className="rst-hint">
          Layout simplified for the preview — open the full report for the
          designed {entry.kind === "case_study" ? "scrollytelling" : "essay"}{" "}
          layout.
        </div>
      )}

      {isCaseStudy ? (
        // Flattened preview: chart list only (scene markup cannot be split
        // safely) + the open action above.
        <div className="rst-flat-charts">
          {Object.entries(entry.charts ?? {}).map(([chartId, spec]) => (
            <ChartCard
              key={chartId}
              chartId={chartId}
              spec={spec}
              title={entry.queries?.[chartId]?.title}
              sql={entry.queries?.[chartId]?.sql}
              sourceModel={entry.queries?.[chartId]?.source_model}
            />
          ))}
          {Object.keys(entry.charts ?? {}).length === 0 && (
            <div className="rst-empty">This case study has no chart payloads.</div>
          )}
        </div>
      ) : (
        <ReportContent data={reportData} sections={sections} />
      )}
    </section>
  );
}
