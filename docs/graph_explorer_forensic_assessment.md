All claims verified against source and warehouse. Writing the assessment.

---

# Graph Explorer — Closing Assessment (Round 3)

**Scope note:** the scores below grade the *investigation reports*, not the app. A mode can score 87 because it was investigated well and still be unusable. The "usable today" column is my judgment of the software.

---

## 1. Per-mode verdict

| Mode | Report score | Forensically usable today | The one thing that most limits it |
|---|---|---|---|
| **Investigate** | 80 | **Partly** — topology yes, evidence no | `build_evidence_sql` has no `ORDER BY` and **no time predicate at all** (`graph_profiles.py:429-434`). An edge computed over 90d drills into rows from 2021 and 2024. The drill-down does not describe the edge it hangs off. |
| **Flows** | 84 | **Partly** — trustworthy for value, not for counterparties | The 400-node per-hop budget (`constants.py:70`) drops 2,004 of 2,404 hop-1 counterparties (83.4%) with no stub, marker or count. It keeps 91.2% of the money in 16.6% of the addresses. |
| **Timeline** | 87 | **No — actively misleading** | It plots a 365-day axis over a node set chosen on a 90-day window, and reports `warnings=[]`. At default settings it says **growth** (+8.2/wk OLS) when ground truth is **decline** (-10.4/wk). |
| **Atlas** | 87 | **N/A — it is not a forensic surface** | It is a catalog browser with a canvas bolted on. 7 of 12 profiles have constant weight, so the canvas renders a uniformly-weighted hairball carrying no information. |
| **TX graph** | 78 | **Does not exist** | The data is already in the table Flows queries. `int_execution_transfers_whitelisted_raw` has `transaction_hash`, `block_number`, `transaction_index`, `log_index` — the app aggregates all four away and lets 25 rows out through an evidence panel. |

The uncomfortable summary: **the mode that scored highest as an investigation (Timeline, 87) is the one I would most urgently pull from the product**, because it is the only mode that returns a confidently wrong answer rather than an incomplete one.

---

## 2. What each mode is actually FOR

Your users cannot tell them apart because three of the four answer the *same* question shape — "which addresses did this address touch" — and differ only in which axis they decorate it with.

| Mode | The one-line distinction |
|---|---|
| **Atlas** | "What relationship types does this platform model at all?" — no address in mind, browse the 12 profiles and see each in bulk. |
| **Investigate** | "Who does this one address deal with?" — counterparty structure, no clock, no money. |
| **Timeline** | "Did this relationship run all year or just one stretch?" — same structure as Investigate, plus a clock, no money. |
| **Flows** | "Where did the money come from and go next, and how much?" — the only mode where edges carry settled USD. |

**Does any mode fail to justify its existence?**

Yes — two, for different reasons.

**Atlas does not justify a top-level slot beside three address-centric modes.** Its value is entirely in `search_graph_catalog`, which is a *picker*, not a mode. The canvas actively misleads: 7 of 12 profiles have a constant weight (6 because their source is one-row-per-pair, plus `bridge_user_flows` whose `volume_usd` column is NULL upstream, rendering every edge at weight 0.0). A force-directed graph of uniformly-weighted edges is decoration. **Demote Atlas to a profile picker that seeds Investigate.** You lose nothing and you free a slot.

**Timeline does not justify its existence in its current form.** The concept is sound and it is the mode closest to what your owner actually asked for. But it animates a node set someone else chose, on a window it does not plot, and it does not say so. It should be merged into the bucketed-Flows build in §4 rather than patched.

That leaves **Investigate** (structure) and **Flows** (money) as the two modes that earn their keep — which is the right number for a tool of this size.

---

## 3. Ranked defect list

Ranked by forensic impact: *confidently wrong* > *silently incomplete* > *visibly limited*.

### BLOCKERS — the app returns a wrong answer with no signal

