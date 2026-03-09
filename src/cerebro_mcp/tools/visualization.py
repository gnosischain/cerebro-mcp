import json
import os
import re
import threading
from datetime import date, datetime

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.config import settings
from cerebro_mcp.tools.query import truncate_response


GNOSIS_WATERMARK = {
    "type": "image",
    "id": "watermark",
    "z": 1000,
    "bounding": "raw",
    "style": {
        "image": (
            "https://raw.githubusercontent.com/gnosis/gnosis-brand-assets/"
            "main/Brand%20Assets/Logo/RGB/Owl_Logomark_Black_RGB.png"
        ),
        "width": 25,
        "height": 25,
        "opacity": 0.1,
    },
    "right": 10,
    "bottom": 10,
}

# --- Chart Registry ---
_chart_registry: dict[str, dict] = {}
_chart_counter = 0
_chart_lock = threading.Lock()


def _next_chart_id() -> str:
    global _chart_counter
    with _chart_lock:
        _chart_counter += 1
        return f"chart_{_chart_counter}"


def _serialize_value(val):
    """Convert ClickHouse values to JSON-serializable types."""
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


def _build_col_index(columns: list[str]) -> dict[str, int]:
    """Map column names to their indices."""
    return {name: i for i, name in enumerate(columns)}


def _extract_column(rows: list, index: int) -> list:
    """Extract a column from rows and serialize values."""
    return [_serialize_value(row[index]) for row in rows]


def _build_line_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
    area: bool = False,
) -> dict:
    """Build ECharts option for line/area charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    if series_field and series_field in col_index:
        series_idx = col_index[series_field]
        # Group by series
        series_data: dict[str, dict] = {}
        x_values_set: list[str] = []
        for row in rows:
            x_val = _serialize_value(row[x_idx])
            s_val = str(_serialize_value(row[series_idx]))
            y_val = _serialize_value(row[y_idx])
            if x_val not in x_values_set:
                x_values_set.append(x_val)
            if s_val not in series_data:
                series_data[s_val] = {}
            series_data[s_val][x_val] = y_val

        series_list = []
        for s_name, data_map in series_data.items():
            s = {
                "name": s_name,
                "type": "line",
                "data": [data_map.get(x, None) for x in x_values_set],
                "smooth": True,
                "symbolSize": 2,
            }
            if area:
                s["areaStyle"] = {"opacity": 0.3}
            series_list.append(s)

        legend_data = list(series_data.keys())
        x_values = x_values_set
    else:
        x_values = _extract_column(rows, x_idx)
        y_values = _extract_column(rows, y_idx)
        s = {
            "name": y_field,
            "type": "line",
            "data": y_values,
            "smooth": True,
            "symbolSize": 2,
        }
        if area:
            s["areaStyle"] = {"opacity": 0.3}
        series_list = [s]
        legend_data = [y_field]

    return {
        "title": {"text": title} if title else {},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend_data},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "containLabel": True},
        "xAxis": {"type": "category", "data": x_values, "boundaryGap": False},
        "yAxis": {"type": "value"},
        "series": series_list,
        "graphic": [GNOSIS_WATERMARK],
    }


def _build_bar_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for bar charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    if series_field and series_field in col_index:
        series_idx = col_index[series_field]
        series_data: dict[str, dict] = {}
        x_values_set: list[str] = []
        for row in rows:
            x_val = _serialize_value(row[x_idx])
            s_val = str(_serialize_value(row[series_idx]))
            y_val = _serialize_value(row[y_idx])
            if x_val not in x_values_set:
                x_values_set.append(x_val)
            if s_val not in series_data:
                series_data[s_val] = {}
            series_data[s_val][x_val] = y_val

        series_list = [
            {
                "name": s_name,
                "type": "bar",
                "data": [data_map.get(x, None) for x in x_values_set],
            }
            for s_name, data_map in series_data.items()
        ]
        legend_data = list(series_data.keys())
        x_values = x_values_set
    else:
        x_values = _extract_column(rows, x_idx)
        y_values = _extract_column(rows, y_idx)
        series_list = [{"name": y_field, "type": "bar", "data": y_values}]
        legend_data = [y_field]

    return {
        "title": {"text": title} if title else {},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend_data},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "containLabel": True},
        "xAxis": {"type": "category", "data": x_values},
        "yAxis": {"type": "value"},
        "series": series_list,
        "graphic": [GNOSIS_WATERMARK],
    }


def _build_pie_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    title: str,
) -> dict:
    """Build ECharts option for pie charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = [
        {"name": _serialize_value(row[x_idx]), "value": _serialize_value(row[y_idx])}
        for row in rows
    ]

    return {
        "title": {"text": title} if title else {},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"orient": "vertical", "left": "left"},
        "series": [
            {
                "type": "pie",
                "radius": "60%",
                "data": data,
                "emphasis": {"itemStyle": {"shadowBlur": 10}},
            }
        ],
        "graphic": [GNOSIS_WATERMARK],
    }


