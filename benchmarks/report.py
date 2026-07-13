"""Generate a self-contained HTML dashboard for the benchmark result files.

    uv run python -m benchmarks.report [--out benchmarks/results/index.html]

Reads every ``benchmarks/results/*.json`` and renders one explorable page:
an overview of all runs, a suite-tailored table per run (latency budgets,
semantic coverage + stage timings, search hit@k, workflow call efficiency,
load concurrency scaling), and a per-tool latency trend where a suite+mode
has more than one run. No external assets — inline CSS/JS, theme-aware.

Pure stdlib; imports nothing from ``cerebro_mcp`` (safe to run anytime).
"""

from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Status palette (dataviz reference — fixed, never themed) + neutral for skips.
_STATUS_COLOR = {
    "ok": "var(--good)",
    "over_budget": "var(--warning)",
    "error": "var(--critical)",
    "skipped": "var(--muted)",
}
_STATUS_LABEL = {
    "ok": "ok",
    "over_budget": "over budget",
    "error": "error",
    "skipped": "skipped",
}


# ── loading ──────────────────────────────────────────────────────────


def load_runs(results_dir: Path) -> list[dict[str, Any]]:
    runs = []
    for path in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        if "suite" not in data or "cases" not in data:
            continue
        data["_file"] = path.name
        runs.append(data)
    runs.sort(key=lambda d: d.get("started_at", ""))
    return runs


# ── small html helpers ───────────────────────────────────────────────


def esc(v: Any) -> str:
    return html.escape(str(v), quote=True)


def fmt_ms(v: Any) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    if v == 0:
        return "0"
    if v < 1:
        return f"{v:.2f}"
    if v < 100:
        return f"{v:.1f}"
    return f"{v:.0f}"


def pct(v: Any) -> str:
    return f"{v * 100:.0f}%" if isinstance(v, (int, float)) else "—"


def status_pill(status: str) -> str:
    color = _STATUS_COLOR.get(status, "var(--muted)")
    label = _STATUS_LABEL.get(status, status)
    return f'<span class="pill" style="--pill:{color}">{esc(label)}</span>'


def budget_bar(p50: float | None, budget: float | None, status: str) -> str:
    """Horizontal p50-vs-budget bar. Track = budget; fill = p50 (clamped).
    Over-budget rows overflow the track and carry the warning hue."""
    if not isinstance(p50, (int, float)) or not isinstance(budget, (int, float)) or budget <= 0:
        return '<span class="muted">—</span>'
    ratio = p50 / budget
    fill = min(100.0, ratio * 100.0)
    color = "var(--warning)" if ratio > 1 else "var(--series-1)"
    over = f'<span class="over">{ratio:.1f}×</span>' if ratio > 1 else ""
    return (
        f'<span class="bar"><span class="bar-fill" '
        f'style="width:{fill:.0f}%;background:{color}"></span></span>{over}'
    )


def sparkline(values: list[float], *, width: int = 96, height: int = 22) -> str:
    """Inline SVG sparkline for a per-case p50 trend across runs."""
    pts = [v for v in values if isinstance(v, (int, float))]
    if len(pts) < 2:
        return '<span class="muted">—</span>'
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    step = width / (len(pts) - 1)
    coords = [
        (i * step, height - 2 - (v - lo) / span * (height - 4))
        for i, v in enumerate(pts)
    ]
    path = " ".join(
        ("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}" for i, (x, y) in enumerate(coords)
    )
    last_up = pts[-1] > pts[0]
    dot_color = "var(--warning)" if last_up else "var(--good)"
    cx, cy = coords[-1]
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
        f'<path d="{path}" fill="none" stroke="var(--series-1)" stroke-width="1.5"/>'
        f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2.4" fill="{dot_color}"/></svg>'
    )


def tile(label: str, value: str, *, sub: str = "", tone: str = "") -> str:
    tone_cls = f" tile-{tone}" if tone else ""
    sub_html = f'<div class="tile-sub">{esc(sub)}</div>' if sub else ""
    return (
        f'<div class="tile{tone_cls}"><div class="tile-val">{value}</div>'
        f'<div class="tile-label">{esc(label)}</div>{sub_html}</div>'
    )