**D1. Timeline inverts the sign of the trend at default settings.**
`load_graph_timeline` plots a 365-day axis, but `open_graph_explorer` picks the node set on `window_days=90`. All five runs returned `warnings=[]`. Default config: seed-incident first/last-k ratios 1.154 / 1.205 / 1.174 / 1.187 / 0.960 across k∈{8,10,13,17,26}, OLS **+8.2/wk**. Ground truth over the identical measure: 0.892 / 0.933 / 0.924 / 0.913 / 0.953, OLS **-10.4/wk**. Sign inverted at four of five splits. Forcing the node set to 365d gets the direction right at every split but overstates the decline ~2.4x.
*Repro:* `open_graph_explorer(seed)` → `load_graph_timeline(view_id, seed)`; compare the bucket series against `SELECT toStartOfWeek(date,1), sum(transfer_count) FROM dbt.int_execution_transfers_whitelisted_daily WHERE from=seed OR to=seed`.

**D2. Flows renders a stale pipeline as a confident empty graph.**
`FLOWS_RELATION = int_execution_transfers_whitelisted_raw` (`flow_queries.py:24`) is stale. I re-verified this while writing: `max(block_timestamp) = 2026-07-07T23:59:55`, while the sibling `int_execution_lending_aave_user_balances_daily` is current to `2026-07-17`. A 10-day gap. `range_days=7` therefore lies entirely past the data horizon and returns `node_count=1, edge_count=0, truncated=false, warnings=[]`. "Empty because the pipeline is 10 days behind" is indistinguishable from "empty because nothing happened."
*Repro:* `load_graph_flows(view_id, seed_node_ids=[seed], range_days=7)`.

**D3. `edge_evidence` has no time predicate — the drill-down is from a different era than the edge.**
`build_evidence_sql` (`graph_profiles.py:429-434`) is `SELECT * FROM {model} WHERE {src}={src} AND {tgt}={tgt} LIMIT {lim}`. No `ORDER BY`, no window. Confirmed in source. An edge whose weight was computed over `2026-04-20..2026-07-18` drills into `2025-08-01..2025-08-25` — eleven months outside the window, zero overlap. Rows from 2021 surface on 90-day edges.
*Consequence beyond the panel:* because each edge independently returns its own unwindowed slice, **no cross-edge temporal comparison is possible in Investigate mode, for any pair of edges, ever.**

**D4. The flow evidence panel asserts exactness while returning a biased sample.**
Every drill-down reports `stats {mode:"exact_bounded", warnings:[]}` and `next_page_token: None`. The cap is exactly 25 (`fetch.py:83` and `:128` default `limit: int = 25`; `ui_tools.py:725` passes no override; SQL `ORDER BY amount_usd DESC, block_timestamp DESC LIMIT {lim}` at `flow_queries.py:171-181`). Reconciliation closes at `transfer_count ≤ 25` and fails from 26. Blast radius across the default trace: 278 of 2,039 transfer edges (13.6%) exceed the cap, and those 278 carry **$176,424,798.34 of $247,114,934.24 — 71.4% of traced value**. The seed→CowSwap edge (tc=21,232) shows 4.36% of its value and 0.12% of its transactions, labelled exact.
*Repro:* select any flow edge with `transfer_count ≥ 26`; sum `amount_usd` in `edge_evidence` against the edge's own `amount_usd`.

### MAJORS — silently incomplete

**D5. Unordered `LIMIT` makes results nondeterministic.**
`build_neighborhood_sql` (`graph_profiles.py:220`) and `build_sample_sql` (`:260`) both end `ORDER BY weight DESC` with **no tiebreaker**, while every timeline builder (`:360, :377, :396, :411`) correctly ends `ORDER BY weight DESC, source_id, target_id`. You already know the fix; it is applied in one file and not the other. Measured: Atlas returns 3 distinct edge sets over 6 identical calls for `address_labeled_as`, with run0 ∩ run1 = **0 of 50** — completely disjoint. `circles_trust` likewise 0 of 50. Four of twelve profiles affected.
*Note on a conflict between reports:* Investigate measured `edge_evidence` at 17 distinct sets over 20 calls; TX-graph measured 12/12 identical on a different edge. Both are honest. The SQL is unordered *by contract*, so stability is an artifact of how many MergeTree parts the predicate touches — large edges destabilize, small ones do not. Treat it as latent everywhere; a merge or mutation flips it silently.