def _build_number_display(
    rows: list,
    col_index: dict[str, int],
    y_field: str,
    title: str,
) -> dict:
    """Build a KPI number display spec."""
    y_idx = col_index[y_field]
    value = _serialize_value(rows[0][y_idx]) if rows else 0

    return {
        "type": "numberDisplay",
        "title": title,
        "value": value,
        "format": "formatNumber",
    }


CHART_BUILDERS = {
    "line": lambda rows, ci, xf, yf, sf, t: _build_line_chart(rows, ci, xf, yf, sf, t, area=False),
    "area": lambda rows, ci, xf, yf, sf, t: _build_line_chart(rows, ci, xf, yf, sf, t, area=True),
    "bar": _build_bar_chart,
    "pie": lambda rows, ci, xf, yf, sf, t: _build_pie_chart(rows, ci, xf, yf, t),
    "numberDisplay": lambda rows, ci, xf, yf, sf, t: _build_number_display(rows, ci, yf, t),
}


# --- Markdown to HTML Converter ---

def _markdown_to_html(text: str) -> str:
    """Convert markdown to HTML. Handles headers, bold, tables, lists, code, and chart placeholders."""
    lines = text.split("\n")
    html_lines: list[str] = []
    in_list = False
    in_code_block = False
    in_table = False
    table_header_done = False

    for line in lines:
        # Code blocks
        if line.strip().startswith("```"):
            if in_code_block:
                html_lines.append("</code></pre>")
                in_code_block = False
            else:
                lang = line.strip()[3:].strip()
                cls = f' class="language-{lang}"' if lang else ""
                html_lines.append(f"<pre><code{cls}>")
                in_code_block = True
            continue

        if in_code_block:
            html_lines.append(_escape_html(line))
            continue

        # Close list if needed
        if in_list and not line.strip().startswith("- "):
            html_lines.append("</ul>")
            in_list = False

        # Close table if needed
        if in_table and not line.strip().startswith("|"):
            html_lines.append("</tbody></table>")
            in_table = False
            table_header_done = False

        stripped = line.strip()

        # Chart placeholders
        chart_match = re.match(r"\{\{chart:(\w+)\}\}", stripped)
        if chart_match:
            chart_id = chart_match.group(1)
            html_lines.append(
                f'<div id="chart-{chart_id}" class="chart-container"></div>'
            )
            continue

        # Headers
        if stripped.startswith("### "):
            html_lines.append(f"<h3>{_inline_format(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            html_lines.append(f"<h2>{_inline_format(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            html_lines.append(f"<h1>{_inline_format(stripped[2:])}</h1>")
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            html_lines.append("<hr>")
            continue

        # Table rows
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped[1:-1].split("|")]
            # Skip separator rows
            if all(re.match(r"^:?-+:?$", c) for c in cells):
                continue
            if not in_table:
                html_lines.append('<table>')
                html_lines.append("<thead><tr>")
                for cell in cells:
                    html_lines.append(f"<th>{_inline_format(cell)}</th>")
                html_lines.append("</tr></thead><tbody>")
                in_table = True
                table_header_done = True
                continue
            html_lines.append("<tr>")
            for cell in cells:
                html_lines.append(f"<td>{_inline_format(cell)}</td>")
            html_lines.append("</tr>")
            continue

        # List items
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_format(stripped[2:])}</li>")
            continue

        # Empty line
        if not stripped:
            html_lines.append("")
            continue

        # Paragraph
        html_lines.append(f"<p>{_inline_format(stripped)}</p>")

    # Close any open tags
    if in_list:
        html_lines.append("</ul>")
    if in_table:
        html_lines.append("</tbody></table>")
    if in_code_block:
        html_lines.append("</code></pre>")

    return "\n".join(html_lines)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting: bold, code, links."""
    text = _escape_html(text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Inline code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# --- HTML Report Template ---

REPORT_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
  --primary: #4f46e5;
  --primary-light: #6366f1;
  --bg: #ffffff;
  --text: #1a1a2e;
  --text-secondary: #6b7280;
  --surface: #f8f9fa;
  --border: #e5e7eb;
  --table-stripe: #f9fafb;
}}
[data-theme="dark"] {{
  --bg: #1a1a2e;
  --text: #e5e7eb;
  --text-secondary: #9ca3af;
  --surface: #2d2d44;
  --border: #404060;
  --table-stripe: #252540;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
  line-height: 1.6;
  transition: background 0.3s, color 0.3s;
}}
header {{
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 2rem;
  padding-bottom: 1.5rem;
  border-bottom: 2px solid var(--primary);
}}
header h1 {{
  font-size: 1.75rem;
  font-weight: 700;
  color: var(--primary);
}}
.meta {{
  color: var(--text-secondary);
  font-size: 0.875rem;
  margin-top: 0.25rem;
}}
.theme-toggle {{
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--text);
  padding: 0.5rem 1rem;
  border-radius: 6px;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.875rem;
  transition: background 0.2s;
}}
.theme-toggle:hover {{ background: var(--border); }}
h1 {{ font-size: 1.5rem; font-weight: 700; margin: 1.5rem 0 0.75rem; }}
h2 {{ font-size: 1.25rem; font-weight: 600; margin: 1.5rem 0 0.75rem; color: var(--primary); }}
h3 {{ font-size: 1.1rem; font-weight: 600; margin: 1.25rem 0 0.5rem; }}
p {{ margin: 0.5rem 0; }}
hr {{ border: none; border-top: 1px solid var(--border); margin: 1.5rem 0; }}
ul {{ padding-left: 1.5rem; margin: 0.5rem 0; }}
li {{ margin: 0.25rem 0; }}
strong {{ font-weight: 600; }}
code {{
  background: var(--surface);
  padding: 0.15rem 0.4rem;
  border-radius: 4px;
  font-size: 0.9em;
  font-family: 'SF Mono', 'Fira Code', monospace;
}}
pre {{
  background: var(--surface);
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.75rem 0;
  border: 1px solid var(--border);
}}
pre code {{
  background: none;
  padding: 0;
}}
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 0.75rem 0;
  font-size: 0.9rem;
}}
th, td {{
  padding: 0.6rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid var(--border);
}}
th {{
  background: var(--surface);
  font-weight: 600;
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.025em;
  color: var(--text-secondary);
}}
tr:nth-child(even) td {{ background: var(--table-stripe); }}
.chart-container {{
  width: 100%;
  height: 400px;
  margin: 1.5rem 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
}}
@media print {{
  .theme-toggle {{ display: none; }}
  body {{ padding: 0; max-width: none; }}
  .chart-container {{ break-inside: avoid; }}
}}
@media (max-width: 768px) {{
  body {{ padding: 1rem; }}
  .chart-container {{ height: 300px; }}
  header {{ flex-direction: column; gap: 0.75rem; }}
}}
</style>
</head>
<body>
<header>
  <div>
    <h1>{title}</h1>
    <div class="meta">Generated: {timestamp} &middot; Gnosis Chain Analytics</div>
  </div>
  <button class="theme-toggle" onclick="toggleTheme()">Dark Mode</button>
</header>
<main>{rendered_html}</main>
<script>
const chartSpecs = {chart_specs_json};
const charts = {{}};
Object.entries(chartSpecs).forEach(([id, spec]) => {{
  const el = document.getElementById('chart-' + id);
  if (el) {{
    const chart = echarts.init(el);
    chart.setOption(spec);
    charts[id] = chart;
  }}
}});
window.addEventListener('resize', () => {{
  Object.values(charts).forEach(c => c.resize());
}});
function toggleTheme() {{
  const html = document.documentElement;
  const isDark = html.dataset.theme === 'dark';
  html.dataset.theme = isDark ? 'light' : 'dark';
  document.querySelector('.theme-toggle').textContent = isDark ? 'Dark Mode' : 'Light Mode';
  Object.values(charts).forEach(c => c.resize());
}}
</script>
</body>
</html>"""


def register_visualization_tools(mcp, ch: ClickHouseManager):
    """Register chart generation and report tools."""

    @mcp.tool()
    def generate_chart(
        sql: str,
        database: str = "dbt",
        chart_type: str = "line",
        x_field: str = "",
        y_field: str = "",
        series_field: str = "",
        title: str = "",
        max_rows: int = 500,
    ) -> str:
        """Execute a query and generate an ECharts visualization spec.

        Returns a JSON object compatible with echarts.setOption() and the
        Gnosis metrics-dashboard EChartsContainer component. Includes a
        Gnosis owl watermark.

        Supported chart types: line, area, bar, pie, numberDisplay.

        Args:
            sql: SQL query to execute for chart data.
            database: Target database. Default: dbt.
            chart_type: Chart type (line, area, bar, pie, numberDisplay). Default: line.
            x_field: Column name for the X axis (categories/dates).
            y_field: Column name for the Y axis (values).
            series_field: Optional column name to split data into multiple series.
            title: Chart title.
            max_rows: Maximum data points. Default: 500.

        Returns:
            ECharts option JSON string. Render with: echarts.setOption(JSON.parse(result))
        """
        try:
            if chart_type not in CHART_BUILDERS:
                supported = ", ".join(CHART_BUILDERS.keys())
                return f"Error: Unknown chart type '{chart_type}'. Supported: {supported}"

            result = ch.execute_query(sql, database, max_rows)
            columns = result["columns"]
            rows = result["rows"]

            if not rows:
                return "Error: Query returned no data. Cannot generate chart."

            col_index = _build_col_index(columns)

            # Auto-detect fields if not specified
            if not x_field and columns:
                x_field = columns[0]
            if not y_field and len(columns) > 1:
                y_field = columns[1]

            # Validate fields exist
            if x_field and x_field not in col_index:
                available = ", ".join(columns)
                return f"Error: x_field '{x_field}' not found in columns: {available}"
            if y_field and y_field not in col_index:
                available = ", ".join(columns)
                return f"Error: y_field '{y_field}' not found in columns: {available}"
            if series_field and series_field not in col_index:
                available = ", ".join(columns)
                return f"Error: series_field '{series_field}' not found in columns: {available}"

            builder = CHART_BUILDERS[chart_type]
            option = builder(rows, col_index, x_field, y_field, series_field, title)

            # Register chart in registry
            chart_id = _next_chart_id()
            _chart_registry[chart_id] = {
                "option": option,
                "title": title or chart_type,
                "chart_type": chart_type,
                "data_points": len(rows),
            }

            output = json.dumps(option, default=str, indent=2)
            metadata = (
                f"\n\n---\n"
                f"Chart ID: **{chart_id}** (use in reports with "
                f"`{{{{chart:{chart_id}}}}}`) | "
                f"Type: {chart_type} | "
                f"Data points: {len(rows)} | "
                f"Query time: {result['elapsed_seconds']}s"
            )
            return truncate_response(output + metadata)

        except Exception as e:
            return f"Error: {e}"

    @mcp.tool()
    def list_charts() -> str:
        """List all charts in the registry with IDs, titles, and types.

        Returns:
            Table of registered charts available for use in generate_report.
        """
        if not _chart_registry:
            return "No charts registered. Use `generate_chart` to create charts first."

        lines = ["# Registered Charts\n"]
        lines.append("| Chart ID | Title | Type | Data Points |")
        lines.append("|----------|-------|------|-------------|")

        for chart_id, info in _chart_registry.items():
            lines.append(
                f"| {chart_id} | {info['title']} | {info['chart_type']} "
                f"| {info['data_points']} |"
            )

        lines.append(
            f"\nTotal: {len(_chart_registry)} charts. "
            f"Use `{{{{chart:CHART_ID}}}}` placeholders in `generate_report`."
        )
        return "\n".join(lines)

    @mcp.tool()
    def generate_report(
        title: str,
        content_markdown: str,
        output_path: str = "",
    ) -> str:
        """Create a standalone HTML report with rendered ECharts visualizations.

        Takes markdown content with {{chart:CHART_ID}} placeholders and produces
        a self-contained HTML file with interactive charts, Gnosis branding,
        dark mode toggle, and responsive design.

        Workflow:
        1. Call generate_chart multiple times to create charts (each returns a chart ID)
        2. Write markdown content with {{chart:CHART_ID}} where charts should appear
        3. Call this tool to produce the HTML file

        Args:
            title: Report title displayed in the header.
            content_markdown: Markdown content with {{chart:CHART_ID}} placeholders.
            output_path: File path for HTML output. Defaults to
                ~/.cerebro-mcp/reports/{title_slug}_{timestamp}.html

        Returns:
            Path to the generated HTML file and report metadata.
        """
        try:
            # Find chart placeholders
            chart_ids_in_content = re.findall(
                r"\{\{chart:(\w+)\}\}", content_markdown
            )

            # Collect chart specs for referenced charts
            chart_specs = {}
            missing = []
            for cid in chart_ids_in_content:
                if cid in _chart_registry:
                    chart_specs[cid] = _chart_registry[cid]["option"]
                else:
                    missing.append(cid)

            if missing:
                return (
                    f"Error: Chart IDs not found in registry: {', '.join(missing)}. "
                    f"Available: {', '.join(_chart_registry.keys()) or 'none'}. "
                    f"Use `generate_chart` to create charts first."
                )

            # Convert markdown to HTML
            rendered_html = _markdown_to_html(content_markdown)

            # Generate timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

            # Build HTML
            chart_specs_json = json.dumps(chart_specs, default=str)
            html = REPORT_HTML_TEMPLATE.format(
                title=_escape_html(title),
                timestamp=timestamp,
                rendered_html=rendered_html,
                chart_specs_json=chart_specs_json,
            )

            # Determine output path
            if not output_path:
                reports_dir = os.path.expanduser(settings.REPORTS_OUTPUT_DIR)
                os.makedirs(reports_dir, exist_ok=True)
                slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:50]
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_path = os.path.join(reports_dir, f"{slug}_{ts}.html")
            else:
                parent = os.path.dirname(output_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html)

            size_kb = os.path.getsize(output_path) / 1024
            return (
                f"Report generated: {output_path}\n"
                f"Charts: {len(chart_specs)} | "
                f"Size: {size_kb:.1f} KB\n\n"
                f"Open in browser to view interactive charts."
            )

        except Exception as e:
            return f"Error: {e}"
