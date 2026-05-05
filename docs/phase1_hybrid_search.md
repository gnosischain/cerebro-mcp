# Phase 1 — Hybrid Search + Deterministic dbt Lineage

**Status:** shipped (Sprint 1 of the next-gen architecture plan).
**Goal:** stop the LLM from hallucinating table and column names, give analyst
agents a deterministic lineage walker, and keep wide-table schema injection
under control without losing join-critical columns.

This document describes everything that landed in Phase 1, why each piece
exists, the verification scripts, and the measured outcomes on the live
cerebro-mcp dbt manifest (862 models / 75 sources).

---

## What was implemented

### 1. BM25 keyword search (`src/cerebro_mcp/semantic_bm25.py`)

A new module exposing two pure-Python, picklable indices:

- **`BM25Index`** — ranks dbt **models** against a free-text query.
  Built once per manifest reload from the existing `search_index` blob.
  Empty corpora are tolerated (returns `[]` instead of raising).
- **`ColumnBM25Index`** — ranks **columns within a single model**, used by
  the SQL compiler to keep prompt context small on wide tables (100+ cols)
  without losing the join keys / date columns the model actually needs.
  One physical BM25 holds every column across every model; per-model
  filtering happens at query time.

Both are deterministic (regex tokenizer, no embeddings, no model loading)
and rebuild in well under a second on a 5,000-column corpus.

### 2. Reciprocal Rank Fusion (`src/cerebro_mcp/semantic_index.py`)

`rrf_fuse(rankings, k=60, top_k=None)` combines two or more ranked lists
into a single fused ranking. Standard formula from Cormack/Clarke/Buettcher
(2009): `score(d) = Σ 1 / (k + rank_i(d))`. Items missing from a list
contribute 0 (no penalty for absence). Used to fuse the legacy token-overlap
ranker with the new BM25 ranker — items present in both rise to the top.

### 3. Enriched search blob (`manifest_loader.py:_build_indexes_internal`)

The blob BM25 sees per model now carries:

| Field | Before Phase 1 | After Phase 1 |
|---|---|---|
| Model name | ✅ | ✅ |
| Description | ✅ | ✅ |
| Tags | ✅ | ✅ |
| Owner | ✅ | ✅ |
| **Column names + descriptions** | ❌ | ✅ |
| **Path tokens** (`execution dex marts`) | ❌ | ✅ |
| **`meta.inference_notes`** | ❌ | ✅ |

Columns are the most direct query→data signal — a model with a column named
`effective_balance` should match `"validator balance"` queries even if the
description is generic. Path tokens add the category context that's missing
from one-line descriptions. `inference_notes` is meta authored specifically
for retrieval, when present.

The trade-off is blob length: a 100-column model has a much longer blob.
BM25's length-normalization (`b=0.75` default) compensates well; the legacy
substring scorer does not — this is one of the reasons Phase 1 widens the
NEW–OLD gap rather than narrowing it.

### 4. Hybrid `search_models` (`manifest_loader.py:search_models`)

The public method now runs **both** rankers and fuses with RRF. Tag/module
filters still apply *before* ranking (they restrict the candidate set). The
legacy token-overlap scorer is preserved because it carries hand-tuned
behavior (substring match, short-token fallback, alphabetical tie-break)
that BM25 doesn't replicate. Items present in both rankings dominate the
fused output.

API is unchanged. Every caller (`tools/dbt.py`, `discover_models`,
preflight) gets the new ranking transparently.

### 5. networkx-backed lineage (`manifest_loader.py`)

A `networkx.DiGraph` is hydrated from the dbt `parent_map`/`child_map` once
per manifest reload. Nodes are full unique-ids, edges go parent→child. Both
models and sources are added so ancestor walks don't drop edges.

New methods on `ManifestLoader`:

- `upstream(model_name)` / `downstream(model_name)` — full transitive sets
  (unique-ids), via `nx.ancestors` / `nx.descendants`.
- `upstream_named(model_name)` / `downstream_named(model_name)` — short
  model names only (sources skipped).
- `top_columns_for_model(model, query, top_k)` — wraps `ColumnBM25Index`.

