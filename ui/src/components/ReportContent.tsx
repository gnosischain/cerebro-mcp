import type { ReportData, ChartSpec, QueryInfo } from "../types";
import type { HtmlSection } from "../types";
import { ChartCard } from "./ChartCard";

interface SectionContentProps {
  html: string;
  charts: Record<string, ChartSpec>;
  queries?: Record<string, QueryInfo>;
}

/**
 * Given a section's HTML, find chart placeholder divs and render them
 * as React ChartCards, while keeping the surrounding HTML intact.
 */
function SectionContent({ html, charts, queries }: SectionContentProps) {
  // Split HTML on chart container divs: <div id="chart-chart_N" class="chart-container"></div>
  const parts = html.split(
    /(<div\s+id="chart-(chart_\d+)"\s+class="chart-container"><\/div>)/i
  );

  const elements: React.ReactNode[] = [];

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (!part) continue;

    // Check if this part matches a chart container pattern
    const chartMatch = part.match(
      /^<div\s+id="chart-(chart_\d+)"\s+class="chart-container"><\/div>$/i
    );

    if (chartMatch) {
      const chartId = chartMatch[1];
      const spec = charts[chartId];
      if (spec) {
        // Find the chart-card wrapper div and chart-title before this container
        const prevHtml =
          elements.length > 0 ? elements[elements.length - 1] : null;
        let chartTitle = "";

        // Extract title from the chart-card wrapper in the previous HTML part
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
              // Remove the chart-card wrapper from the previous HTML
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
            database={queries?.[chartId]?.database}
          />
        );
      }
      // Skip the next part which is the captured group
      i += 1;
      continue;
    }

    // Regular HTML content
    if (part.trim()) {
      elements.push(
        <div
          key={`html-${i}`}
          className="report-html"
          dangerouslySetInnerHTML={{ __html: part }}
        />
      );
    }
  }

  return <>{elements}</>;
}

interface Props {
  data: ReportData;
  sections: HtmlSection[];
  activeIndex: number;
}

export function ReportContent({ data, sections, activeIndex }: Props) {
  return (
    <div className="report-content">
      {sections.map((section, i) => (
        <div
          key={i}
          style={{ display: i === activeIndex ? "block" : "none" }}
        >
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
