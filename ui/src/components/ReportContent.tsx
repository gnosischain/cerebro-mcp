import type { ReportData, ChartSpec, QueryInfo } from "../types";
import type { HtmlSection } from "../types";
import { ChartCard } from "./ChartCard";

interface SectionContentProps {
  html: string;
  charts: Record<string, ChartSpec>;
  queries?: Record<string, QueryInfo>;
}

/**
 * Given a section's HTML, find chart placeholder divs and grid containers,
 * then render them as React ChartCards while keeping surrounding HTML intact.
 */
function SectionContent({ html, charts, queries }: SectionContentProps) {
  // Split on chart containers AND grid containers
  const parts = html.split(
    /(<div\s+class="chart-grid[^"]*"\s+data-grid-charts="[^"]*"><\/div>|<div\s+id="chart-(chart_\d+)"\s+class="chart-container"><\/div>)/i
  );

  const elements: React.ReactNode[] = [];

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (!part) continue;

    // Check for grid container: <div class="chart-grid chart-grid-N" data-grid-charts="id1,id2,..."></div>
    const gridMatch = part.match(
      /^<div\s+class="(chart-grid[^"]*)"\s+data-grid-charts="([^"]*)"><\/div>$/i
    );
    if (gridMatch) {
      const gridClasses = gridMatch[1];
      const chartIds = gridMatch[2].split(",").filter(Boolean);

      elements.push(
        <div className={gridClasses} key={`grid-${i}`}>
          {chartIds.map((chartId) => {
            const spec = charts[chartId];
            if (!spec) return null;
            const chartData = findChartTitle(chartId, html);
            return (
              <ChartCard
                key={chartId}
                chartId={chartId}
                spec={spec}
                title={chartData}
                sql={queries?.[chartId]?.sql}
              />
            );
          })}
        </div>
      );
      continue;
    }

    // Check for individual chart container
    const chartMatch = part.match(
      /^<div\s+id="chart-(chart_\d+)"\s+class="chart-container"><\/div>$/i
    );

    if (chartMatch) {
      const chartId = chartMatch[1];
      const spec = charts[chartId];
      if (spec) {
        // Extract title from the chart-card wrapper in the previous HTML part
        let chartTitle = "";
        const prevHtml =
          elements.length > 0 ? elements[elements.length - 1] : null;

        if (
          prevHtml &&
          typeof prevHtml === "object" &&
          prevHtml !== null &&
          "props" in prevHtml
        ) {
          const el = prevHtml as React.ReactElement<{
            dangerouslySetInnerHTML?: { __html: string };
          }>;
          const prevStr = el.props.dangerouslySetInnerHTML?.__html;
          if (prevStr) {
            const titleMatch = prevStr.match(
              /<div class="chart-title">(.*?)<\/div>\s*$/
            );
            if (titleMatch) {
              chartTitle = titleMatch[1];
              const cleaned = prevStr.replace(
                /<div class="chart-card">.*?<div class="chart-title">.*?<\/div>\s*$/,
                ""
              );
              elements[elements.length - 1] = (
                <div
                  key={`html-${i}-cleaned`}
                  className="report-html"
                  dangerouslySetInnerHTML={{ __html: cleaned }}
                />
              );
            }
          }
        }

        elements.push(
          <ChartCard
            key={chartId}
            chartId={chartId}
            spec={spec}
            title={chartTitle}
            sql={queries?.[chartId]?.sql}
          />
        );
      }
      // Skip the next part which is the captured group
      i += 1;
      continue;
    }

    // Regular HTML content — wrap in card like charts
    if (part.trim()) {
      elements.push(
        <div
          key={`html-${i}`}
          className="content-card report-html"
          dangerouslySetInnerHTML={{ __html: part }}
        />
      );
    }
  }

  return <>{elements}</>;
}

/** Extract chart title — title comes from the chart spec via Python backend */
function findChartTitle(_chartId: string, _html: string): string {
  return "";
}

interface Props {
  data: ReportData;
  sections: HtmlSection[];
}

export function ReportContent({ data, sections }: Props) {
  return (
    <div className="report-content">
      {sections.map((section, i) => (
        <div key={i} id={`section-${i}`}>
          <SectionContent
            html={section.html}
            charts={data.charts}
            queries={data.queries}
          />
        </div>
      ))}
    </div>
  );
}
