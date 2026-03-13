import importlib.resources
import json
import os
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
if sys.version_info >= (3, 11):
    from typing import NotRequired, TypedDict
else:
    from typing_extensions import NotRequired, TypedDict

from mcp.types import Annotations, CallToolResult, TextContent

from cerebro_mcp.clickhouse_client import ClickHouseManager
from cerebro_mcp.tools.query import truncate_response, _truncate_sql


class ChartSpec(TypedDict):
    """Typed specification for a single chart in a batch generate_charts call."""
    sql: str
    database: NotRequired[str]
    chart_type: NotRequired[str]
    x_field: NotRequired[str]
    y_field: NotRequired[str]
    series_field: NotRequired[str]
    title: NotRequired[str]
    max_rows: NotRequired[int]


# --- Bundled React UI (Vite single-file build) ---
_BUNDLED_REPORT_HTML: str | None = None


def _get_report_html() -> str:
    """Load the Vite-built single-file React app from the static package."""
    global _BUNDLED_REPORT_HTML
    if _BUNDLED_REPORT_HTML is None:
        _BUNDLED_REPORT_HTML = (
            importlib.resources.files("cerebro_mcp")
            .joinpath("static/report.html")
            .read_text("utf-8")
        )
    return _BUNDLED_REPORT_HTML

# ECharts color palettes matching metrics-dashboard
ECHARTS_PALETTE_LIGHT = [
    "#4F46E5", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6",
    "#3B82F6", "#EC4899", "#14B8A6", "#F97316", "#84CC16",
    "#06B6D4", "#A855F7", "#22C55E", "#FB7185", "#0EA5E9",
]
ECHARTS_PALETTE_DARK = [
    "#818CF8", "#34D399", "#FBBF24", "#F87171", "#A78BFA",
    "#60A5FA", "#F472B6", "#2DD4BF", "#FDBA74", "#A3E635",
    "#67E8F9", "#C4B5FD", "#4ADE80", "#FDA4AF", "#38BDF8",
]

# --- Chart Registry ---
_chart_registry: dict[str, dict] = {}
_chart_counter = 0
_chart_lock = threading.Lock()
_CHART_TTL = timedelta(hours=2)


def _next_chart_id() -> str:
    global _chart_counter
    with _chart_lock:
        _chart_counter += 1
        return f"chart_{_chart_counter}"


def _prune_chart_registry() -> None:
    """Remove expired charts. Must be called under _chart_lock."""
    now = datetime.now()
    expired = [
        k for k, v in _chart_registry.items()
        if now - v.get("created_at", now) > _CHART_TTL
    ]
    for k in expired:
        del _chart_registry[k]


# --- Report Cache ---
_REPORT_CACHE: dict[str, dict] = {}
_REPORT_LOCK = threading.Lock()
_REPORT_TTL = timedelta(hours=1)
_REPORT_MAX_ENTRIES = 20


def _prune_report_cache() -> None:
    """Remove expired/excess reports. Must be called under _REPORT_LOCK."""
    now = datetime.now(timezone.utc)
    expired = [k for k, v in _REPORT_CACHE.items() if now > v["expires"]]
    for k in expired:
        del _REPORT_CACHE[k]
    # Evict oldest if still over limit
    while len(_REPORT_CACHE) > _REPORT_MAX_ENTRIES:
        oldest = min(_REPORT_CACHE, key=lambda k: _REPORT_CACHE[k]["expires"])
        del _REPORT_CACHE[oldest]


# --- Report Helpers ---