**D6. The flows node budget deletes 83.4% of counterparties with no marker.**
`FLOWS_PER_HOP_NODE_BUDGET = 400` (`constants.py:70`), admission loop at `flows.py:185` (`# USD-desc — biggest money first degradation`). True hop-1 set: 2,404 counterparties / $17,834,740.32. App shows: 400 / $16,272,654.84. Hides 2,004 counterparties (83.4%) but only $1,562,085.48 (8.76%). **This bias is correct for value-tracing and is the worst possible bias for structuring/smurfing/fan-out**, because it deletes exactly the signal being hunted — and never names the 2,004 addresses it dropped.

**D7. `edge_evidence` is stale-carried across node selections.** Confirmed in source: `ui_tools.py:699-727` refreshes `node_evidence` under `if selected_node_id:` but never clears `edge_evidence`, then the patch loop at `:742` unconditionally re-emits *both* keys from `updated.datasets`. Result: 30/30 node-selection calls returned 175 rows belonging to the previously selected *edge*, with `selection.edge_id=''`. Three-line fix.

**D8. Timeline scope is silently bound by invisible prior view state.** Identical `load_graph_timeline(grain="day", range_days=30)` returns 2,045 edges on a fresh view and 746 on a view where `load_graph_explorer_seed(max_neighbors=25)` ran first — 2.74x, deterministic within each view, nothing in the payload discloses why.

**D9. `direction="both"` is not the union of the legs.** The budget is spent only on *new* nodes, so the out-leg pre-populates the node set and inbound edges ride in free. "How much came into this address?" returns **$20,425,065.65** (`in`) or **$20,722,822.27** (`both`) — a $297,756.62 delta driven by an unrelated knob. Neither is the true total.

**D10. Widening the window narrows the picture.** Same 400 slots refill with bigger fish: smallest admitted edge rises $10.29 (14d) → $4,338.71 (90d) → $20,735.56 (365d). A counterparty visible at 90d silently vanishes at 365d, with no signal that widening the range *removed* it.

**D11. `min_usd` is inert below the budget boundary.** Identical results at 10 / 1000 / 4000 (402 nodes, $16,272,654.84). The truncation warning says "raise min USD," which works — but *lowering* it to hunt small payments changes nothing and reports nothing.

**D12. No coverage disclosure anywhere.** Timeline plots 94 of 6,260 counterparties — **1.50% of counterparties, 62.46% of activity**. That sentence should precede every trend claim the mode makes; it appears nowhere.

**D13. `bridge_user_flows` renders every edge at weight 0.0.** The profile *has* `weight_column = volume_usd`, but the column is entirely NULL upstream. Constant-zero weights, silently.

**D14. Dangling endpoints ship without validation.** `bridge_user_flows`: 145 of 150 edges have an empty target. `pool_contains_token`: 22 empty sources. `validator_controlled_by`: 8 empty sources. `warnings=[]` on all.

**D15. `window_days` is a true no-op on 3 of 12 profiles** (`circles_avatar_balances`, `lending_user_to_reserve`, `validator_controlled_by` — `time_column` is NULL). Edge sets byte-identical at window 1 vs 3650. The knob renders as active.

### MINORS

**D16.** `lp_in_pool` is registered but unreachable — all four backing models have `enabled=false`. The sampler errors `Unknown profile(s)`; the catalog silently substitutes `pool_contains_token` rather than saying "no such profile."
**D17.** Catalog default `limit=20` hides 9 node types with `hidden_by_tier_count=0` and no truncation signal.
**D18.** `graph_metrics` is not metrics — it restates four request/response numbers and reports *post-truncation* counts (95/100) as though they were the graph. True 90d degree is 3,565; the panel understates the subject ~37x while presenting itself as a metrics surface. Delete it or make it real.
**D19.** Toolbar claims "Edge types 2/12"; legend lists 1.
**D20.** `columns` shape-shifts between endpoints — `[{name,type},…]` from `load_graph_atlas_sample`, plain strings from `get_mini_app_rows`. Guaranteed client TypeError.
**D21.** `load_graph_timeline` does not switch the UI into timeline mode; an agent calling only that tool shows the user nothing.
**D22.** Default node count is 94 or 95 for the identical `open_graph_explorer` call.
**D23.** `in_usd` appears on an out-direction trace and is undocumented hop-1 backflow (162 edges, $11,323,656.62). Not a bug; reads as one.
**D24.** `node_evidence` is sparse — 4 of 7 sampled nodes return nothing — and never emits a token symbol even for token nodes. It is nonetheless the app's *best* identity surface: it correctly typed the seed as an ERC20 contract, named CowSwap, Balancer V2 and Null/Burn.
**D25.** Atlas rank is only citable as a range; spread grows with depth (0 near rank 108, up to 74 near rank 9,300) as ties multiply.