The pre-existing `get_lineage(direction, depth)` is kept untouched for API
compatibility — it does a bounded BFS and is what the older tools use. The
new `nx`-based path is for transitive closure ("everything that depends on
X, regardless of depth").

### 6. New MCP tools (`src/cerebro_mcp/tools/dbt.py`)

Three additions, all read-only:

- **`get_upstream_lineage(model_name, max_results=100)`** — returns every
  model/source the target depends on, with kind (model/source) and
  unique-id. Used by analysts before writing SQL to confirm where a column
  originates.
- **`get_downstream_impact(model_name, max_results=100)`** — returns every
  model that depends on this one. Used by reviewers before approving
  schema changes; the description explicitly frames it as
  "schema-change blast-radius".
- **`get_relevant_columns(model_name, query, top_k=20)`** — returns a
  column-scoped schema block for a wide model, ranked by BM25 against
  `query`. Always includes join keys (`address`, `tx_hash`, ...) and time
  columns (`date`, `day`, `month`, ...). Solves the "100+ column model
  blows up the prompt" problem.

All three include fuzzy-match fallback: if `model_name` is unknown, the
response suggests close matches via `search_models`.

### 7. Column-scoped schema injection (`src/cerebro_mcp/schema_context.py`)

A new helper module producing a markdown schema block per model:

1. Tables narrower than `SQL_COMPILER_FULL_SCHEMA_THRESHOLD` (default 30):
   inject every column verbatim.
2. Wider tables: BM25-rank columns against the query, keep top-K + a fixed
   allowlist of join keys / partition columns / time grains
   (`_ALWAYS_KEEP_NAMES`).
3. Anaemic-result floor: if BM25 + always-keep total fewer than `top_k`
   columns (e.g. an off-topic query against a wide staging table), pad with
   the first K columns so the LLM still gets a usable schema. Without this
   floor, a query like `"balance over time"` against
   `stg_consensus__blocks` returned 4 of 85 columns — too narrow to be
   useful.
4. The block ends with a comment telling the LLM which columns were
   omitted and how to request them by name via `get_relevant_columns`.

Pure-function, side-effect-free, deterministic — safe to call from a worker
process if Phase 4 later offloads prompt assembly.

### 8. Configuration knobs (`config.py`)

```python
SQL_COMPILER_FULL_SCHEMA_THRESHOLD: int = 30  # narrower = full schema
SQL_COMPILER_TOP_COLUMNS: int = 20            # cap for wide-table scoping
```

Both env-overridable.

### 9. Dependencies (`pyproject.toml`)

```toml
"rank-bm25>=0.2.2",
"networkx>=3.2",
```

Both are pure-Python or near-pure-Python; no native build, no model
download.

### 10. Tests (`tests/test_phase1_hybrid_search.py`)

19 new tests covering:

- BM25 indices (empty corpus, ranking, top-K, empty query).
- Column-level BM25 (per-model filtering, unknown model).
- RRF fusion (basic ranking, missing items, top-K cap).
- Hybrid `search_models` (name match wins, filter precedence).
- networkx lineage (transitive upstream/downstream, unknown model, leaf).
- Column-scoped schema (narrow full-injection, wide scoping with
  always-keep, BM25-empty fallback, anaemic-keep floor).

Existing 584 tests continue to pass — Phase 1 is a strictly additive
change to the public API.

---

## Verification scripts

Two scripts in `scripts/` produce machine-readable reports:

### `scripts/bench_phase1.py`

Performance benchmark with a built-in A/B against the legacy token-overlap
ranker (inlined so no `git stash` is needed). Sections:

1. Manifest cold load + index build time.
2. `search_models` latency: NEW (hybrid) vs OLD (token-overlap), per-query
   medians + aggregate.
3. Lineage query latency.
4. Column-scoping latency on the widest model in the manifest.
5. Memory footprint (pickle-size proxy).

```bash
python scripts/bench_phase1.py
python scripts/bench_phase1.py --queries 100      # more samples
python scripts/bench_phase1.py --json out.json    # archival
```

### `scripts/eval_phase1_quality.py`

Ranking-quality eval. For each `(query, expected_model)` pair, runs both
rankers and computes `hit@1 / hit@3 / hit@5 NEW vs OLD`. The default eval
set (12 queries against the live manifest) is hand-curated; override with
`--eval my_set.json` for a custom set.

```bash
python scripts/eval_phase1_quality.py
python scripts/eval_phase1_quality.py --verbose   # show top-5 for every query
```

---

## Measured outcomes

All numbers below are from the live cerebro-mcp manifest:
**862 models / 75 sources / 5,136 column docs / 2,273 lineage nodes / 2,648
edges**.

### Performance

| Metric | Value | Notes |
|---|---|---|
| Cold manifest load | ~150 ms | Includes JSON I/O. |
| Index build only | 83 ms median | Includes BM25 corpus build + networkx hydrate. |
| `search_models` NEW | 2.3 ms median | Hybrid: token-overlap + BM25 + RRF. |
| `search_models` OLD | 1.4 ms median | Legacy token-overlap, for A/B reference only. |
| Search overhead | +0.9 ms / +67% | Acceptable for a tool call. |
| Lineage query | 14 µs median, 0.5 ms p99 | networkx ancestors+descendants. |
| Column scoping | 2.1 ms on 85-col model | Includes BM25 column ranking. |
| Memory total | ~7 MB | BM25 (1.8 + 1.1 MB) + DAG (0.5 MB) + manifest (3.5 MB). |

The +0.9 ms search overhead is the only metric worth flagging. It's well
below typical MCP tool-call serialization overhead (~5–10 ms for a JSON
response) and irrelevant for human-facing analyst calls. It would matter
only if `search_models` were called in a tight loop, which it isn't.

### Ranking quality (12-query eval, hit@k)

| Metric | NEW (Phase 1) | OLD (legacy) | Δ |
|---|---|---|---|
| **hit@1** | **4/12 (33%)** | 1/12 (8%) | **+3 (+25 pp)** |
| **hit@3** | **9/12 (75%)** | 5/12 (42%) | **+4 (+33 pp)** |
| **hit@5** | **11/12 (92%)** | 10/12 (83%) | **+1 (+9 pp)** |

The headline number is `hit@1` going from 1/12 to 4/12 — **4× absolute
improvement**. `hit@3` improvement is also large: a question with this
manifest now reaches the right model in the first three results 75% of the
time, up from 42%.

Note that **OLD also benefits from the enriched blob** — but it benefits
*less*. Legacy substring counting gets noisier as blobs grow (more random
hits = more ties = more arbitrary ranking). BM25's IDF weighting and
length normalization handle blob enrichment gracefully. This is part of
why the gap widens after the Phase 1.5 blob enrichment rather than
narrowing.

### Why ranking improved (mechanism)

BM25 helps in two distinct ways:

1. **Distinctive name tokens dominate.** The model whose **name** contains
   the query terms gets the strongest signal because those tokens have
   high IDF (rare across the corpus). Pure substring counting treats every
   matching token equally.
2. **Column descriptions add direct query→data signal.** A model with a
   column `effective_balance` matches `"validator balance"` queries even
   if its description is generic. This is what made `validator
   withdrawals` jump from rank 5 → 2, `consensus validator balance` from
   rank 3 → 1, and `block production daily` from rank 2 → 1.

### Known residual failure mode

One query regressed on hit@5: `dex pool fees` (rank 4 → 10). Lots of
unrelated models contain column descriptions mentioning "fees" or "pool"
and the enriched blob promoted them. This is the classic length-norm vs
recall trade-off. A targeted fix is multi-field BM25 (separate indices for
name / description / columns / path with per-field weighting) but that's
deferred — current numbers don't justify the complexity yet.

A second class of failures has nothing to do with BM25: queries where
`*_latest` and `*_dist_*` snapshot models compete with `_daily` time-series
models. The fix for those is a small **name-pattern boost layer** (e.g.
boost `^api_.*_daily$`, downweight `_latest$`, query-aware) — not yet
shipped.

---

## How agents use this

The new tools are surfaced in the MCP tool registry under the existing
`register_dbt_tools` block. No persona prompt rewrites were required for
Phase 1; the tool descriptions are written so an analyst can discover the
right call from the tool list alone:

- Before SQL on a wide model → `get_relevant_columns(model, query)`
- Confirm a column's origin → `get_upstream_lineage(model)`
- Before a schema change → `get_downstream_impact(model)`
- Standard discovery → `search_models` (transparently uses hybrid ranking)
- Combined search + details → `discover_models` (transparently uses hybrid)

Phase 3 will add explicit dispatcher routing rules that steer agents toward
these tools, but the tools themselves are useful immediately.

---

## What was deliberately NOT done

- **No vector embeddings.** BM25 + RRF + dbt graph closes most of the
  SQL-correctness gap on this corpus; embeddings can be revisited if a
  later eval shows recall@5 below target.
- **No replacement of igraph.** The existing igraph-based join planner in
  `semantic_graph.py` works; networkx is added strictly for lineage walks.
- **No multi-field BM25.** Single enriched blob first; multi-field is the
  next escalation if numbers warrant.
- **No name-pattern / query-intent boosts.** Identified as the most
  promising next experiment; deferred to keep Phase 1 surgical.
- **No persona prompt rewrites.** Agents discover the new tools from
  registry descriptions; explicit routing arrives in Phase 3.

---

## Files touched

| File | Change |
|---|---|
| `pyproject.toml` | +`rank-bm25`, +`networkx` |
| `src/cerebro_mcp/semantic_bm25.py` | **new** — BM25Index, ColumnBM25Index |
| `src/cerebro_mcp/semantic_index.py` | +`rrf_fuse` |
| `src/cerebro_mcp/manifest_loader.py` | enriched search blob, BM25 + networkx wiring, lineage methods |
| `src/cerebro_mcp/schema_context.py` | **new** — column-scoped schema builder |
| `src/cerebro_mcp/config.py` | +`SQL_COMPILER_FULL_SCHEMA_THRESHOLD`, +`SQL_COMPILER_TOP_COLUMNS` |
| `src/cerebro_mcp/tools/dbt.py` | +`get_relevant_columns`, +`get_upstream_lineage`, +`get_downstream_impact` |
| `tests/test_phase1_hybrid_search.py` | **new** — 19 tests |
| `scripts/bench_phase1.py` | **new** — performance benchmark |
| `scripts/eval_phase1_quality.py` | **new** — ranking quality eval |
| `docs/phase1_hybrid_search.md` | **new** — this document |

---

## Reproducing the numbers

```bash
# unit + integration tests
python -m pytest tests/test_phase1_hybrid_search.py tests/test_manifest_loader.py -q

# performance bench (live manifest required)
python scripts/bench_phase1.py

# ranking-quality eval (live manifest required)
python scripts/eval_phase1_quality.py
```

The bench and eval both load the **global** `manifest` singleton, so they
exercise the same code path the running MCP server uses.