# ── per-suite bodies ─────────────────────────────────────────────────


def _run_summary_tiles(run: dict) -> str:
    s = run["summary"]
    tiles = [tile("cases", str(s.get("cases", 0)))]
    if s.get("ok"):
        tiles.append(tile("ok", str(s["ok"]), tone="good"))
    if s.get("over_budget"):
        tiles.append(tile("over budget", str(s["over_budget"]), tone="warning"))
    if s.get("skipped"):
        tiles.append(tile("skipped", str(s["skipped"]), tone="muted"))
    if s.get("error"):
        tiles.append(tile("error", str(s["error"]), tone="critical"))
    return '<div class="tiles">' + "".join(tiles) + "</div>"


def body_latency(run: dict) -> str:
    rows = []
    cases = sorted(
        run["cases"],
        key=lambda c: (c["status"] == "skipped", -(c.get("stats") or {}).get("p50", 0)),
    )
    for c in cases:
        st = c.get("stats") or {}
        name = c["id"].split("/", 1)[-1]
        if c["status"] == "skipped":
            rows.append(
                f'<tr class="skip"><td class="mono">{esc(name)}</td>'
                f'<td colspan="4" class="muted">{esc(c.get("skip_reason") or "skipped")}</td>'
                f'<td>{status_pill("skipped")}</td></tr>'
            )
            continue
        rows.append(
            f"<tr><td class='mono'>{esc(name)}</td>"
            f"<td class='num'>{fmt_ms(st.get('p50'))}</td>"
            f"<td class='num'>{fmt_ms(st.get('p95'))}</td>"
            f"<td class='num muted'>{fmt_ms(c.get('budget_ms'))}</td>"
            f"<td class='barcell'>{budget_bar(st.get('p50'), c.get('budget_ms'), c['status'])}</td>"
            f"<td>{status_pill(c['status'])}</td></tr>"
        )
    return (
        '<table class="grid"><thead><tr>'
        "<th>tool</th><th class='num'>p50 ms</th><th class='num'>p95 ms</th>"
        "<th class='num'>budget</th><th>p50 / budget</th><th>status</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def body_semantic(run: dict) -> str:
    coverage, routing, stages = [], None, []
    for c in run["cases"]:
        m = c.get("meta") or {}
        if m.get("kind") == "coverage":
            coverage.append((m, c))
        elif "distribution" in m:
            routing = m["distribution"]
        elif c.get("samples_ms"):
            stages.append(c)

    out = []

    if coverage:
        tiles = []
        for m, c in coverage:
            val = m.get("value")
            stat = m.get("stat", c["id"])
            direction = m.get("direction", "")
            if stat.startswith("pct_"):
                shown = f"{val:.0f}%" if isinstance(val, (int, float)) else "—"
            else:
                shown = f"{val:,.0f}" if isinstance(val, (int, float)) else "—"
            tone = ""
            if direction == "must_be_zero":
                tone = "good" if val == 0 else "critical"
            elif stat == "pct_models_with_entities" and isinstance(val, (int, float)):
                tone = "warning" if val < 60 else ""
            tiles.append(tile(stat.replace("_", " "), shown, tone=tone))
        out.append('<h4>registry health</h4><div class="tiles">' + "".join(tiles) + "</div>")

    if routing:
        total = sum(routing.values()) or 1
        seg = []
        colors = {
            "semantic_ready": "var(--good)",
            "hybrid_ready": "var(--series-1)",
            "semantic_coverage_gap": "var(--muted)",
        }
        legend = []
        for route, n in routing.items():
            w = n / total * 100
            seg.append(
                f'<span class="seg" style="width:{w:.1f}%;background:{colors.get(route, "var(--series-3)")}" '
                f'title="{esc(route)}: {n}"></span>'
            )
            legend.append(
                f'<span class="lg"><span class="dot" style="background:{colors.get(route, "var(--series-3)")}"></span>'
                f'{esc(route)} <b>{n}</b></span>'
            )
        out.append(
            "<h4>routing distribution — 40 pinned queries</h4>"
            f'<div class="stack">{"".join(seg)}</div>'
            f'<div class="legend">{"".join(legend)}</div>'
        )

    if stages:
        rows = []
        for c in sorted(stages, key=lambda x: -(x.get("stats") or {}).get("p50", 0)):
            st = c.get("stats") or {}
            rows.append(
                f"<tr><td class='mono'>{esc(c['id'].replace('semantic.', ''))}</td>"
                f"<td class='num'>{fmt_ms(st.get('p50'))}</td>"
                f"<td class='num'>{fmt_ms(st.get('p95'))}</td>"
                f"<td class='num muted'>{fmt_ms(c.get('budget_ms'))}</td>"
                f"<td>{status_pill(c['status'])}</td></tr>"
            )
        out.append(
            "<h4>stage latency</h4>"
            '<table class="grid"><thead><tr><th>stage</th><th class="num">p50 ms</th>'
            '<th class="num">p95 ms</th><th class="num">budget</th><th>status</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        )
    return "".join(out)


def body_search(run: dict) -> str:
    aggs, errors = [], []
    for c in run["cases"]:
        if c["id"].startswith("search/agg/"):
            aggs.append(c)
        elif c["status"] == "error":
            errors.append(c)
    rows = []
    for c in sorted(aggs, key=lambda x: x["id"]):
        m = c.get("meta") or {}
        surface = c["id"].replace("search/agg/", "")
        rows.append(
            f"<tr><td class='mono'>{esc(surface)}</td>"
            f"<td class='num'>{pct(m.get('hit1'))}</td>"
            f"<td class='num'>{pct(m.get('hit3'))}</td>"
            f"<td class='num'>{pct(m.get('hit5'))}</td>"
            f"<td class='num'>{m.get('mrr', 0):.3f}</td>"
            f"<td class='num muted'>{m.get('n', '—')}</td></tr>"
        )
    table = (
        '<table class="grid"><thead><tr><th>surface</th><th class="num">hit@1</th>'
        '<th class="num">hit@3</th><th class="num">hit@5</th><th class="num">MRR</th>'
        '<th class="num">n</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table>"
    )
    note = ""
    if errors:
        note = f'<p class="muted">{len(errors)} case(s) below hit@5 — routing/precision failures.</p>'
    return table + note


def body_workflows(run: dict) -> str:
    rows = []
    for c in run["cases"]:
        m = c.get("meta") or {}
        name = c["id"].replace("workflows/", "")
        calls, opt = m.get("tool_calls"), m.get("optimal_calls")
        over = isinstance(calls, int) and isinstance(opt, int) and calls > opt
        call_cell = f"{calls}/{opt}" if opt is not None else str(calls)
        call_cls = " class='num warn'" if over else " class='num'"
        unexp = m.get("gate_blocks_unexpected", 0)
        rows.append(
            f"<tr><td class='mono'>{esc(name)}</td>"
            f"<td{call_cls}>{esc(call_cell)}</td>"
            f"<td class='num'>{m.get('total_response_chars', '—'):,}</td>"
            f"<td class='num muted'>{m.get('est_tokens', '—'):,}</td>"
            f"<td class='num'>{m.get('gate_blocks_expected', 0)}</td>"
            f"<td class='num'>{'<b class=bad>' + str(unexp) + '</b>' if unexp else 0}</td>"
            f"<td>{status_pill(c['status'])}</td></tr>"
        )
    return (
        '<table class="grid"><thead><tr><th>workflow</th><th class="num">calls / optimal</th>'
        '<th class="num">resp chars</th><th class="num">~tokens</th>'
        '<th class="num">blocks exp</th><th class="num">unexp</th><th>status</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def body_load(run: dict) -> str:
    # group by workload -> {concurrency: case}
    by_wl: dict[str, list[dict]] = defaultdict(list)
    for c in run["cases"]:
        parts = c["id"].split("/")
        wl = parts[1] if len(parts) > 1 else c["id"]
        by_wl[wl].append(c)
    rows = []
    for wl in sorted(by_wl):
        for c in sorted(by_wl[wl], key=lambda x: x["id"]):
            m = c.get("meta") or {}
            st = c.get("stats") or {}
            conc = c["id"].split("/")[-1]
            ttfb = (m.get("ttfb_ms") or {}).get("p50")
            err = m.get("error_rate", 0)
            rows.append(
                f"<tr><td class='mono'>{esc(wl)}</td><td class='mono'>{esc(conc)}</td>"
                f"<td class='num'>{fmt_ms(st.get('p50'))}</td>"
                f"<td class='num'>{fmt_ms(st.get('p95'))}</td>"
                f"<td class='num'>{fmt_ms(ttfb)}</td>"
                f"<td class='num'>{m.get('throughput_cps', 0):.1f}</td>"
                f"<td class='num'>{m.get('calls', '—')}</td>"
                f"<td class='num {'bad' if err else ''}'>{pct(err)}</td></tr>"
            )
    return (
        '<table class="grid"><thead><tr><th>workload</th><th>conc</th>'
        '<th class="num">call p50</th><th class="num">call p95</th><th class="num">ttfb p50</th>'
        '<th class="num">tput /s</th><th class="num">calls</th><th class="num">err</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


_BODIES = {
    "latency": body_latency,
    "semantic": body_semantic,
    "search": body_search,
    "workflows": body_workflows,
    "load": body_load,
}


# ── trend section (per suite+mode with >1 run) ───────────────────────


def trends_section(runs: list[dict]) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in runs:
        groups[(r["suite"], r["mode"])].append(r)
    blocks = []
    for (suite, mode), grp in sorted(groups.items()):
        if len(grp) < 2 or suite not in ("latency", "semantic"):
            continue
        # per-case p50 series across runs (chronological)
        series: dict[str, list[float]] = defaultdict(list)
        for r in grp:
            for c in r["cases"]:
                st = c.get("stats") or {}
                if c["status"] != "skipped" and isinstance(st.get("p50"), (int, float)):
                    series[c["id"]].append(st["p50"])
        rows = []
        for cid, vals in sorted(series.items()):
            if len(vals) < 2:
                continue
            delta = vals[-1] - vals[0]
            arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "·")
            cls = "up" if delta > 0.5 else ("down" if delta < -0.5 else "flat")
            rows.append(
                f"<tr><td class='mono'>{esc(cid.split('/', 1)[-1].replace('semantic.', ''))}</td>"
                f"<td>{sparkline(vals)}</td>"
                f"<td class='num'>{fmt_ms(vals[0])}</td>"
                f"<td class='num'>{fmt_ms(vals[-1])}</td>"
                f"<td class='num delta {cls}'>{arrow} {fmt_ms(abs(delta))}</td></tr>"
            )
        if not rows:
            continue
        blocks.append(
            f'<div class="card" data-suite="{esc(suite)}"><div class="card-head">'
            f'<h3>{esc(suite)} · {esc(mode)} <span class="muted">— p50 trend across {len(grp)} runs</span></h3></div>'
            '<table class="grid"><thead><tr><th>case</th><th>trend</th>'
            '<th class="num">first</th><th class="num">latest</th><th class="num">Δ ms</th>'
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        )
    if not blocks:
        return ""
    return '<section id="trends"><h2>Trends</h2>' + "".join(blocks) + "</section>"


# ── page assembly ────────────────────────────────────────────────────


def _when(run: dict) -> str:
    ts = run.get("started_at", "")
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts


def render_run_card(run: dict) -> str:
    suite = run["suite"]
    body = _BODIES.get(suite, lambda r: '<p class="muted">no renderer for this suite</p>')(run)
    env = run.get("environment") or {}
    sha = env.get("git_sha", "")
    dirty = " +dirty" if env.get("git_dirty") else ""
    fixture = env.get("fixture") or {}
    src = fixture.get("source")
    src_badge = f'<span class="badge">{esc(src)}</span>' if src else ""
    return (
        f'<div class="card" data-suite="{esc(suite)}">'
        f'<div class="card-head">'
        f'<h3>{esc(suite)} <span class="badge mode">{esc(run["mode"])}</span>{src_badge}</h3>'
        f'<div class="card-meta muted mono">{esc(_when(run))} · {esc(sha)}{dirty} · {esc(run["_file"])}</div>'
        f"</div>"
        f"{_run_summary_tiles(run)}"
        f"{body}"
        f"</div>"
    )


def build_html(runs: list[dict]) -> str:
    suites = sorted({r["suite"] for r in runs})
    # overview tiles
    latest_by = {}
    for r in runs:
        latest_by[(r["suite"], r["mode"])] = r
    n_err = sum(r["summary"].get("error", 0) for r in runs)
    overview = '<div class="tiles">' + "".join([
        tile("runs", str(len(runs))),
        tile("suites", str(len(suites))),
        tile("suite×mode", str(len(latest_by))),
        tile("total errors", str(n_err), tone="critical" if n_err else "good"),
    ]) + "</div>"

    chips = '<button class="chip active" data-filter="all">all</button>' + "".join(
        f'<button class="chip" data-filter="{esc(s)}">{esc(s)}</button>' for s in suites
    )

    # newest first for the cards
    cards = "".join(render_run_card(r) for r in reversed(runs))
    trends = trends_section(runs)

    generated = ""  # Date.now() unavailable in some sandboxes; omit wall clock

    return _TEMPLATE.format(
        overview=overview,
        chips=chips,
        trends=trends,
        cards=cards,
        generated=generated,
    )


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cerebro-MCP Benchmarks</title>
<style>
:root {{
  --plane:#f9f9f7; --surface:#fcfcfb; --text:#0b0b0b; --text2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,0.10);
  --series-1:#2a78d6; --series-3:#eda100;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --plane:#0d0d0d; --surface:#1a1a19; --text:#fff; --text2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --border:rgba(255,255,255,0.10);
    --series-1:#3987e5; --series-3:#c98500;
    --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  }}
}}
:root[data-theme=light] {{
  --plane:#f9f9f7; --surface:#fcfcfb; --text:#0b0b0b; --text2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --border:rgba(11,11,11,0.10); --series-1:#2a78d6; --series-3:#eda100;
}}
:root[data-theme=dark] {{
  --plane:#0d0d0d; --surface:#1a1a19; --text:#fff; --text2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --border:rgba(255,255,255,0.10); --series-1:#3987e5; --series-3:#c98500;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--plane); color:var(--text);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif; font-size:14px; line-height:1.45;
}}
.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
.muted {{ color:var(--muted); }}
header {{
  position:sticky; top:0; z-index:10; background:var(--plane);
  border-bottom:1px solid var(--border); padding:14px 24px;
  display:flex; align-items:center; gap:16px; flex-wrap:wrap;
}}
header h1 {{ font-size:16px; margin:0; font-weight:650; }}
.chips {{ display:flex; gap:6px; flex-wrap:wrap; }}
.chip {{
  border:1px solid var(--border); background:var(--surface); color:var(--text2);
  border-radius:999px; padding:4px 12px; font-size:12.5px; cursor:pointer;
}}
.chip.active {{ background:var(--series-1); color:#fff; border-color:transparent; }}
.spacer {{ flex:1; }}
.theme-btn {{ border:1px solid var(--border); background:var(--surface); color:var(--text2);
  border-radius:8px; padding:4px 10px; cursor:pointer; font-size:12.5px; }}
main {{ max-width:1080px; margin:0 auto; padding:24px; }}
h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
  margin:32px 0 12px; font-weight:650; }}
h4 {{ font-size:12.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
  margin:18px 0 8px; font-weight:650; }}
.tiles {{ display:flex; gap:10px; flex-wrap:wrap; margin:4px 0 14px; }}
.tile {{ background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:10px 14px; min-width:96px; }}
.tile-val {{ font-size:22px; font-weight:640; font-variant-numeric:tabular-nums; }}
.tile-label {{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; }}
.tile-sub {{ font-size:11px; color:var(--text2); margin-top:2px; }}
.tile-good .tile-val {{ color:var(--good); }}
.tile-warning .tile-val {{ color:var(--warning); }}
.tile-critical .tile-val {{ color:var(--critical); }}
.tile-muted .tile-val {{ color:var(--muted); }}
.card {{ background:var(--surface); border:1px solid var(--border); border-radius:14px;
  padding:16px 18px; margin:14px 0; }}
.card-head {{ display:flex; justify-content:space-between; align-items:baseline; gap:12px;
  flex-wrap:wrap; margin-bottom:6px; }}
.card-head h3 {{ margin:0; font-size:15px; font-weight:640; }}
.badge {{ font-size:11px; background:var(--grid); color:var(--text2); border-radius:6px;
  padding:2px 7px; margin-left:6px; font-weight:500; text-transform:none; }}
.badge.mode {{ background:transparent; border:1px solid var(--border); }}
table.grid {{ width:100%; border-collapse:collapse; margin:6px 0 2px; }}
table.grid th {{ text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:.04em;
  color:var(--muted); font-weight:600; padding:6px 10px; border-bottom:1px solid var(--border); }}
table.grid td {{ padding:5px 10px; border-bottom:1px solid var(--grid); }}
table.grid tr:last-child td {{ border-bottom:none; }}
tr.skip td {{ color:var(--muted); }}
.pill {{ font-size:11px; font-weight:600; color:var(--pill); }}
.pill::before {{ content:"●"; margin-right:4px; }}
.bar {{ display:inline-block; width:120px; height:8px; background:var(--grid); border-radius:4px;
  overflow:hidden; vertical-align:middle; }}
.bar-fill {{ display:block; height:100%; border-radius:4px; }}
.barcell .over {{ font-size:11px; color:var(--warning); margin-left:6px; font-weight:600;
  font-family:ui-monospace,monospace; }}
.stack {{ display:flex; height:16px; border-radius:6px; overflow:hidden; margin:4px 0; }}
.seg {{ height:100%; }}
.legend {{ display:flex; gap:16px; flex-wrap:wrap; font-size:12px; color:var(--text2); margin-top:6px; }}
.lg .dot, .legend .dot {{ display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; }}
.spark {{ vertical-align:middle; }}
.delta.up {{ color:var(--warning); }}
.delta.down {{ color:var(--good); }}
.delta.flat {{ color:var(--muted); }}
.warn {{ color:var(--warning); }}
.bad {{ color:var(--critical); }}
.hidden {{ display:none; }}
footer {{ max-width:1080px; margin:0 auto; padding:24px; color:var(--muted); font-size:12px; }}
</style>
</head>
<body>
<header>
  <h1>Cerebro-MCP Benchmarks</h1>
  <div class="chips">{chips}</div>
  <div class="spacer"></div>
  <button class="theme-btn" id="theme">◐ theme</button>
</header>
<main>
  <h2>Overview</h2>
  {overview}
  {trends}
  <h2>Runs</h2>
  {cards}
</main>
<footer>Generated by <span class="mono">benchmarks/report.py</span> · re-run after each benchmark to refresh.{generated}</footer>
<script>
const chips = document.querySelectorAll('.chip');
chips.forEach(ch => ch.addEventListener('click', () => {{
  chips.forEach(c => c.classList.remove('active'));
  ch.classList.add('active');
  const f = ch.dataset.filter;
  document.querySelectorAll('.card[data-suite]').forEach(card => {{
    card.classList.toggle('hidden', f !== 'all' && card.dataset.suite !== f);
  }});
}}));
const tbtn = document.getElementById('theme');
tbtn.addEventListener('click', () => {{
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : (cur === 'light' ? '' : 'dark');
  if (next) document.documentElement.setAttribute('data-theme', next);
  else document.documentElement.removeAttribute('data-theme');
}});
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="benchmarks.report", description=__doc__)
    p.add_argument("--results", default=str(RESULTS_DIR), help="results directory")
    p.add_argument("--out", default=None, help="output HTML path (default: <results>/index.html)")
    args = p.parse_args(argv)

    results_dir = Path(args.results)
    runs = load_runs(results_dir)
    if not runs:
        print(f"no result files found in {results_dir}")
        return 1
    out = Path(args.out) if args.out else results_dir / "index.html"
    out.write_text(build_html(runs))
    print(f"{len(runs)} run(s) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