---

## 4. Time-as-exploration: "at t1 money moved to X, then t2…"

Neither mode delivers this, and neither can be patched into it:

- **Timeline** has the time axis (`bucket_start`/`bucket_end` on every edge row, 53 weekly buckets) but **no USD** and a node set capped at 100 chosen on the wrong window.
- **Flows** has USD, direction and hop structure but **a single flat `(t0, t1)`** — the trace is one static snapshot. `first_seen`/`last_seen` on edges are not a time axis.

The answer is **bucketed Flows**, and the narrative your owner wants is a *table*, not a picture. Build in this order:

**1. Bucket the flows trace.** Add `grain` to `load_graph_flows`. `build_flow_edges_sql` (`flow_queries.py:87`) already groups by `(source, target, token)` — add `toStartOfWeek(block_timestamp) AS bucket` to the `GROUP BY` and to the `ORDER BY` tiebreaker chain. This is a small change to one query.

**2. Make the node budget global, not per-bucket.** Admit the union of the top-N nodes by *total* USD over the whole range, then compute per-bucket edges only among admitted nodes. If you budget per-bucket, the node set churns and you reproduce D1 exactly — this is the same fix Timeline needs.

**3. Emit a `narrative` dataset — this is the actual deliverable.** Per bucket, a diff against the previous bucket: top-k newly-appearing counterparties, top-k by USD delta, top-k that disappeared. That is literally "at t1 money moved to X, then at t2 to Y." It is one window function over the bucketed edges, not a rendering problem. **Ship this before any canvas work** — it answers the question on its own, in a text panel, with no graph at all.

**4. Then the canvas.** Scrubber over buckets, edges fade in and out, narrative rows in a rail beside the canvas. The rail is the product; the canvas is decoration.

**5. Disclose per bucket:** nodes admitted / nodes existing, USD shown / USD total. The 62.46%-of-activity figure belongs on screen, per bucket.

The core insight from three rounds: **your owner keeps asking for a narrative and the app keeps handing them a picture.** The narrative is a SQL query you can write this week.

---

## 5. Transaction graph visualizer — build spec

**The finding that should change your plan:** you do not need new data. I checked the schema directly:

```
dbt.int_execution_transfers_whitelisted_raw
  block_number       Nullable(UInt32)
  transaction_index  Nullable(UInt32)
  log_index          Nullable(UInt32)
  transaction_hash   Nullable(String)
  from, to, token_address, symbol, amount, amount_usd, block_timestamp
```

Block ordering, intra-block ordering and intra-transaction ordering are **all already present in the relation Flows queries today**. The app aggregates them away in `build_flow_edges_sql` and lets 25 rows escape through the evidence panel. This is a day of SQL, not a data engineering project.

**The gap it closes.** One edge currently reads "seed → CowSwap, EURe, 5,333 transfers." Those 5,333 transactions actually contain **33,260 transfer legs across 14 tokens, 382 senders and 693 receivers**. The app renders **16.0% of the value movement inside transactions it is already pointing at.**

### Spec

**Unit of analysis:** `(transaction_hash, log_index)` — a transfer *leg*, not an address pair. This is the one design decision that matters; everything else follows.

**Entry points.** Three, all from surfaces you already have:
1. Any flow edge → "show the transactions behind this edge" (replaces the 25-row panel).
2. A pasted transaction hash.
3. Any address + block range → "show every transaction it appears in."

