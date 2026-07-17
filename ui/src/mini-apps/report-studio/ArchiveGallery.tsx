// Report archive gallery: search + kind filter + sort, a card grid over the
// filename-derived metadata (cheap), pagination via offset. Card click opens
// the native preview.

import { useEffect, useState } from "react";
import { MaSearchInput } from "../shared/MaSearchInput";
import { SegmentedControl } from "../shared/SegmentedControl";
import { useDebouncedValue } from "../shared/useDebouncedValue";
import type { ArchivePage } from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

const KIND_LABEL: Record<string, string> = {
  "": "All",
  report: "Dashboards",
  research: "Research",
  case_study: "Case studies",
};

function formatDate(epochSeconds: number): string {
  try {
    return new Date(epochSeconds * 1000).toISOString().slice(0, 16).replace("T", " ");
  } catch {
    return "";
  }
}

interface ArchiveGalleryProps {
  initial: ArchivePage;
  callTool: CallTool;
  onOpen: (reportRef: string) => void;
  /** Bumped by the parent after delete/rename/compose to force a refetch. */
  refreshNonce: number;
}

export function ArchiveGallery({
  initial,
  callTool,
  onOpen,
  refreshNonce,
}: ArchiveGalleryProps) {
  const [page, setPage] = useState<ArchivePage>(initial);
  const [query, setQuery] = useState(initial.query ?? "");
  const [kind, setKind] = useState(initial.kind ?? "");
  const [sort, setSort] = useState(initial.sort ?? "newest");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const debouncedQuery = useDebouncedValue(query, 250);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    callTool<ArchivePage>("list_report_archive", {
      query: debouncedQuery,
      kind,
      sort,
      offset,
      limit: initial.limit || 50,
    })
      .then((result) => {
        if (!cancelled && result) setPage(result);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Archive load failed");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery, kind, sort, offset, refreshNonce]);

  const limit = page.limit || 50;
  const hasPrev = offset > 0;
  const hasNext = offset + limit < page.total;

  return (
    <section className="rst-archive" aria-label="Report archive">
      <div className="rst-archive-bar">
        <MaSearchInput
          value={query}
          onChange={(v) => {
            setQuery(v);
            setOffset(0);
          }}
          onSubmit={() => setOffset(0)}
          placeholder="Search reports — filename, id…"
          ariaLabel="Search the report archive"
        />
        <SegmentedControl<string>
          ariaLabel="Filter by report kind"
          size="sm"
          value={kind}
          onChange={(k) => {
            setKind(k);
            setOffset(0);
          }}
          options={Object.entries(KIND_LABEL).map(([value, label]) => ({ value, label }))}
        />
        <button
          type="button"
          className="rst-toggle"
          title="Toggle sort order"
          onClick={() => setSort(sort === "newest" ? "oldest" : "newest")}
        >
          {sort === "newest" ? "newest first" : "oldest first"}
        </button>
      </div>

      {error && <div className="rst-error">{error}</div>}
      {page.warning_count > 0 && (
        <div className="rst-hint">
          {page.warning_count} file(s) could not be read and were skipped.
        </div>
      )}

      {page.reports.length === 0 && !loading && (
        <div className="rst-empty">
          No reports {debouncedQuery || kind ? "match the filter" : "in the archive yet"} —
          ask the agent to generate one.
        </div>
      )}

      <div className="rst-cards">
        {page.reports.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className="rst-card"
            onClick={() => onOpen(entry.id)}
            title={entry.filename}
          >
            <span className={`rst-kind rst-kind--${entry.kind}`}>
              {entry.kind === "case_study" ? "case study" : entry.kind}
            </span>
            <span className="rst-card-title">{entry.title_hint || entry.short_id}</span>
            <span className="rst-card-meta">
              {formatDate(entry.created_utc)} · {entry.size_kb.toLocaleString()} KB ·{" "}
              <code>{entry.short_id}</code>
            </span>
          </button>
        ))}
      </div>

      {(hasPrev || hasNext) && (
        <div className="rst-pager">
          <button
            type="button"
            className="rst-toggle"
            disabled={!hasPrev}
            onClick={() => setOffset(Math.max(0, offset - limit))}
          >
            ← newer
          </button>
          <span className="rst-pager-info">
            {offset + 1}–{Math.min(offset + limit, page.total)} of {page.total}
          </span>
          <button
            type="button"
            className="rst-toggle"
            disabled={!hasNext}
            onClick={() => setOffset(offset + limit)}
          >
            older →
          </button>
        </div>
      )}
    </section>
  );
}
