---
id: eval-single-id-pin-on-shared-symptom
title: >-
  A retrieval-eval scenario that pins ONE lesson id on symptom wording a whole
  incident cluster shares breaks when the corpus grows — and widening the
  tuple without a unique re-pin silently unpins the lesson
status: observed
layer: testing
scope: >-
  the retrieval evals — tests/test_agent_knowledge_eval.py (dbt corpus, built
  in the sibling repo, grows with zero local diff) and RETRIEVAL_CASES in
  tests/test_cerebro_lessons.py (repo-local); any top-k assertion over a
  corpus another writer can grow
symptom: >-
  an eval scenario fails after the sibling lessons corpus was rebuilt — the
  expected lesson dropped out of the top 3 although its own score did not
  change; nothing in this repo changed and no local diff touched the eval or
  the ranker
last_verified: 2026-08-26
evidence:
  - >-
    verified 2026-08-26 against agent_context.public.json (38 lessons), query
    "decoded events missing after raw backfill" — raw-logs-ingestion-holes
    13.0, backfill-order-cumulative 11.0, event-field-can-lie 10.0,
    decode-watermark-late-logs 9.0 (rank 4 by id tiebreak; its score is
    unchanged from when the scenario passed — five lessons added to the dbt
    corpus 2026-08-20..25 crowded it out)
  - >-
    verified 2026-08-26 — the only other scenario naming
    decode-watermark-late-logs ("negative token balances real holder") is a
    tuple satisfied by frontier-day-incomplete-inputs at 16.0;
    decode-watermark is not even in its top 5, so widening the drifted tuple
    alone would have left the lesson with no retrievability guarantee at all
  - >-
    verified 2026-08-26 — re-pin query "logs arrived late missing from
    decoded models" ranks decode-watermark-late-logs #1 at 19.5 with the
    runner-up (raw-logs-ingestion-holes, 14.5) being the same-incident
    lesson, so a future drift degrades gracefully
  - >-
    tests/test_agent_knowledge_eval.py — widened tuple + unique re-pin
    scenario (fix in tree, pending commit)
---
## Symptom

`test_correct_lesson_in_top3` fails for one scenario after the sibling repo
rebuilt `target/agent_context.public.json`. The expected lesson's score is
identical to when the scenario passed — newly added lessons outscored it. No
commit in this repo is anywhere near the eval, the loader, or the ranker.

## Root cause

Two compounding design gaps. (1) The scenario pinned a single lesson id on
symptom wording that a whole incident cluster legitimately shares: "decoded
events missing after raw backfill" describes the same incident as
`raw-logs-ingestion-holes`, whose evidence names the raw backfill landing
below the append watermarks — so records that are *correct answers* compete
with the pinned one, and the pin holds only while nothing else scores near
it (the pinned lesson sat tied at 9.0, kept in the top 3 by id tiebreak).
(2) The corpus is built in a sibling repo, so the ranking drifts with zero
local diff and no review surface — the failure arrives with someone else's
lesson-writing, exactly like a base table another writer grows.

## Forbidden action

Do not "fix" the drift by making the ranker smarter for one query: a
simulated light-stemming fix passed all 47 ranking assertions across both
corpora but left the target at rank 3 with a 0.5-point margin — it re-fixes
the symptom, not the fragility, reshuffles top-3 lists everywhere, and cuts
against the ranker's deliberately closed-class design. And never widen an
acceptable-ids tuple without first checking where the displaced lesson is
still uniquely pinned — if its only other appearances are tuples satisfied
by other lessons, the widening silently removes its retrievability
guarantee while the eval stays green.

## Detection

Recompute scores with `score_lessons` against the live artifact: the
expected id scoring the same as before the rebuild, displaced by recent
additions or same-incident records, is this class (a ranker regression
changes the score). Then audit tuples: for every lesson id the eval means to
guarantee, at least one scenario must be satisfiable only by it.

## Safe remediation

Widen the tuple only when the surfacing lesson genuinely owns the same
symptom — the in-file precedent wording is "Two lessons legitimately own
this symptom" — and cite the shared-incident evidence in the comment. Then
ADD a scenario that re-pins the displaced lesson under wording only it owns,
verified rank 1 with margin against the live artifact before landing.

## Enforcement

None yet — a hermetic gate would need a pinned corpus fixture that itself
rots, and the dbt corpus is deliberately consumed live. The guard is
procedural: this record, plus the widened-tuple + unique-re-pin comment pair
in `tests/test_agent_knowledge_eval.py` that models the pattern.