**Query.** No new model:
```sql
SELECT transaction_hash, log_index, block_number, transaction_index,
       block_timestamp, `from`, `to`, token_address, symbol, amount, amount_usd
FROM int_execution_transfers_whitelisted_raw
WHERE transaction_hash IN {hashes:Array(String)}
ORDER BY block_number, transaction_index, log_index
```
`ORDER BY` is not optional — see D5.

**Layout.** Legs on a vertical `log_index` axis, addresses as columns. A multi-token atomic swap then reads as what it is: a set of legs at adjacent log indices moving different tokens between the same parties. This is the shape none of the four existing modes can draw, and it is the shape that makes a swap, a batch settlement or an exploit legible at a glance.

**Node typing.** Reuse `resolve_address_roles` — it already returns `dune_project`, `is_lp_provider`, `pool_protocol`, `flags:['token_contract']`. It named CowSwap, Balancer V2 and Null/Burn correctly across every report.

**Scope contract (mandatory, see §6).** Every payload carries `{ hashes_requested, hashes_returned, legs_returned, legs_total, truncated }`. No `mode:"exact_bounded"` unless `legs_returned == legs_total`.

**Explicitly out of scope for v1:** internal calls and native-value traces. Those need `rpc_trace_transaction` and a different pipeline. Ship ERC-20 legs first; they are already in the warehouse.

**What this unlocks that nothing else can:** the identity proof that took Investigate 40 evidence draws at an 85% hit rate — burn `aGnoEURe` to `0x0`, pay out `EURe` from the contract, same amount to the wei — is *two adjacent log indices in one transaction*. The TX graph shows it in a single view, deterministically, with no repetition.

---

## 6. The single highest-value change

**Make scope and truncation machine-readable on every dataset, and always rendered.**

One struct on every dataset descriptor:

```
scope: {
  t0, t1,                    // the window ACTUALLY applied — null if none was
  window_source,             // which knob resolved it
  rows_returned, rows_total, // pre-truncation count
  truncated, truncation_rule,
  ordered                    // false when the SQL has no total order
}
```
Rendered as one line under every panel. Refuse to emit `mode:"exact_bounded"` when `rows_returned < rows_total`.

**Why this and not a specific bug fix.** Every report converged independently on one root cause: **the app computes over one scope and displays evidence from another, and never says so.** Every mode has an instance — Investigate (edge over 90d, evidence unwindowed, D3), Timeline (365d axis, 90d node set, D1), Flows (traced range correct, evidence capped at 25 and labelled exact, D4), Atlas (a window that is a no-op on 3 of 12 profiles and silently total on the tied-weight ones, D15). The scope contract closes or discloses **D1, D2, D3, D4, D6, D10, D11, D12, D15 and D18** — nine of the fifteen blockers and majors — in one architectural change.

**Why disclosure before correction.** D1 and D2 are the only two defects where the app returns a *confidently wrong answer* rather than an incomplete one, and both are scope-mismatch instances. Fixing the underlying sampling bias is weeks of design work. Making the tool stop lying about its own scope is days. **An incomplete tool with honest bounds is usable; a complete-looking tool with hidden bounds is not.** That is the entire difference between where this app is now and where it needs to be.

**Do these three alongside it — they are hours, not days:**
- Add `, source_id, target_id` to `graph_profiles.py:220` and `:260`. You already wrote the correct version at `:360-411`. This kills D5.
- Clear `edge_evidence` when `selected_node_id` is set (`ui_tools.py:699`). Three lines. Kills D7.
- Add a time predicate to `build_evidence_sql` (`graph_profiles.py:429-434`) bound to the edge's own window, and an `ORDER BY`. Kills D3.

**One last thing, said plainly.** Three rounds of review found no defect in your data model, your profile abstraction or your query layer — those are sound, and the Aave aToken identity was established *from app payloads*, with SQL only confirming it. The app's problem is uniformly one of **honesty about its own limits**. That is a much better problem to have than the alternative, and it is fixable without rearchitecting anything.