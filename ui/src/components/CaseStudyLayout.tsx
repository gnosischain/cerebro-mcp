import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Sun, Moon, Copy, Check, ExternalLink } from "lucide-react";
import type { ReportData, ChartSpec, QueryInfo } from "../types";
import { isNumberDisplay } from "../types";
import { ChartCard } from "./ChartCard";
import { useTheme } from "../hooks/useTheme";
import "../themes/case-study.css";

interface Props {
  data: ReportData;
}

interface StepState {
  chartId: string;
  state: string;
}

export function CaseStudyLayout({ data }: Props) {
  const meta = data.case_study_metadata;
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [chartMounts, setChartMounts] = useState<
    { chartId: string; el: HTMLElement; sceneChart?: string }[]
  >([]);
  const [scrollPct, setScrollPct] = useState(0);
  const [activeStep, setActiveStep] = useState<Record<string, StepState>>({});

  // Imperatively set body HTML, then discover chart mount points.
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    root.innerHTML = data.sections_html;

    const mounts: { chartId: string; el: HTMLElement; sceneChart?: string }[] = [];
    root.querySelectorAll<HTMLElement>(".chart-container").forEach((el) => {
      const idAttr = el.id;
      if (idAttr && idAttr.startsWith("chart-")) {
        const chartId = idAttr.slice("chart-".length);
        mounts.push({ chartId, el });
      }
    });
    setChartMounts(mounts);

    // Scroll-progress bar
    const onScroll = () => {
      const h = document.documentElement;
      const max = h.scrollHeight - h.clientHeight;
      if (max <= 0) {
        setScrollPct(0);
      } else {
        setScrollPct(Math.min(100, Math.max(0, (h.scrollTop / max) * 100)));
      }
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [data.sections_html]);

  // IntersectionObserver: reveal bullets when they enter viewport.
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    const items = root.querySelectorAll<HTMLElement>(
      '[data-cs-reveal="true"] li'
    );
    if (items.length === 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
          }
        }
      },
      { threshold: 0.3, rootMargin: "0px 0px -10% 0px" }
    );
    items.forEach((el, i) => {
      el.style.transitionDelay = `${i * 80}ms`;
      obs.observe(el);
    });
    return () => obs.disconnect();
  }, [chartMounts]);

  // IntersectionObserver: track active chart-step per scene chart.
  useEffect(() => {
    const root = bodyRef.current;
    if (!root) return;
    const steps = root.querySelectorAll<HTMLElement>(".cs-step");
    if (steps.length === 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        // Pick the most-visible step per chart id.
        const byChart: Record<string, { step: HTMLElement; ratio: number }> = {};
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          const el = e.target as HTMLElement;
          const chartId = el.dataset.stepChart || "";
          if (!chartId) continue;
          const prev = byChart[chartId];
          if (!prev || e.intersectionRatio > prev.ratio) {
            byChart[chartId] = { step: el, ratio: e.intersectionRatio };
          }
        }
        if (Object.keys(byChart).length === 0) return;
        setActiveStep((prev) => {
          const next = { ...prev };
          let changed = false;
          for (const [chartId, { step }] of Object.entries(byChart)) {
            const state = step.dataset.stepState || "";
            if (!next[chartId] || next[chartId].state !== state) {
              next[chartId] = { chartId, state };
              changed = true;
            }
            // Reflect active class on DOM for CSS.
            const scene = step.closest(".cs-scene");
            if (scene) {
              scene
                .querySelectorAll<HTMLElement>(".cs-step")
                .forEach((s) => s.classList.remove("is-active"));
              step.classList.add("is-active");
            }
          }
          return changed ? next : prev;
        });
      },
      { threshold: [0.5], rootMargin: "-30% 0px -30% 0px" }
    );
    steps.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [chartMounts]);

  const keyPoints = meta?.key_points ?? [];
  const authors = meta?.authors ?? [];
  const category = meta?.category;
  const publishedLabel = useMemo(
    () => formatPublishedDate(meta?.published_date),
    [meta?.published_date]
  );
  const readingMinutes = meta?.reading_minutes ?? null;
  const heroChartId = meta?.hero_chart_id || null;
  const heroImage = meta?.hero_image || null;
  const cta = meta?.cta || null;

  // Separate a hero chart mount from body mounts if explicitly referenced.
  const heroMount = useMemo(() => {
    if (!heroChartId) return null;
    return chartMounts.find((m) => m.chartId === heroChartId) || null;
  }, [chartMounts, heroChartId]);

  return (
    <div className="cs-shell">
      <div
        className="cs-progress no-print"
        style={{ width: `${scrollPct}%` }}
        aria-hidden="true"
      />
      <CaseStudyTopBar fileUri={data.file_uri} />

      <main className="cs-main">
        <header className="cs-hero">
          <div className="cs-hero-meta">
            {category && <div className="cs-category">{category}</div>}
            <h1 className="cs-title">{data.title}</h1>
            {meta?.deck && <p className="cs-deck">{meta.deck}</p>}
            <div className="cs-byline">
              {authors.length > 0 && (
                <span className="cs-authors">{authors.join(", ")}</span>
              )}
              {publishedLabel && (
                <span className="cs-published">{publishedLabel}</span>
              )}
              {readingMinutes != null && (
                <span className="cs-reading">{readingMinutes} min read</span>
              )}
            </div>
          </div>
          {(heroChartId || heroImage) && (
            <div className="cs-hero-visual">
              {heroImage && !heroChartId && (
                <img
                  src={heroImage}
                  alt={meta?.deck || data.title}
                  className="cs-hero-image"
                />
              )}
              {heroChartId && !heroMount && (
                <div id={`chart-${heroChartId}`} className="chart-container" />
              )}
            </div>
          )}
        </header>

        {keyPoints.length > 0 && (
          <section className="cs-keypoints" aria-label="Key points">
            <ol className="cs-keypoints-list">
              {keyPoints.map((p, i) => (
                <li key={i} className="cs-keypoint">
                  <span className="cs-keypoint-num">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="cs-keypoint-text">{p}</span>
                </li>
              ))}
            </ol>
          </section>
        )}

        <article className="cs-body">
          <div ref={bodyRef} className="cs-prose" />

          {chartMounts.map(({ chartId, el }) => {
            const spec = data.charts[chartId];
            if (!spec) return null;
            const title =
              data.queries?.[chartId]?.title ||
              (isNumberDisplay(spec) ? spec.title || "" : "");
            const step = activeStep[chartId];
            return createPortal(
              <ChartHost
                chartId={chartId}
                spec={spec}
                title={title}
                queries={data.queries}
                stepState={step?.state}
              />,
              el,
              chartId
            );
          })}
        </article>

        {cta && (
          <section className="cs-footer-cta" aria-label="Call to action">
            <div className="cs-footer-cta-inner">
              <div className="cs-footer-cta-text">
                <h2 className="cs-footer-cta-heading">Ready to go further?</h2>
                {meta?.deck && (
                  <p className="cs-footer-cta-sub">{meta.deck}</p>
                )}
              </div>
              <a
                href={cta.href}
                target="_blank"
                rel="noopener noreferrer"
                className="cs-footer-cta-btn"
              >
                {cta.label}
              </a>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

interface ChartHostProps {
  chartId: string;
  spec: ChartSpec;
  title: string;
  queries?: Record<string, QueryInfo>;
  stepState?: string;
}

function ChartHost({ chartId, spec, title, queries, stepState }: ChartHostProps) {
  // stepState is surfaced via a data attribute on the wrapper; ChartCard
  // itself does not yet act on it (a future enhancement could re-compute
  // the ECharts option based on the state string).
  return (
    <div data-step-state={stepState || ""}>
      <ChartCard
        chartId={chartId}
        spec={spec}
        title={title}
        sql={queries?.[chartId]?.sql}
      />
    </div>
  );
}

function CaseStudyTopBar({ fileUri }: { fileUri?: string }) {
  const { isDark, toggle } = useTheme();
  const [copied, setCopied] = useState(false);
  const isFileUri = fileUri?.startsWith("file://");
  return (
    <div className="cs-topbar no-print">
      <div className="cs-topbar-inner">
        <div className="cs-topbar-brand">Cerebro · Case Study</div>
        <div className="cs-topbar-actions">
          {fileUri && isFileUri && (
            <button
              className="cs-icon-btn"
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
              className="cs-icon-btn"
              title="Open in browser"
            >
              <ExternalLink size={16} />
            </a>
          )}
          <button className="cs-icon-btn" onClick={toggle} title="Toggle theme">
            {isDark ? <Sun size={16} /> : <Moon size={16} />}
          </button>
        </div>
      </div>
    </div>
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

