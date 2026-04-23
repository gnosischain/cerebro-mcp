import type { ReportData } from "./types";

function renderMarkdownForDev(md: string): string {
  // Very small subset of the Python _markdown_to_html output in research mode.
  // Only used in `npm run dev` — production bundles receive server-rendered HTML.
  const lines = md.split("\n");
  const out: string[] = [];
  let para: string[] = [];
  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${para.join(" ")}</p>`);
      para = [];
    }
  };
  const slug = (t: string) =>
    t
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const s = line.trim();
    if (!s) {
      flushPara();
      i++;
      continue;
    }
    if (s.startsWith("## ")) {
      flushPara();
      const t = s.slice(3);
      out.push(
        `<h2 id="${slug(t)}" class="rr-section-heading">${t}</h2>`
      );
      i++;
      continue;
    }
    if (s.startsWith("### ")) {
      flushPara();
      out.push(`<h3>${s.slice(4)}</h3>`);
      i++;
      continue;
    }
    const fig = s.match(/^\{\{figure:(\w+)(?:\s+(.*))?\}\}$/);
    if (fig) {
      flushPara();
      const attrs: Record<string, string> = {};
      (fig[2] || "").replace(/(\w+)\s*=\s*"([^"]*)"/g, (_m, k, v) => {
        attrs[k] = v;
        return "";
      });
      const cap = attrs.caption
        ? `<figcaption class="rr-figure-caption">${attrs.caption}${attrs.source ? ` <span class="rr-figure-source">Source: ${attrs.source}</span>` : ""}</figcaption>`
        : "";
      out.push(
        `<figure class="rr-figure"><div class="chart-card rr-figure-chart"><div id="chart-${fig[1]}" class="chart-container"></div></div>${cap}</figure>`
      );
      i++;
      continue;
    }
    if (s === "{{pullquote}}") {
      flushPara();
      const buf: string[] = [];
      i++;
      while (i < lines.length && lines[i].trim() !== "{{/pullquote}}") {
        buf.push(lines[i]);
        i++;
      }
      i++;
      out.push(
        `<blockquote class="rr-pullquote"><p>${buf.join(" ")}</p></blockquote>`
      );
      continue;
    }
    const calloutOpen = s.match(/^\{\{callout\s+kind=([A-Za-z0-9_-]+)\}\}$/);
    if (calloutOpen) {
      flushPara();
      const kind = calloutOpen[1];
      const buf: string[] = [];
      i++;
      while (i < lines.length && lines[i].trim() !== "{{/callout}}") {
        buf.push(lines[i]);
        i++;
      }
      i++;
      out.push(
        `<aside class="rr-callout rr-callout--${kind}"><p>${buf.join(" ")}</p></aside>`
      );
      continue;
    }
    para.push(s);
    i++;
  }
  flushPara();
  return out.join("\n");
}

const RESEARCH_MARKDOWN = `## Why on-chain yield is consolidating

For most of 2024 and 2025, stablecoin yields across DeFi looked like a long tail — dozens of pools, hundreds of strategies, and enough variance that spreadsheets and Dune dashboards were the way you navigated them. That tail is now shortening.

## What the data shows

{{figure:chart_1 caption="Weekly TVL across the five largest stablecoin yield venues." source="dbt-cerebro / Dune"}}

Three venues — Curve, Ethena, and Sky — now account for **68%** of stablecoin TVL earning yield above the risk-free rate, up from 41% a year ago. The rest of the table didn't shrink in absolute terms; it just grew more slowly.

{{callout kind=key_takeaway}}
Concentration is not a byproduct of outflows. Smaller venues kept their deposits; large venues simply grew faster. This is compounding, not competition.
{{/callout}}

{{pullquote}}
The headline isn't that yields fell — it's that the *variance* of yields fell.
{{/pullquote}}

## Methods

We pulled daily deposits, redemptions, and realised yield per pool from the \`fct_stablecoin_yield__pool_daily\` model for the twelve months ending 2026-04-15. Venues were ranked by TVL-weighted median yield across the period.

Assumptions worth flagging[^1]: CEX yield products are excluded, and "Ethena" here aggregates sUSDe and USDtb.

## Implications

If the trend holds, the next leg of growth is unlikely to come from discovering a new venue — it's more likely to come from re-pricing risk inside the three that already dominate. That shifts the useful research question from "which pool?" to "at what point does concentration itself start to matter?"

[^1]: Data through 2026-04-15. Cross-chain bridges are counted at origin.
`;

export const DEV_RESEARCH_REPORT_DATA: ReportData = {
  title: "The quiet consolidation of on-chain yield",
  timestamp: "2026-04-21T14:30:00Z",
  presentation_mode: "research",
  research_metadata: {
    deck: "How three venues quietly absorbed the bulk of stablecoin yield TVL in under a year — and why that changes the next research question.",
    authors: ["Cerebro Research"],
    published_date: "2026-04-21",
    category: "DeFi Research",
    reading_minutes: 7,
    key_takeaways: [
      "Three venues now hold 68% of above-risk-free stablecoin TVL, up from 41% a year ago.",
      "Smaller pools didn't shrink; the top of the distribution simply grew faster.",
      "The useful forward-looking question shifts from discovery to concentration risk.",
    ],
    footnotes: [
      { id: "2", text: "Data excludes CEX-side yield products." },
    ],
  },
  charts: {
    chart_1: {
      tooltip: { trigger: "axis" },
      legend: { data: ["Curve", "Ethena", "Sky"], top: 0 },
      grid: { left: "3%", right: "4%", bottom: "10%", top: "40", containLabel: true },
      xAxis: {
        type: "category",
        data: ["W-48", "W-44", "W-40", "W-36", "W-32", "W-28", "W-24", "W-20", "W-16", "W-12", "W-8", "W-4", "Now"],
        boundaryGap: false,
      },
      yAxis: { type: "value", name: "TVL ($B)" },
      series: [
        { name: "Curve", type: "line", smooth: true, data: [4.1, 4.3, 4.5, 4.8, 5.0, 5.2, 5.5, 5.7, 5.9, 6.2, 6.5, 6.8, 7.1] },
        { name: "Ethena", type: "line", smooth: true, data: [0.8, 1.1, 1.5, 1.9, 2.4, 3.1, 3.8, 4.5, 5.0, 5.4, 5.8, 6.1, 6.4] },
        { name: "Sky", type: "line", smooth: true, data: [2.0, 2.1, 2.2, 2.3, 2.5, 2.7, 3.0, 3.3, 3.5, 3.7, 3.9, 4.0, 4.2] },
      ],
    },
  },
  sections_html: renderMarkdownForDev(RESEARCH_MARKDOWN),
};


/**
 * Mock report data for local HMR development.
 * Run `npm run dev` to see the report at localhost:5173 without the MCP server.
 */
export const DEV_REPORT_DATA: ReportData = {
  title: "Gnosis Chain -- Weekly Report (Mar 3-9, 2026)",
  timestamp: "2026-03-10T12:00:00Z",
  charts: {
    chart_1: {
      title: {},
      tooltip: { trigger: "axis" },
      legend: { data: ["Transactions"], top: 0, type: "scroll" },
      grid: {
        left: "3%",
        right: "4%",
        bottom: "10%",
        top: "40",
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: [
          "2026-03-03",
          "2026-03-04",
          "2026-03-05",
          "2026-03-06",
          "2026-03-07",
          "2026-03-08",
          "2026-03-09",
        ],
        boundaryGap: false,
      },
      yAxis: { type: "value" },
      series: [
        {
          name: "Transactions",
          type: "line",
          data: [125432, 131205, 128903, 135678, 142310, 119876, 127500],
          smooth: true,
        },
      ],
    },
    chart_2: {
      title: {},
      tooltip: { trigger: "axis" },
      legend: {
        data: ["Nethermind", "Erigon", "Geth"],
        top: 0,
        type: "scroll",
      },
      grid: {
        left: "3%",
        right: "4%",
        bottom: "10%",
        top: "40",
        containLabel: true,
      },
      xAxis: {
        type: "category",
        data: [
          "2026-03-03",
          "2026-03-04",
          "2026-03-05",
          "2026-03-06",
          "2026-03-07",
          "2026-03-08",
          "2026-03-09",
        ],
        boundaryGap: false,
      },
      yAxis: { type: "value" },
      series: [
        {
          name: "Nethermind",
          type: "line",
          data: [42, 43, 42, 44, 43, 43, 44],
          smooth: true,
          areaStyle: { opacity: 0.15 },
        },
        {
          name: "Erigon",
          type: "line",
          data: [35, 34, 35, 33, 34, 34, 33],
          smooth: true,
          areaStyle: { opacity: 0.15 },
        },
        {
          name: "Geth",
          type: "line",
          data: [23, 23, 23, 23, 23, 23, 23],
          smooth: true,
          areaStyle: { opacity: 0.15 },
        },
      ],
    },
    chart_3: {
      type: "numberDisplay",
      title: "Active Validators",
      value: 1842,
      change: {
        value: 5.4,
        direction: "positive",
        label: "vs prior week",
        format: "formatNumber",
      },
    },
    chart_4: {
      tooltip: { trigger: "item" },
      legend: { top: 0, type: "scroll" },
      series: [
        {
          type: "pie",
          radius: ["40%", "70%"],
          data: [
            { name: "Nethermind", value: 44 },
            { name: "Erigon", value: 33 },
            { name: "Geth", value: 23 },
          ],
        },
      ],
    },
  },
  sections_html: `
<h2>Executive Summary</h2>
<p>Gnosis Chain maintained strong network performance during the week of March 3-9, 2026, with an average of <strong>130,129 daily transactions</strong>. Validator participation remained stable at <strong>1,842 active validators</strong>.</p>
<blockquote><strong>Highlight:</strong> Transaction volume peaked on March 7 with 142,310 transactions, a 13.5% increase from the weekly low.</blockquote>
<div class="chart-card"><div class="chart-title">Active Validators</div><div id="chart-chart_3" class="chart-container"></div></div>

<h2>Transaction Activity</h2>
<p>Daily transactions showed consistent activity throughout the week, with a notable peak mid-week.</p>
<div class="chart-card"><div class="chart-title">Daily Transactions</div><div id="chart-chart_1" class="chart-container"></div></div>
<table>
<thead><tr><th>Date</th><th>Transactions</th><th>Change</th></tr></thead>
<tbody>
<tr><td>2026-03-03</td><td>125,432</td><td>--</td></tr>
<tr><td>2026-03-04</td><td>131,205</td><td>+4.6%</td></tr>
<tr><td>2026-03-05</td><td>128,903</td><td>-1.8%</td></tr>
<tr><td>2026-03-06</td><td>135,678</td><td>+5.3%</td></tr>
<tr><td>2026-03-07</td><td>142,310</td><td>+4.9%</td></tr>
<tr><td>2026-03-08</td><td>119,876</td><td>-15.8%</td></tr>
<tr><td>2026-03-09</td><td>127,500</td><td>+6.4%</td></tr>
</tbody>
</table>

<h2>Client Diversity</h2>
<p>Execution client distribution remained well-balanced, with <strong>Nethermind</strong> leading at 44% share.</p>
<div class="chart-card"><div class="chart-title">Client Distribution (%)</div><div id="chart-chart_4" class="chart-container"></div></div>
<div class="chart-card"><div class="chart-title">Client Share Over Time (%)</div><div id="chart-chart_2" class="chart-container"></div></div>

<h2>Key Insights</h2>
<ul>
<li><strong>Transaction Growth:</strong> Mid-week surge of +13.5% indicates increased DeFi activity</li>
<li><strong>Validator Stability:</strong> 1,842 active validators with no significant churn</li>
<li><strong>Client Health:</strong> No single client exceeds 50% -- Nakamoto coefficient remains strong</li>
<li><strong>Weekend Dip:</strong> Typical 15.8% decrease on Saturday, recovering by Sunday</li>
</ul>
`,
};
