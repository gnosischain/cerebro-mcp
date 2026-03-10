import type { ReportData } from "./types";

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
