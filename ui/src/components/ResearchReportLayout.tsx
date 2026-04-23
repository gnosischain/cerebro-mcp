import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Sun, Moon, Copy, Check, ExternalLink } from "lucide-react";
import type { ReportData, ChartSpec, QueryInfo } from "../types";
import { isNumberDisplay } from "../types";
import { ChartCard } from "./ChartCard";
import { useTheme } from "../hooks/useTheme";
import "../themes/research.css";

interface Props {
  data: ReportData;
}

interface TocEntry {
  id: string;
  title: string;
}

export function ResearchReportLayout({ data }: Props) {
  const meta = data.research_metadata;
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [tocEntries, setTocEntries] = useState<TocEntry[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [chartMounts, setChartMounts] = useState<
    { chartId: string; el: HTMLElement }[]
  >([]);

  // Set body HTML imperatively once per sections_html, then discover TOC
  // entries and chart mount points. We avoid React's dangerouslySetInnerHTML
  // because subsequent setState re-renders would clobber the DOM nodes that
  // the portals below need to remain stable references.
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    root.innerHTML = data.sections_html;

    const entries: TocEntry[] = [];
    root.querySelectorAll<HTMLHeadingElement>("h2.rr-section-heading").forEach(
      (h) => {
        if (h.id) {
          entries.push({ id: h.id, title: h.textContent?.trim() || h.id });
        }
      }
    );
    setTocEntries(entries);

    const mounts: { chartId: string; el: HTMLElement }[] = [];
    root.querySelectorAll<HTMLElement>(".chart-container").forEach((el) => {
      const idAttr = el.id;
      if (idAttr && idAttr.startsWith("chart-")) {
        const chartId = idAttr.slice("chart-".length);
        mounts.push({ chartId, el });
      }
    });
    setChartMounts(mounts);
  }, [data.sections_html]);

  // Scroll-spy for the TOC.
  useEffect(() => {
    if (tocEntries.length === 0) return;
    const root = bodyRef.current;
    if (!root) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort(
            (a, b) =>
              (a.target as HTMLElement).offsetTop -
              (b.target as HTMLElement).offsetTop
          );
        if (visible.length > 0) {
          setActiveId((visible[0].target as HTMLElement).id);
        }
      },
      { rootMargin: "-30% 0px -55% 0px", threshold: 0 }
    );

    tocEntries.forEach((t) => {
      const el = document.getElementById(t.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [tocEntries]);

  const takeaways = meta?.key_takeaways ?? [];
  const authors = meta?.authors ?? [];
  const footnotes = meta?.footnotes ?? [];
  const category = meta?.category;
  const publishedLabel = useMemo(
    () => formatPublishedDate(meta?.published_date),
    [meta?.published_date]
  );
  const readingMinutes = meta?.reading_minutes ?? null;

  return (
    <div className="rr-shell">
      <ResearchTopBar fileUri={data.file_uri} />

      <main className="rr-main">
        <header className="rr-hero">
          {category && <div className="rr-category">{category}</div>}
          <h1 className="rr-title">{data.title}</h1>
          {meta?.deck && <p className="rr-deck">{meta.deck}</p>}
          <div className="rr-byline">
            {authors.length > 0 && (
              <span className="rr-authors">{authors.join(", ")}</span>
            )}
            {publishedLabel && (
              <span className="rr-published">{publishedLabel}</span>
            )}
            {readingMinutes != null && (
              <span className="rr-reading">{readingMinutes} min read</span>
            )}
          </div>
        </header>

        {takeaways.length > 0 && (
          <section className="rr-takeaways" aria-label="Key takeaways">
            <h2 className="rr-takeaways-heading">Key takeaways</h2>
            <ol className="rr-takeaways-list">
              {takeaways.map((t, i) => (
                <li key={i} className="rr-takeaway">
                  <span className="rr-takeaway-num">{String(i + 1).padStart(2, "0")}</span>
                  <span className="rr-takeaway-text">{t}</span>
                </li>
              ))}
            </ol>
          </section>
        )}

        <div className="rr-grid">
          <ResearchTOC entries={tocEntries} activeId={activeId} />

          <article className="rr-body">
            <div ref={bodyRef} className="rr-prose" />

            {chartMounts.map(({ chartId, el }) => {
              const spec = data.charts[chartId];
              if (!spec) return null;
              const title =
                data.queries?.[chartId]?.title ||
                (isNumberDisplay(spec) ? spec.title || "" : "");
              return createPortal(
                <ChartHost
                  chartId={chartId}
                  spec={spec}
                  title={title}
                  queries={data.queries}
                />,
                el,
                chartId
              );
            })}
          </article>

          <aside className="rr-right-gutter" aria-hidden="true" />
        </div>

        {footnotes.length > 0 && (
          <section className="rr-meta-footnotes" aria-label="Notes">
            <h2 className="rr-footnotes-heading">Notes</h2>
            <ol className="rr-footnote-list">
              {footnotes.map((fn) => (
                <li
                  key={fn.id}
                  id={`fn-meta-${fn.id}`}
                  className="rr-footnote-item"
                >
                  <span className="rr-footnote-num">{fn.id}.</span>{" "}
                  {fn.text}
                </li>
              ))}
            </ol>
          </section>
        )}

        <ResearchCitation data={data} />
      </main>
    </div>
  );
}

interface ChartHostProps {
  chartId: string;
  spec: ChartSpec;
  title: string;
  queries?: Record<string, QueryInfo>;
}

function ChartHost({ chartId, spec, title, queries }: ChartHostProps) {
  return (
    <ChartCard
      chartId={chartId}
      spec={spec}
      title={title}
      sql={queries?.[chartId]?.sql}
    />
  );
}

function ResearchTOC({
  entries,
  activeId,
}: {
  entries: TocEntry[];
  activeId: string;
}) {
  if (entries.length === 0) {
    return <aside className="rr-toc rr-toc--empty" aria-hidden="true" />;
  }
  return (
    <aside className="rr-toc" aria-label="Table of contents">
      <div className="rr-toc-inner">
        <div className="rr-toc-label">Contents</div>
        <ol className="rr-toc-list">
          {entries.map((e) => (
            <li
              key={e.id}
              className={
                "rr-toc-item" +
                (activeId === e.id ? " rr-toc-item--active" : "")
              }
            >
              <a href={`#${e.id}`}>{e.title}</a>
            </li>
          ))}
        </ol>
      </div>
    </aside>
  );
}

function ResearchTopBar({ fileUri }: { fileUri?: string }) {
  const { isDark, toggle } = useTheme();
  const [copied, setCopied] = useState(false);
  const isFileUri = fileUri?.startsWith("file://");
  return (
    <div className="rr-topbar no-print">
      <div className="rr-topbar-inner">
        <div className="rr-topbar-brand">Cerebro · Research</div>
        <div className="rr-topbar-actions">
          {fileUri && isFileUri && (
            <button
              className="rr-icon-btn"
              title="Copy report path"
              onClick={() => {
                navigator.clipboard.writeText(fileUri);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              }}
            >
              {copied ? <Check size={16} /> : <Copy size={16} />}
            </button>
          )}
          {fileUri && !isFileUri && (
            <a
              href={fileUri}
              target="_blank"
              rel="noopener noreferrer"
              className="rr-icon-btn"
              title="Open in browser"
            >
              <ExternalLink size={16} />
            </a>
          )}
          <button className="rr-icon-btn" onClick={toggle} title="Toggle theme">
            {isDark ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
      </div>
    </div>
  );
}

function ResearchCitation({ data }: { data: ReportData }) {
  const meta = data.research_metadata;
  const authors = meta?.authors ?? [];
  const year = (meta?.published_date ?? "").slice(0, 4) || "n.d.";
  const authorStr =
    authors.length === 0
      ? "Cerebro Research"
      : authors.length === 1
        ? authors[0]
        : authors.length === 2
          ? `${authors[0]} & ${authors[1]}`
          : `${authors[0]} et al.`;
  return (
    <section className="rr-citation" aria-label="Citation">
      <h2 className="rr-citation-heading">Cite this report</h2>
      <p className="rr-citation-body">
        {authorStr} ({year}). <em>{data.title}</em>. Cerebro Research.
      </p>
    </section>
  );
}

function formatPublishedDate(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}
