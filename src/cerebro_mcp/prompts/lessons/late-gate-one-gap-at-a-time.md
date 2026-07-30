---
id: late-gate-one-gap-at-a-time
title: A gate that fires late and reports one gap at a time gets abandoned, not satisfied
status: enforced
layer: governance
scope: tools/governance/session_state.py, tools/visualization/charts.py
symptom: the model does all the analysis, then delivers markdown files or prose instead of the report artifact; no report appears in list_reports
last_verified: 2026-07-31
evidence: |
  An 11-year-anniversary campaign session ran 28 `execute_query` calls, 5
  `discover_models`, 7 `describe_table` and built 7 charts, then called
  `generate_case_study_report`. The gate returned exactly one complaint —
  "No dimensional breakdown". The session abandoned the report and wrote two
  markdown files to disk instead. `list_reports` showed nothing newer than
  the previous day.

  Reproducing that session's state against the gate showed it actually had
  TWO unmet requirements (dimensional breakdown AND relational analysis), so
  fixing the reported one would have produced a second rejection.
---

## Symptom

A session does the whole analysis correctly and then does not produce the
artifact. The user gets prose, markdown files, or a promise to build the report
later. Nothing appears in `list_reports`. From the transcript it reads like the
model "forgot" to call the tool, but it called it and was refused.

## Root cause

Two properties compound:

1. **The gate fires last.** `check_report_preconditions` runs at
   `generate_report` time, so composition requirements (a `series_field` chart,
   a scatter/heatmap or a correlation query) are only discoverable AFTER every
   chart has been built. Learning "add a dimensional chart" once seven flat
   time series exist means going back and building another.
2. **It reported one gap per call.** Each requirement was its own
   `return False, ...`, so the caller could not see the total cost of
   compliance. Satisfying the named gap surfaced the next one.

A caller facing an unknown number of sequential rejections, at the end of a long
and expensive session, will rationally stop and deliver what it already has. The
gate was doing its job and still produced a worse outcome than no gate.

## Forbidden action

Do not read "the model ignored the report tool" from a missing report. Check
whether the tool was called and refused — the trace under `THINKING_LOG_DIR`
records the gate's exact message.

Do not add a new blocking requirement to a late gate as a bare early `return`.

## Detection

- `list_reports` empty while the session trace shows `generate_*_report` with an
  `error` field.
- Session trace shows many `generate_chart` (singular) calls instead of one
  `generate_charts` batch — a sign the caller was improvising around a gate it
  did not understand.

## Safe remediation

Collect every unmet requirement into a list and return them together, with an
explicit next action. `_format_chart_gate_reason` already did this for the chart
gate; `_format_report_gate_reason` now does it for the report gate. Keep the
single-gap case verbatim so existing substring assertions survive.

The message must name what to do, not only what is wrong, because it lands at
the point of maximum temptation to give up — including naming the observed
failure mode ("do not fall back to writing markdown files").

Better still, surface composition requirements BEFORE the charts are built, so
the caller plans a `series_field` chart rather than discovering the need after
the fact. Not yet done.

## Enforcement

`tests/test_report_gate_bundles_gaps.py` reproduces the incident's exact state
(28 queries, 7 flat line charts, no `series_field`, no correlation) and asserts
both gaps appear in one message, that the message names `generate_charts` and
"retry", that a single gap is still returned verbatim, and that the gate passes
once the gaps are filled.

Related: [[silenced-write-can-still-block]] — a different way the same session
loses its work.
