---
id: published-is-not-served
title: >-
  A raw census_publications row is not a served snapshot — resolve dates from
  v_publications_current, or a published-but-ineligible day empties the panel
status: observed
layer: sql
scope: >-
  every read of an rpc-state-indexer plane that resolves its dates (as-of,
  month-ends) from census_publications, or joins a per-target publication fact
  from it, beside a published view or the raw balance tables — the governance
  treasury plane was the paid instance; the pools plane (queries/pools/) carried
  the same premise and a live fan-out from it, fixed 2026-09-26
symptom: >-
  a month vanishes from a history chart or a whole as-of panel comes back empty
  with no error, while census_publications clearly shows publications for that
  date; raw publication counts per token-day are sometimes double; the canonical
  view has no rows for dates the publications table lists; counts joined over a
  per-pool publication fact are inflated on some days and not others; an as-of
  panel shows fewer pools for hours every morning than it does in the evening
last_verified: 2026-09-26
evidence:
  - >-
    verified 2026-09-25: Ethereum daily_treasury had 891-895 raw publications per
    day for 2026-07-28..2026-08-15 and 0 eligible (v_publications_eligible) —
    one gap-fill run on 2026-09-12 had stamped an interim config hash
    (e7974681… vs the registry's 83a6b5b3…), and v_publications_eligible inner-
    joins the registry on config_hash. The app picked 2026-07-31 (raw max) as
    July's month-end and the July bucket vanished for every Ethereum wallet
  - >-
    verified 2026-09-25 (Gnosis): of 2,251 raw-published days, 47 served 0
    tokens and 166 served <98%; EURe/GBPe v1 were eligible on 35/17 of ~1,000
    days since 2023-12; GNO/SAFE/sDAI/USDC.e had 16-22 scattered unserved days
    each. Taking each token's latest SERVED day within the month's last 7 raw
    days leaves 0 gap months on both chains
  - >-
    verified 2026-09-25: re-census publications duplicate raw rows (1,798 raw =
    2 x 899 served on 2026-09-14..24), so any raw count must be
    uniqExact(target_address), never count()
  - src/cerebro_mcp/tools/visualization/queries/governance/_cte_treasury_asof.sql
  - src/cerebro_mcp/tools/visualization/queries/governance/_cte_treasury_month_served.sql
  - src/cerebro_mcp/tools/visualization/queries/governance/treasury_history_coverage.sql
  - tests/test_governance_explorer.py::test_treasury_dates_resolve_from_served_publications_and_every_scan_is_pruned
  - tests/test_governance_live_smoke.py::test_treasury_two_step_read_equals_the_canonical_view_at_as_of
  - >-
    verified 2026-09-26 (pools plane): raw census_publications carries duplicate
    pool-days — 20 CL days (635 extra rows) and 256 reserves days (148,386) — and
    the probe-flag CTE every as-of dataset LEFT JOINs by pool was not pinned to
    an attempt, so at 2026-09-10 pools_summary counted 4,215 configured / 2,714 CL
    pools against a 4,022 / 2,521 registry, the directory listed 193 pools twice,
    WXDAI's token page counted 161 pools (157 real) and summed their reserves
    twice, and a pool's history drew the re-censused days twice
  - >-
    verified 2026-09-26 (pools plane): served == raw on every CL day of history
    (874,228 pool-days) and on all but 10 reserves days (1 pool each), so the
    premise held by luck; but the raw max(snapshot_date) resolved the CL as-of to a
    day still being written — the job publishes over 1-4.5 hours every morning —
    and a requested 2026-08-23 (1,082 of 2,519 pools, never completed) to that
    partial day. The served complete-day resolver returns 2026-08-22
  - >-
    verified 2026-09-26: v_publications_current OOMs at 2 GiB scanned unbounded
    over the two pool jobs' history, and each bounded read carries ~0.25s of fixed
    eligibility overhead (census_errors / census_attempts FINAL) whatever the date
    count — read it for candidate days, one resolved date, or one pool only
  - src/cerebro_mcp/tools/visualization/queries/pools/_cte_asof.sql
  - src/cerebro_mcp/tools/visualization/queries/pools/_cte_probe_flags.sql
  - tests/test_pools_explorer.py::test_dates_resolve_from_served_publications_and_every_view_scan_is_pruned
  - tests/test_pools_explorer_live_smoke.py::test_the_as_of_is_the_newest_complete_served_day
  - tests/test_pools_explorer_live_smoke.py::test_a_re_censused_day_fans_no_pool_out
  - >-
    fix in tree 2026-09-25 (treasury) and 2026-09-26 (pools), pending deploy —
    status stays observed until merged
---
## Symptom

A history chart is missing a month, or an as-of panel is empty, and nothing
errors. Querying `census_publications` shows the date plainly published for
hundreds of tokens. The canonical `v_*_published` / `v_treasury_balances` view has
no rows for it. Raw publication counts per token-day are sometimes exactly double.

## Root cause

`census_publications` records every publication an attempt wrote. What the
indexer SERVES is narrower: `v_publications_eligible` inner-joins the config
registry on `config_hash`, the canonical day anchor, a verified attempt with no
errors; `v_publications_current` further drops conflicting signatures. A
publication written under a config hash that is no longer registered (an interim
hash from a since-reverted change), or whose attempt failed a check, is present
in the raw table and absent from every served view. The earlier record
fat-view-join-never-prunes asserted "a date exists in the view iff published" —
that premise is false, and code built on it resolves a date the view cannot serve.
Re-censuses add a second raw row per token-day, so raw counts double.

## Forbidden action

Never resolve an as-of or month-end date from raw `census_publications` alone for
a read of served data, never count raw publications with `count()`, and never
JOIN a per-target publication fact (a check verdict, an attempt, an anchor) from
the raw table without pinning it to the served attempt — a re-census row fans
every target it covers into two joined rows. Do not "fix" a vanished month by
widening a date window on the raw table.

## Detection

For the affected date, compare
`uniqExact(target_address)` over `census_publications` with the same over
`v_publications_current` (restricted to the date — cheap). A served count far
below the raw count, or zero, is this class. `v_coverage_calendar` reports such
days as `missing`.

## Safe remediation

Resolve dates from SERVED publications: the as-of is the latest complete served
day (served >= ratio x max(raw that day, window peak)), with per-token carry from
the token's own latest served day, counted; month-ends take each token's latest
served day within the month's last few raw candidate days. Where one snapshot must
be whole (the pools plane — a pool carried from another day would mix dates in one
panel) there is no carry: the as-of walks back to the newest complete candidate,
judged against a TRAILING peak so a genuine universe shrink lags by at most the
trailing window instead of pinning the as-of until the old peak ages out. Pin every
per-target fact read from the raw table to the served attempt — `(target,
attempt_id) IN (served ...)` for a snapshot, a scalar `has(groupArray(...))` array
for one target's calendar — and keep one row per key by construction
(`LIMIT 1 BY`). Where the served view already holds the fact, read it there:
`tick_count > 0` on a served CL state row IS that attempt's probe verdict. Read balances for
exactly those served attempts (job pin + constant date bound + 4-tuple IN on
attempt_id, argMax dedup — see ch-final-three-way-rule). Disclose per-month
completeness (published vs served vs carried; gap / partial / unpublished) instead
of drawing a gap as a zero. Upstream, re-census the hidden days scoped by
publication TIME, not snapshot date — the interim hash was stamped on older dates
that backfills filled during the window.

## Enforcement

Hermetic: test_treasury_dates_resolve_from_served_publications_and_every_scan_is_pruned
rejects raw `max(snapshot_date) AS as_of|month_end` resolution and any
token_balances read without served attempts, and proves it on a negative fixture
of the old shape. The pools twin,
test_dates_resolve_from_served_publications_and_every_view_scan_is_pruned,
classifies EVERY raw-publications read in every pools spec as either pinned to the
served attempt or a distinct count (an unclassified read fails), rejects raw as-of
resolution and row counts, and carries its own negative fixture; it was also run
against deliberately broken fragments (unpinned probe, IN prune, countIf) and
failed on each. Live: the treasury smoke proves the served two-step read equals
the canonical view at as-of (P1) and every served attempt's balances sum to its
own observed_sum_raw across history (P2); the pools smoke re-derives the
complete-day rule in Python from one query's ingredients (it fails on 2026-08-23
under the old raw-max rule) and checks the most recent re-censused day fans no
pool out. Enforced once deployed.