def _get_report_dir() -> Path:
    """Resolve and ensure the report directory exists."""
    d = Path(os.environ.get("CEREBRO_REPORT_DIR", "~/.cerebro/reports")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _report_filename(report_id: str, title: str) -> str:
    """Build a durable report filename: cerebro_report_<UTC>_<slug>_<full-id>.html"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Slug: first 3 words of title, lowercased, non-alpha stripped, joined by hyphen
    words = re.sub(r"[^a-zA-Z0-9 ]", "", title).split()[:3]
    slug = "-".join(w.lower() for w in words) if words else "report"
    return f"cerebro_report_{ts}_{slug}_{report_id}.html"


# --- Optional HTTP Report Server ---
_report_server_started = False


def _ensure_report_server() -> None:
    """Start a lightweight HTTP server for serving reports (daemon thread)."""
    global _report_server_started
    from cerebro_mcp.config import settings

    if _report_server_started or not settings.REPORT_SERVER_PORT:
        return

    from http.server import SimpleHTTPRequestHandler
    from socketserver import ThreadingTCPServer

    report_dir = _get_report_dir()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(report_dir), **kwargs)

        def log_message(self, format, *args):
            pass  # suppress access logs

    try:
        server = ThreadingTCPServer(("", settings.REPORT_SERVER_PORT), Handler)
        server.daemon_threads = True
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _report_server_started = True
    except OSError:
        pass  # port in use, skip


def _get_report_link(path: Path) -> str:
    """Get the best available URL for a report file."""
    from cerebro_mcp.config import settings

    if settings.REPORT_BASE_URL:
        return f"{settings.REPORT_BASE_URL.rstrip('/')}/{path.name}"
    if settings.REPORT_SERVER_PORT:
        _ensure_report_server()
        return f"http://localhost:{settings.REPORT_SERVER_PORT}/{path.name}"
    return path.resolve().as_uri()  # file:// fallback


def _find_report_on_disk(report_ref: str) -> Path | None:
    """Find a report file by full UUID or 8-char prefix."""
    report_dir = _get_report_dir()
    if not report_dir.exists():
        return None
    # Try exact match first (full UUID in filename)
    for f in report_dir.glob(f"cerebro_report_*_{report_ref}.html"):
        return f
    # Try 8-char prefix match
    matches = [
        f for f in report_dir.glob("cerebro_report_*.html")
        if report_ref in f.name
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _extract_report_id_from_path(path: Path) -> str:
    """Extract the full report UUID from a filename."""
    # Format: cerebro_report_<ts>_<slug>_<uuid>.html
    name = path.stem  # drop .html
    parts = name.split("_")
    # UUID is the last part (may contain hyphens)
    if len(parts) >= 5:
        return parts[-1]
    return name


def _extract_structured_from_html(html: str) -> dict | None:
    """Try to extract embedded report data from standalone HTML."""
    match = re.search(
        r'<script\s+id="report-data"\s+type="application/json">(.*?)</script>',
        html, re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


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

    # Dual y-axis: comma-separated y_field (e.g., "transactions,gas_price")
    y_fields = [f.strip() for f in y_field.split(",")]
    if len(y_fields) > 1:
        rows_sorted = sorted(rows, key=lambda r: r[x_idx])
        x_values = _extract_column(rows_sorted, x_idx)
        series_list = []
        for i, yf in enumerate(y_fields):
            yi = col_index[yf]
            s: dict = {
                "name": yf,
                "type": "line",
                "data": _extract_column(rows_sorted, yi),
                "smooth": True,
                "symbolSize": 2,
            }
            if i > 0:
                s["yAxisIndex"] = 1
            if area:
                s["areaStyle"] = {"opacity": 0.3}
            series_list.append(s)
        return {
            "title": {},
            "tooltip": {"trigger": "axis"},
            "legend": {"data": y_fields, "top": 0, "type": "scroll"},
            "grid": {"left": "3%", "right": "6%", "bottom": "10%", "top": "40", "containLabel": True},
            "xAxis": {"type": "category", "data": x_values, "boundaryGap": False},
            "yAxis": [{"type": "value"}, {"type": "value"}],
            "series": series_list,
        }

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
        # Sort x-axis chronologically (ISO dates sort correctly as strings)
        x_values = sorted(x_values_set)
    else:
        # Sort rows by x_field for chronological ordering
        rows = sorted(rows, key=lambda r: r[x_idx])
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
        "title": {},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend_data, "top": 0, "type": "scroll"},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "top": "40", "containLabel": True},
        "xAxis": {"type": "category", "data": x_values, "boundaryGap": False},
        "yAxis": {"type": "value"},
        "series": series_list,
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

    # Dual y-axis: comma-separated y_field
    y_fields = [f.strip() for f in y_field.split(",")]
    if len(y_fields) > 1:
        x_values = _extract_column(rows, x_idx)
        series_list = []
        for i, yf in enumerate(y_fields):
            yi = col_index[yf]
            s: dict = {"name": yf, "type": "bar", "data": _extract_column(rows, yi)}
            if i > 0:
                s["yAxisIndex"] = 1
            series_list.append(s)
        return {
            "title": {},
            "tooltip": {"trigger": "axis"},
            "legend": {"data": y_fields, "top": 0, "type": "scroll"},
            "grid": {"left": "3%", "right": "6%", "bottom": "10%", "top": "40", "containLabel": True},
            "xAxis": {"type": "category", "data": x_values},
            "yAxis": [{"type": "value"}, {"type": "value"}],
            "series": series_list,
        }

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
        "title": {},
        "tooltip": {"trigger": "axis"},
        "legend": {"data": legend_data, "top": 0, "type": "scroll"},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "top": "40", "containLabel": True},
        "xAxis": {"type": "category", "data": x_values},
        "yAxis": {"type": "value"},
        "series": series_list,
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
        "title": {},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "legend": {"orient": "vertical", "left": "left", "top": 0, "type": "scroll"},
        "series": [
            {
                "type": "pie",
                "radius": "60%",
                "data": data,
                "emphasis": {"itemStyle": {"shadowBlur": 10}},
            }
        ],
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


def _build_scatter_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for scatter charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    if series_field and series_field in col_index:
        series_idx = col_index[series_field]
        series_data: dict[str, list] = {}
        for row in rows:
            s_val = str(_serialize_value(row[series_idx]))
            if s_val not in series_data:
                series_data[s_val] = []
            series_data[s_val].append([
                _serialize_value(row[x_idx]),
                _serialize_value(row[y_idx]),
            ])
        series_list = [
            {"name": s_name, "type": "scatter", "data": data, "symbolSize": 6}
            for s_name, data in series_data.items()
        ]
        legend_data = list(series_data.keys())
    else:
        data = [
            [_serialize_value(row[x_idx]), _serialize_value(row[y_idx])]
            for row in rows
        ]
        series_list = [{"name": y_field, "type": "scatter", "data": data, "symbolSize": 6}]
        legend_data = [y_field]

    return {
        "title": {},
        "tooltip": {"trigger": "item"},
        "legend": {"data": legend_data, "top": 0, "type": "scroll"},
        "grid": {"left": "3%", "right": "4%", "bottom": "10%", "top": "40", "containLabel": True},
        "xAxis": {"type": "value", "name": x_field},
        "yAxis": {"type": "value", "name": y_field},
        "series": series_list,
    }


def _build_heatmap_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for grid heatmap charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]
    # Value comes from series_field or the 3rd column
    if series_field and series_field in col_index:
        v_idx = col_index[series_field]
    else:
        remaining = [i for i in range(len(col_index)) if i != x_idx and i != y_idx]
        v_idx = remaining[0] if remaining else y_idx

    x_cats = list(dict.fromkeys(_serialize_value(row[x_idx]) for row in rows))
    y_cats = list(dict.fromkeys(_serialize_value(row[y_idx]) for row in rows))
    x_map = {v: i for i, v in enumerate(x_cats)}
    y_map = {v: i for i, v in enumerate(y_cats)}

    data = []
    values = []
    for row in rows:
        xv = _serialize_value(row[x_idx])
        yv = _serialize_value(row[y_idx])
        val = _serialize_value(row[v_idx])
        data.append([x_map[xv], y_map[yv], val])
        if isinstance(val, (int, float)):
            values.append(val)

    return {
        "title": {},
        "tooltip": {"position": "top"},
        "grid": {"left": "3%", "right": "4%", "bottom": "15%", "top": "10%", "containLabel": True},
        "xAxis": {"type": "category", "data": [str(c) for c in x_cats], "splitArea": {"show": True}},
        "yAxis": {"type": "category", "data": [str(c) for c in y_cats], "splitArea": {"show": True}},
        "visualMap": {
            "min": min(values) if values else 0,
            "max": max(values) if values else 1,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "bottom": "0%",
        },
        "series": [{"type": "heatmap", "data": data, "label": {"show": True}}],
        "_cerebro_height": "400px",
    }


def _build_calendar_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for calendar heatmap charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = []
    values = []
    dates = []
    for row in rows:
        d = str(_serialize_value(row[x_idx]))[:10]  # YYYY-MM-DD
        val = _serialize_value(row[y_idx])
        data.append([d, val])
        dates.append(d)
        if isinstance(val, (int, float)):
            values.append(val)

    if not dates:
        return {"title": {"text": "No data"}}

    date_min = min(dates)
    date_max = max(dates)
    # Calculate number of years for height
    year_min = int(date_min[:4])
    year_max = int(date_max[:4])
    num_years = max(1, year_max - year_min + 1)
    height = f"{180 * num_years}px"

    calendars = []
    series_list = []
    for i, year in enumerate(range(year_min, year_max + 1)):
        calendars.append({
            "top": 60 + i * 160,
            "range": str(year),
            "cellSize": ["auto", 15],
            "left": 80,
            "right": 30,
        })
        year_data = [d for d in data if d[0].startswith(str(year))]
        series_list.append({
            "type": "heatmap",
            "coordinateSystem": "calendar",
            "calendarIndex": i,
            "data": year_data,
        })

    return {
        "tooltip": {"position": "top", "formatter": "{c}"},
        "visualMap": {
            "min": min(values) if values else 0,
            "max": max(values) if values else 1,
            "calculable": True,
            "orient": "horizontal",
            "left": "center",
            "top": 0,
        },
        "calendar": calendars,
        "series": series_list,
        "_cerebro_height": height,
    }


def _build_gauge_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for gauge charts."""
    y_idx = col_index[y_field]
    value = _serialize_value(rows[0][y_idx]) if rows else 0

    return {
        "tooltip": {"formatter": "{b}: {c}"},
        "series": [{
            "type": "gauge",
            "data": [{"value": value, "name": title or y_field}],
            "detail": {"formatter": "{value}"},
            "title": {"fontSize": 14},
        }],
        "_cerebro_height": "250px",
    }


def _build_treemap_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for treemap charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = [
        {"name": str(_serialize_value(row[x_idx])), "value": _serialize_value(row[y_idx])}
        for row in rows
    ]

    return {
        "tooltip": {"formatter": "{b}: {c}"},
        "series": [{
            "type": "treemap",
            "data": data,
            "label": {"show": True, "formatter": "{b}"},
            "breadcrumb": {"show": False},
        }],
    }


def _build_sankey_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for sankey flow diagrams."""
    src_idx = col_index[x_field]
    tgt_idx = col_index[y_field]
    val_idx = col_index[series_field] if series_field and series_field in col_index else None

    nodes_set: set[str] = set()
    links = []
    for row in rows:
        src = str(_serialize_value(row[src_idx]))
        tgt = str(_serialize_value(row[tgt_idx]))
        val = _serialize_value(row[val_idx]) if val_idx is not None else 1
        nodes_set.add(src)
        nodes_set.add(tgt)
        links.append({"source": src, "target": tgt, "value": val})

    return {
        "tooltip": {"trigger": "item"},
        "series": [{
            "type": "sankey",
            "data": [{"name": n} for n in sorted(nodes_set)],
            "links": links,
            "emphasis": {"focus": "adjacency"},
            "lineStyle": {"color": "gradient", "curveness": 0.5},
        }],
        "_cerebro_height": "450px",
    }


def _build_graph_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for force-directed graph charts."""
    src_idx = col_index[x_field]
    tgt_idx = col_index[y_field]
    val_idx = col_index[series_field] if series_field and series_field in col_index else None

    degree: dict[str, int] = {}
    links = []
    for row in rows:
        src = str(_serialize_value(row[src_idx]))
        tgt = str(_serialize_value(row[tgt_idx]))
        val = _serialize_value(row[val_idx]) if val_idx is not None else 1
        degree[src] = degree.get(src, 0) + 1
        degree[tgt] = degree.get(tgt, 0) + 1
        links.append({"source": src, "target": tgt, "value": val})

    max_deg = max(degree.values(), default=1)
    nodes = [
        {"name": n, "symbolSize": 10 + 30 * (d / max_deg)}
        for n, d in degree.items()
    ]

    return {
        "tooltip": {},
        "series": [{
            "type": "graph",
            "layout": "force",
            "data": nodes,
            "links": links,
            "roam": True,
            "label": {"show": True, "position": "right", "fontSize": 10},
            "force": {"repulsion": 200, "edgeLength": [50, 200]},
            "emphasis": {"focus": "adjacency"},
            "lineStyle": {"opacity": 0.6},
        }],
        "_cerebro_height": "500px",
    }


def _build_funnel_chart(
    rows: list,
    col_index: dict[str, int],
    x_field: str,
    y_field: str,
    series_field: str,
    title: str,
) -> dict:
    """Build ECharts option for funnel charts."""
    x_idx = col_index[x_field]
    y_idx = col_index[y_field]

    data = sorted(
        [
            {"name": str(_serialize_value(row[x_idx])), "value": _serialize_value(row[y_idx])}
            for row in rows
        ],
        key=lambda d: d["value"] if isinstance(d["value"], (int, float)) else 0,
        reverse=True,
    )

    return {
        "tooltip": {"trigger": "item", "formatter": "{b}: {c}"},
        "legend": {"data": [d["name"] for d in data], "top": 0, "type": "scroll"},
        "series": [{
            "type": "funnel",
            "left": "10%",
            "width": "80%",
            "top": 40,
            "bottom": 20,
            "data": data,
            "label": {"show": True, "position": "inside"},
            "emphasis": {"label": {"fontSize": 14}},
        }],
    }


CHART_BUILDERS = {
    "line": lambda rows, ci, xf, yf, sf, t: _build_line_chart(rows, ci, xf, yf, sf, t, area=False),
    "area": lambda rows, ci, xf, yf, sf, t: _build_line_chart(rows, ci, xf, yf, sf, t, area=True),
    "bar": _build_bar_chart,
    "pie": lambda rows, ci, xf, yf, sf, t: _build_pie_chart(rows, ci, xf, yf, t),
    "numberDisplay": lambda rows, ci, xf, yf, sf, t: _build_number_display(rows, ci, yf, t),
    "scatter": _build_scatter_chart,
    "heatmap": _build_heatmap_chart,
    "calendar": _build_calendar_chart,
    "gauge": _build_gauge_chart,
    "treemap": _build_treemap_chart,
    "sankey": _build_sankey_chart,
    "graph": _build_graph_chart,
    "funnel": _build_funnel_chart,
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
    in_content_card = False
    in_grid = False
    grid_cols = 0
    grid_chart_ids: list[str] = []

    def _close_content_card():
        nonlocal in_content_card
        if in_content_card:
            html_lines.append("</div>")  # close .content-card
            in_content_card = False

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

        # Grid open: {{grid:N}}
        grid_open = re.match(r"\{\{grid:(\d+)\}\}", stripped)
        if grid_open:
            _close_content_card()
            in_grid = True
            grid_cols = int(grid_open.group(1))
            grid_chart_ids = []
            continue

        # Grid close: {{/grid}} — emit combined grid element with data attribute
        if stripped == "{{/grid}}" and in_grid:
            ids_str = ",".join(grid_chart_ids)
            html_lines.append(
                f'<div class="chart-grid chart-grid-{grid_cols}" '
                f'data-grid-charts="{ids_str}"></div>'
            )
            in_grid = False
            grid_chart_ids = []
            continue

        # Chart placeholders
        chart_match = re.match(r"\{\{chart:(\w+)\}\}", stripped)
        if chart_match:
            chart_id = chart_match.group(1)
            # Inside grid: collect IDs, don't emit individual cards
            if in_grid:
                grid_chart_ids.append(chart_id)
                continue
            # Outside grid: emit standalone card
            _close_content_card()
            chart_title = _chart_registry.get(chart_id, {}).get("title", "")
            title_html = (
                f'<div class="chart-title">{_escape_html(chart_title)}</div>'
                if chart_title
                else ""
            )
            html_lines.append(
                f'<div class="chart-card">'
                f'{title_html}'
                f'<div id="chart-{chart_id}" class="chart-container"></div>'
                f'</div>'
            )
            continue

        # Headers
        if stripped.startswith("### "):
            _close_content_card()
            in_content_card = True
            html_lines.append(f'<div class="content-card">')
            html_lines.append(f"<h3>{_inline_format(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            _close_content_card()
            html_lines.append(f"<h2>{_inline_format(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            _close_content_card()
            html_lines.append(f"<h1>{_inline_format(stripped[2:])}</h1>")
            continue

        # Horizontal rule
        if stripped in ("---", "***", "___"):
            _close_content_card()
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
                cell_chart = re.match(
                    r"\{\{chart:(\w+)\}\}", cell.strip()
                )
                if cell_chart:
                    cid = cell_chart.group(1)
                    chart_data = _chart_registry.get(cid, {})
                    chart_opt = chart_data.get("option", {})
                    # Render numberDisplay values inline in table cells
                    if chart_opt.get("type") == "numberDisplay":
                        val = chart_opt.get("value", "")
                        if isinstance(val, (int, float)):
                            formatted = f"{val:,.0f}" if val == int(val) else f"{val:,.2f}"
                        else:
                            formatted = str(val)
                        html_lines.append(
                            f'<td class="kpi-cell">'
                            f'<span class="kpi-value">{_escape_html(formatted)}</span>'
                            f'</td>'
                        )
                    else:
                        # Non-number charts: emit chart container div
                        html_lines.append(
                            f'<td><div id="chart-{cid}" '
                            f'class="chart-container"></div></td>'
                        )
                else:
                    html_lines.append(
                        f"<td>{_inline_format(cell)}</td>"
                    )
            html_lines.append("</tr>")
            continue

        # List items
        if stripped.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_format(stripped[2:])}</li>")
            continue

        # Blockquote
        if stripped.startswith("> "):
            html_lines.append(
                f"<blockquote><p>{_inline_format(stripped[2:])}</p></blockquote>"
            )
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
    _close_content_card()

    return "\n".join(html_lines)


def _inline_format(text: str) -> str:
    """Apply inline markdown formatting: bold, code, links, and value coloring."""
    text = _escape_html(text)
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Inline code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # Positive numeric changes: +1,234.56% or +1234.56 etc.
    text = re.sub(
        r"(\+\d[\d,]*\.?\d*%?)",
        r'<span class="number-change positive">\1</span>',
        text,
    )
    # Negative numeric changes: -1,234.56% or -1234.56 etc.
    text = re.sub(
        r"(-\d[\d,]*\.?\d*%?)",
        r'<span class="number-change negative">\1</span>',
        text,
    )
    return text


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# --- MCP App Resource URI ---

REPORT_URI = "ui://cerebro/report"


def _build_standalone_html(
    title: str,
    timestamp: str,
    charts: dict,
    sections_html: str,
    queries: dict | None = None,
) -> str:
    """Build self-contained HTML with embedded data for disk saves / direct file access.

    Injects a <script id="report-data"> tag into the Vite-built React app.
    The React app detects this tag and renders the report in standalone mode.
    """
    data_dict = {
        "title": title,
        "timestamp": timestamp,
        "charts": charts,
        "sections_html": sections_html,
    }
    if queries:
        data_dict["queries"] = queries
    data = json.dumps(data_dict, default=str)

    html = _get_report_html()
    data_tag = f'<script id="report-data" type="application/json">{data}</script>'
    # Use rfind to target only the LAST </body> tag (not one inside minified JS)
    insert_pos = html.rfind("</body>")
    if insert_pos == -1:
        return html + data_tag
    return html[:insert_pos] + data_tag + "\n" + html[insert_pos:]


def register_visualization_tools(mcp, ch: ClickHouseManager):
    """Register chart generation and report tools."""

    @mcp.tool()
    # ── Shared chart builder ───────────────────────────────────────

    def _build_and_register_chart(
        sql: str,
        database: str,
        chart_type: str,
        x_field: str,
        y_field: str,
        series_field: str,
        title: str,
        max_rows: int,
        return_metadata_only: bool = False,
    ) -> str:
        """Internal helper: execute SQL, build ECharts spec, register chart.

        When return_metadata_only=True, returns only a compact metadata line
        (chart ID, type, title, data points, query time) without the full
        ECharts JSON or SQL echo. Used by generate_charts batch tool.
        """
        from cerebro_mcp.tools.session_state import state

        if chart_type not in CHART_BUILDERS:
            supported = ", ".join(CHART_BUILDERS.keys())
            return f"Error: Unknown chart type '{chart_type}'. Supported: {supported}"

        try:
            state.record_generate_chart(chart_type, sql, series_field)

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

            # Register chart in registry (with TTL tracking)
            chart_id = _next_chart_id()
            with _chart_lock:
                _prune_chart_registry()
                _chart_registry[chart_id] = {
                    "option": option,
                    "title": title or chart_type,
                    "chart_type": chart_type,
                    "data_points": len(rows),
                    "created_at": datetime.now(),
                    "sql": sql,
                    "database": database,
                    "series_field": series_field,
                }

            # Metadata-only mode: compact single line for batch tool
            if return_metadata_only:
                series_tag = f" | series: {series_field}" if series_field else ""
                return (
                    f"OK|{chart_id}|{chart_type}|{title or chart_type}"
                    f"|{len(rows)}|{result['elapsed_seconds']}s{series_tag}"
                )

            output = json.dumps(option, default=str, indent=2)
            metadata = (
                f"\n\n---\n"
                f"Chart ID: **{chart_id}** (use in reports with "
                f"`{{{{chart:{chart_id}}}}}`) | "
                f"Type: {chart_type} | "
                f"Data points: {len(rows)} | "
                f"Query time: {result['elapsed_seconds']}s"
            )

            metadata += f"\n\n### SQL\n```sql\n{_truncate_sql(sql)}\n```"

            # Workflow next-step with registered charts summary
            total_charts = len(_chart_registry)
            chart_list = ", ".join(_chart_registry.keys())
            metadata += (
                f"\n\n**Registered charts ({total_charts}):** {chart_list}\n"
                "**Next step:** When all charts are ready, call "
                "`generate_report(title, content_markdown)` with "
                "`{{chart:ID}}` placeholders to produce an interactive report."
            )

            return truncate_response(output + metadata)

        except Exception as e:
            error_msg = str(e)
            if "UNKNOWN_IDENTIFIER" in error_msg or "Unknown expression" in error_msg:
                return (
                    f"Error: {error_msg}\n\n"
                    "**Hint**: Wrong column name in the SQL query. "
                    "Use `describe_table` to verify exact column names before writing SQL. "
                    "Do NOT guess — most tables use generic names like `value`, `cnt`, `date`."
                )
            return f"Error: {e}"

    # ── Gated chart tool (for reports) ──────────────────────────────

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
        """Generate a single ad-hoc chart. For reports, use `generate_charts` instead.

        This tool creates ONE chart at a time. If you are building a report,
        DO NOT call this tool repeatedly — use `generate_charts` (batch) to
        create all charts in a single call. Calling this tool multiple times
        wastes steps and context.

        Use this tool ONLY for:
        - Adding a single extra chart after a batch
        - Quick one-off visualizations outside of a report workflow

        Supported chart types: line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel.

        Args:
            sql: SQL query to execute for chart data. Only use column names
                 verified via `describe_table` or `get_model_details`.
            database: Target database. Default: dbt.
            chart_type: Chart type (line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel). Default: line.
            x_field: Column name for the X axis (categories/dates).
            y_field: Column name for the Y axis (values).
            series_field: Optional column name to split data into multiple series.
            title: Chart title.
            max_rows: Maximum data points. Default: 500.

        Returns:
            ECharts option JSON string. Render with: echarts.setOption(JSON.parse(result))
        """
        from cerebro_mcp.tools.session_state import state

        passed, reason = state.check_chart_preconditions()
        if not passed:
            return (
                f"**Analysis depth check failed:** {reason}\n\n"
                "Complete the missing steps, then retry `generate_chart`."
            )

        result = _build_and_register_chart(
            sql, database, chart_type, x_field, y_field,
            series_field, title, max_rows,
        )

        # Nudge LLM toward batch tool if calling repeatedly
        with state.lock:
            chart_count = state.generate_chart_count
        if chart_count >= 2:
            result += (
                f"\n\n**Warning:** You have called `generate_chart` "
                f"{chart_count} times individually. For reports, use "
                f"`generate_charts` (batch) to create all remaining "
                f"charts in ONE call — this saves steps and context."
            )

        return result

    # ── Quick chart tool (no gates) ─────────────────────────────────

    @mcp.tool()
    def quick_chart(
        sql: str,
        database: str = "dbt",
        chart_type: str = "line",
        x_field: str = "",
        y_field: str = "",
        series_field: str = "",
        title: str = "",
        max_rows: int = 500,
    ) -> str:
        """Generate a quick ad-hoc chart without workflow gates.

        Use this for simple, one-off plot requests. No discovery or
        exploration preconditions — just provide SQL and get a chart.
        Charts from quick_chart are registered and can be used in reports.

        For full analytical reports, use `generate_chart` instead (which
        enforces discovery and exploration).

        Supported chart types: line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel.

        Args:
            sql: SQL query to execute for chart data.
            database: Target database. Default: dbt.
            chart_type: Chart type (line, area, bar, pie, numberDisplay, scatter, heatmap, calendar, gauge, treemap, sankey, graph, funnel). Default: line.
            x_field: Column name for the X axis (categories/dates).
            y_field: Column name for the Y axis (values).
            series_field: Optional column name to split data into multiple series.
            title: Chart title.
            max_rows: Maximum data points. Default: 500.

        Returns:
            ECharts option JSON string. Render with: echarts.setOption(JSON.parse(result))
        """
        return _build_and_register_chart(
            sql, database, chart_type, x_field, y_field,
            series_field, title, max_rows,
        )

    # ── Batch chart tool (for reports) ──────────────────────────────

    @mcp.tool()
    def generate_charts(charts: list[ChartSpec]) -> str:
        """Create multiple charts in ONE tool call. Use this for reports.

        PREFERRED over generate_chart for reports. Creates all charts in a
        single call instead of calling generate_chart repeatedly. This saves
        steps and context — always use this when building a report.

        Runs the same precondition checks as generate_chart but only once,
        then creates all charts in sequence. Returns compact metadata (no
        ECharts JSON, no SQL echo) mapping input index to chart ID.

        Each chart spec must have at least `sql`. All other fields are optional
        with sensible defaults. Reports MUST include:
        - At least 1 chart with series_field (dimensional breakdown)
        - At least 1 scatter/heatmap chart OR correlation query

        Args:
            charts: List of chart specifications. Each spec has:
                sql (required), database (default "dbt"),
                chart_type (default "line"), x_field, y_field,
                series_field, title, max_rows (default 500).

        Returns:
            Summary table mapping input index to chart IDs for report placement.
        """
        from cerebro_mcp.tools.session_state import state

        if not charts:
            return "Error: No chart specs provided. Pass a non-empty list."

        # Run precondition check once
        passed, reason = state.check_chart_preconditions()
        if not passed:
            return (
                f"**Analysis depth check failed:** {reason}\n\n"
                "Complete the missing steps, then retry `generate_charts`."
            )

        succeeded = []
        failed = []

        for i, spec in enumerate(charts, 1):
            sql = spec.get("sql", "")
            if not sql:
                failed.append((i, spec.get("title", "untitled"), "No SQL provided"))
                continue

            result = _build_and_register_chart(
                sql=sql,
                database=spec.get("database", "dbt"),
                chart_type=spec.get("chart_type", "line"),
                x_field=spec.get("x_field", ""),
                y_field=spec.get("y_field", ""),
                series_field=spec.get("series_field", ""),
                title=spec.get("title", ""),
                max_rows=spec.get("max_rows", 500),
                return_metadata_only=True,
            )

            if result.startswith("OK|"):
                parts = result.split("|")
                succeeded.append({
                    "index": i,
                    "chart_id": parts[1],
                    "chart_type": parts[2],
                    "title": parts[3],
                    "data_points": parts[4],
                    "query_time": parts[5],
                })
            else:
                failed.append((i, spec.get("title", "untitled"), result))

        # Build output
        total = len(charts)
        ok_count = len(succeeded)
        lines = [f"Generated {ok_count}/{total} charts:\n"]

        lines.append("| # | Chart ID | Title | Type | Points | Time |")
        lines.append("|---|----------|-------|------|--------|------|")
        for s in succeeded:
            lines.append(
                f"| {s['index']} | {s['chart_id']} | {s['title']} "
                f"| {s['chart_type']} | {s['data_points']} "
                f"| {s['query_time']} |"
            )

        if failed:
            lines.append(f"\nFailed ({len(failed)}):")
            for idx, title, err in failed:
                lines.append(f"- Input #{idx} (\"{title}\"): {err}")

        # Chart registry summary
        chart_list = ", ".join(_chart_registry.keys())
        lines.append(
            f"\n**Registered charts ({len(_chart_registry)}):** {chart_list}\n"
            "**Next step:** Call `generate_report(title, content_markdown)` "
            "with `{{chart:ID}}` placeholders."
        )

        return "\n".join(lines)

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

    @mcp.tool(meta={
        "ui": {"resourceUri": REPORT_URI},
        "ui/resourceUri": REPORT_URI,
    })
    def generate_report(
        title: str,
        content_markdown: str,
    ) -> CallToolResult:
        """Create an interactive report rendered as a native UI in the chat client.

        YOU MUST call this as the FINAL step when producing any report or visual
        analysis. Call `generate_charts` (batch) first to create all charts in
        one call, then call this tool with markdown containing {{chart:CHART_ID}}
        placeholders.

        For GUI clients (Claude Desktop, VS Code): renders as interactive iframe.
        For terminal clients (Claude Code): opens report in default browser.

        LAYOUT RULES (ENFORCED — report will be rejected without proper layout):
        - KPI/counter charts (numberDisplay) MUST be in {{grid:3}} or {{grid:4}} rows
        - Breakdown charts (bar/pie) should pair in {{grid:2}}
        - Trend charts (line/area) go full-width between grid groups
        - Text commentary goes BETWEEN chart groups, not lumped at the end

        Example layout:
            ## Key Metrics
            {{grid:3}}
            {{chart:chart_1}}
            {{chart:chart_2}}
            {{chart:chart_3}}
            {{/grid}}

            Commentary about the KPI trends.

            {{chart:chart_4}}

            ## Breakdown
            {{grid:2}}
            {{chart:chart_5}}
            {{chart:chart_6}}
            {{/grid}}

        After this tool returns, summarize key insights and ask if the user
        wants the HTML exported (via `export_report`) or converted to docx/pdf/pptx.
        Do NOT echo the report markdown or {{chart:...}} placeholders.
        SQL queries are embedded in the report UI (click </> on each chart).

        Args:
            title: Report title displayed in the header.
            content_markdown: Markdown content with {{chart:CHART_ID}} placeholders.

        Returns:
            Interactive UI resource rendered natively in the chat client.
        """
        try:
            # --- Report quality gate ---
            from cerebro_mcp.tools.session_state import state

            passed, reason, warnings = state.check_report_preconditions(
                _chart_registry
            )
            if not passed:
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=f"Error: Report quality gate failed: {reason}",
                    )],
                    isError=True,
                )

            # Find chart placeholders
            chart_ids_in_content = re.findall(
                r"\{\{chart:(\w+)\}\}", content_markdown
            )

            # --- Grid layout enforcement ---
            has_grid = "{{grid:" in content_markdown
            kpi_count = sum(
                1 for cid in chart_ids_in_content
                if _chart_registry.get(cid, {}).get("chart_type") == "numberDisplay"
            )
            if not has_grid and kpi_count >= 2:
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=(
                            f"Error: Layout rejected: Found {kpi_count} KPI/counter "
                            f"charts but no {{{{grid:N}}}} directives. "
                            f"KPI counters MUST be grouped in a grid row "
                            f"(e.g., {{{{grid:3}}}} ... {{{{/grid}}}}). "
                            f"Breakdowns should use {{{{grid:2}}}}. "
                            f"Restructure the markdown and retry."
                        ),
                    )],
                    isError=True,
                )

            # Collect chart specs and SQL queries for referenced charts
            chart_specs: dict = {}
            chart_queries: dict = {}
            missing = []
            for cid in chart_ids_in_content:
                if cid in _chart_registry:
                    chart_specs[cid] = _chart_registry[cid]["option"]
                    chart_queries[cid] = {
                        "sql": _chart_registry[cid].get("sql", ""),
                        "database": _chart_registry[cid].get("database", "dbt"),
                        "title": _chart_registry[cid].get("title", ""),
                    }
                else:
                    missing.append(cid)

            if missing:
                return CallToolResult(
                    content=[TextContent(
                        type="text",
                        text=(
                            f"Error: Chart IDs not found in registry: {', '.join(missing)}. "
                            f"Available: {', '.join(_chart_registry.keys()) or 'none'}. "
                            f"Use `generate_chart` to create charts first."
                        ),
                    )],
                    isError=True,
                )

            # Convert markdown to HTML
            rendered_html = _markdown_to_html(content_markdown)

            # Generate timestamp (real UTC)
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            # Build structured data for MCP App
            structured = {
                "title": title,
                "timestamp": timestamp,
                "charts": chart_specs,
                "sections_html": rendered_html,
                "queries": chart_queries,
            }

            # Build standalone HTML for disk saves
            html = _build_standalone_html(title, timestamp, chart_specs, rendered_html, chart_queries)

            # Cache the report
            report_id = str(uuid.uuid4())

            # Save to persistent directory
            report_dir = _get_report_dir()
            report_path = report_dir / _report_filename(report_id, title)
            report_path.write_text(html, encoding="utf-8")

            file_uri = _get_report_link(report_path)
            structured["file_uri"] = file_uri

            with _REPORT_LOCK:
                _prune_report_cache()
                _REPORT_CACHE[report_id] = {
                    "html": html,
                    "structured": structured,
                    "expires": datetime.now(timezone.utc) + _REPORT_TTL,
                    "path": report_path,
                    "title": title,
                }

            # Reset workflow state for the next analysis cycle
            state.reset()

            # Reply text for the assistant
            reply_text = (
                f"**Report:** {title}\n\n"
                f"Report ID: `{report_id[:8]}` | "
                f"Charts: {len(chart_specs)}\n\n"
                f"To reopen: `open_report(\"{report_id[:8]}\")`\n"
                f"To export HTML: `export_report(\"{report_id[:8]}\")`\n\n"
                f"_Ask if they want the HTML exported or conversion to docx/pdf/pptx._"
            )

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=reply_text,
                        annotations=Annotations(
                            audience=["assistant"],
                            priority=1.0,
                        ),
                    ),
                    TextContent(
                        type="text",
                        text=(
                            f"Report generated: {title} "
                            f"({len(chart_specs)} charts). "
                            f"Report ID: `{report_id[:8]}`"
                        ),
                    ),
                ],
                structuredContent=structured,
            )

        except Exception as e:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Error: {e}")],
                isError=True,
            )

    # --- Report Reopen & List ---

    @mcp.tool(meta={
        "ui": {"resourceUri": REPORT_URI},
        "ui/resourceUri": REPORT_URI,
    })
    def open_report(report_ref: str) -> CallToolResult:
        """Reopen a previously generated report by its ID.

        Accepts the full UUID or the 8-character short ID shown in report
        summaries. Returns the same interactive UI resource as generate_report.

        CRITICAL: After this tool returns, your reply MUST include:
        1. The file:// report link (copy it verbatim from the response)
        2. A brief summary
        Do NOT echo the report markdown.

        Args:
            report_ref: Full report UUID or 8-character prefix.

        Returns:
            Interactive UI resource of the saved report.
        """
        def _build_result(title: str, file_uri: str, report_id: str,
                          structured: dict | None, extra: str = "") -> CallToolResult:
            content_items = []
            if file_uri:
                content_items.append(TextContent(
                    type="text",
                    text=f"**Report:** {title}\n\n**Report link:** [Open Report]({file_uri})",
                    annotations=Annotations(
                        audience=["assistant"],
                        priority=1.0,
                    ),
                ))
                content_items.append(TextContent(type="text", text=file_uri))
                if structured is not None:
                    structured["file_uri"] = file_uri
            metadata = (
                f"Report ID: `{report_id[:8]}`"
                + (f"\n\n{extra}" if extra else "")
            )
            content_items.append(TextContent(type="text", text=metadata))
            return CallToolResult(
                content=content_items,
                structuredContent=structured,
            )

        # Try in-memory cache first (full UUID)
        with _REPORT_LOCK:
            cached = _REPORT_CACHE.get(report_ref)
        if cached:
            file_uri = _get_report_link(cached["path"]) if cached.get("path") else ""
            return _build_result(
                cached.get("title", "Report"), file_uri, report_ref,
                cached.get("structured"),
            )

        # Try cache by prefix
        with _REPORT_LOCK:
            prefix_matches = [
                (rid, data) for rid, data in _REPORT_CACHE.items()
                if rid.startswith(report_ref)
            ]
        if len(prefix_matches) == 1:
            rid, data = prefix_matches[0]
            file_uri = _get_report_link(data["path"]) if data.get("path") else ""
            return _build_result(
                data.get("title", "Report"), file_uri, rid,
                data.get("structured"),
            )
        if len(prefix_matches) > 1:
            ids = ", ".join(f"`{rid[:8]}`" for rid, _ in prefix_matches)
            return CallToolResult(
                content=[TextContent(
                    type="text",
                    text=f"Ambiguous report reference `{report_ref}`. Matches: {ids}",
                )],
            )

        # Fallback: disk lookup
        disk_path = _find_report_on_disk(report_ref)
        if disk_path:
            html = disk_path.read_text(encoding="utf-8")
            full_id = _extract_report_id_from_path(disk_path)
            file_uri = _get_report_link(disk_path)
            # Try to extract structured data from embedded JSON
            structured = _extract_structured_from_html(html)
            return _build_result(
                structured.get("title", "Report") if structured else "Report",
                file_uri, full_id, structured,
                extra=f"Reopened from disk: `{disk_path.name}`",
            )

        return CallToolResult(
            content=[TextContent(
                type="text",
                text=f"Report `{report_ref}` not found in cache or on disk. Use `list_reports` to see available reports.",
            )],
        )

    @mcp.tool()
    def list_reports(limit: int = 20) -> str:
        """List previously generated reports saved on disk.

        Returns a table of saved reports sorted newest-first with file:// links.
        Use `open_report(report_id)` to reopen any report.

        Args:
            limit: Maximum number of reports to show (default 20).

        Returns:
            Table of saved reports with IDs, dates, sizes, and links.
        """
        report_dir = Path(
            os.environ.get("CEREBRO_REPORT_DIR", "~/.cerebro/reports")
        ).expanduser()
        if not report_dir.exists():
            return "No report directory found. Generate a report first with `generate_report`."

        html_files = sorted(
            report_dir.glob("cerebro_report_*.html"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not html_files:
            return "No saved reports found. Generate a report first with `generate_report`."

        lines = ["# Saved Reports\n"]
        lines.append("| # | Report ID | Title | Created (UTC) | Size | Link |")
        lines.append("|---|-----------|-------|---------------|------|------|")

        for i, f in enumerate(html_files[:limit], 1):
            stat = f.stat()
            modified = datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M")
            size_kb = stat.st_size / 1024
            file_uri = _get_report_link(f)
            # Parse filename for ID and slug
            full_id = _extract_report_id_from_path(f)
            short_id = full_id[:8]
            # Extract slug from filename for title hint
            parts = f.stem.split("_")
            slug = parts[4] if len(parts) >= 5 else ""
            title_hint = slug.replace("-", " ").title() if slug else "—"
            lines.append(
                f"| {i} | `{short_id}` | {title_hint} | {modified} "
                f"| {size_kb:.0f} KB | {file_uri} |"
            )

        if len(html_files) > limit:
            lines.append(f"\n_Showing {limit} of {len(html_files)} reports._")

        lines.append(f"\nReport directory: `{report_dir}`")
        lines.append("\nTo reopen: `open_report(\"<report_id>\")`")
        return "\n".join(lines)

    # --- Export Report as HTML ---

    @mcp.tool()
    def export_report(report_ref: str = "") -> str:
        """Export a report as standalone HTML that can be saved and opened in any browser.

        Returns the full self-contained HTML content. Save it to a .html file
        to view the interactive report offline — no server needed.

        Args:
            report_ref: Report ID (full UUID or 8-char prefix). Empty = latest report.

        Returns:
            Full HTML string of the standalone report.
        """
        # Try in-memory cache
        if report_ref:
            with _REPORT_LOCK:
                cached = _REPORT_CACHE.get(report_ref)
            if cached and cached.get("html"):
                return cached["html"]

            # Try prefix match
            with _REPORT_LOCK:
                prefix_matches = [
                    (rid, data) for rid, data in _REPORT_CACHE.items()
                    if rid.startswith(report_ref)
                ]
            if len(prefix_matches) == 1:
                return prefix_matches[0][1]["html"]
            if len(prefix_matches) > 1:
                ids = ", ".join(f"`{rid[:8]}`" for rid, _ in prefix_matches)
                return f"Ambiguous report reference `{report_ref}`. Matches: {ids}"

            # Disk fallback
            disk_path = _find_report_on_disk(report_ref)
            if disk_path:
                return disk_path.read_text(encoding="utf-8")

            return (
                f"Report `{report_ref}` not found. "
                f"Use `list_reports` to see available reports."
            )

        # No ref → latest report from cache or disk
        with _REPORT_LOCK:
            if _REPORT_CACHE:
                latest = max(
                    _REPORT_CACHE.values(),
                    key=lambda v: v.get("expires", datetime.min.replace(tzinfo=timezone.utc)),
                )
                if latest.get("html"):
                    return latest["html"]

        # Disk: most recent file
        report_dir = Path(
            os.environ.get("CEREBRO_REPORT_DIR", "~/.cerebro/reports")
        ).expanduser()
        if report_dir.exists():
            files = sorted(
                report_dir.glob("cerebro_report_*.html"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if files:
                return files[0].read_text(encoding="utf-8")

        return "No reports found. Generate a report first with `generate_report`."

    # --- MCP App Resource ---

    @mcp.resource(
        REPORT_URI,
        mime_type="text/html;profile=mcp-app",
    )
    def serve_report_app() -> str:
        """Serves the MCP App HTML for interactive report rendering.

        Returns the Vite-built single-file React app. All assets (JS, CSS,
        fonts, watermarks) are inlined — no external network requests needed.
        """
        return _get_report_html()
