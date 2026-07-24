---
{
  "id": "export_conversion",
  "label": "Export a report",
  "purpose": "Turn the session's latest report into a standalone shareable file.",
  "category": "utility",
  "tier": "full_report",
  "deliverable": "A standalone exported HTML file (path or download link) of the latest report — building a minimal gate-compliant dashboard first if the session has none.",
  "params": [
    {"name": "TOPIC", "description": "Fallback topic if no report exists yet in this session", "example": "Gnosis Chain daily activity"}
  ],
  "personas": [],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1200, "budget_usd": 8.0, "verify": "export"}
}
---

Using the cerebro tools, export the latest report of this session as a standalone file.

1. Check `list_reports()`. If a report exists, skip to step 3.
2. If the session has NO report yet, build the smallest gate-compliant dashboard on {{TOPIC}} first: `preflight_analytics_request(query, mode="report")` → `search_models` (limit 15) → `get_model_details` on 3 models → `describe_table` → 2 `execute_query` calls (one statistical) → ONE coverage sweep (`exclude_all_discovered_except`) → ONE `generate_charts` batch (3 charts: KPI card, trend, one `series_field` breakdown or heatmap) → `generate_report`.
3. `export_report()` (empty ref = latest) and reply with the exported file's path or download link.

Keep it minimal — the export is the deliverable, not the analysis.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
