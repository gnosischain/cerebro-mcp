# RPC Scan Toolkit — bulk on-chain forensics

The `rpc_scan_*` MCP tool family does what the single-call contract tools
cannot: sweep `eth_getLogs` over arbitrary block windows, batch view-function
reads across tens of thousands of addresses via Multicall3, read storage
slots and bytecode at a pinned block, and collect native-value traces.
Results stream into **ClickHouse scratch tables** (`scratch.rpc_*`) so the
analysis continues in SQL — joins against dbt models included — instead of
through the LLM context window.

It was designed from the workflows of the Gnosis Pay incident forensics
suite (`gp_rpc_forensics`), generalized into chain-agnostic primitives.

## Enablement

```bash
# .env
RPC_SCAN_ENABLED=true
GNOSIS_ARCHIVE_RPC_URL=https://...   # pinned-block scans + traces
```

ClickHouse grant for the deployment user (everything else stays readonly):

```sql
GRANT CREATE DATABASE, CREATE TABLE, INSERT, DROP TABLE, SELECT
ON scratch.* TO <CLICKHOUSE_USER>
```

The `scratch` database is auto-appended to `ALLOWED_DATABASES` so
`execute_query` / `start_query` can read it. Scratch tables are dropped
after `RPC_SCAN_SCRATCH_TTL_DAYS` (default 7).

## Execution model

Every scan tool starts an engine job, waits up to `sync_wait_seconds`
(default 10, cap 25), and returns either the completed counts-first summary
or a running snapshot — same shape either way. While running:

- partial rows are **already queryable** in the scratch table;
- `rpc_scan_status(job_id)` shows progress (blocks/addresses done, rows, ETA);
- `rpc_scan_cancel(job_id)` stops it, keeping rows + cursor;
- `rpc_scan_resume(job_id)` continues a partial/cancelled/restart-orphaned
  job from its persisted cursor into the SAME table;
- `rpc_list_scans()` lists in-memory jobs plus the persisted registry
  (`scratch.rpc_scan_jobs`), which survives server restarts.

Durability is unit-based: a completed block range (logs/traces) or address
batch (calls/storage/code) is flushed to ClickHouse before the cursor
advances — zero-row units checkpoint too. Tables are
`ReplacingMergeTree(_scanned_at)` keyed on the natural dedup key, so resume
overlap dedups on merge: **always count with `uniqExact(<dedup key>)` or
`FINAL`**, never bare `count()`.

## Address sets

Every sweep accepts `addresses` (inline, ≤500) **or** `address_sql` — a
read-only SELECT returning one address column, any size:

```text
address_sql="SELECT safe_address FROM dbt.<model>"
address_sql="SELECT DISTINCT arrayJoin(modules_out_0) FROM scratch.rpc_calls_<id>"
```

The second form chains scans: a previous scan's output is the next scan's
population.

## Tools and scratch schemas

| Tool | Table | Dedup key | Notes |
|---|---|---|---|
| `rpc_scan_logs` | `rpc_logs_<id>` | `(block_number, log_index)` | topics0-3 + data always kept raw; decoded `event_name`/`args_json`; single-`event` mode promotes typed `arg_*` columns |
| `rpc_batch_call` | `rpc_calls_<id>` | `(address)` | WIDE: one row per address; per alias `<alias>_success`, `<alias>_out_N` (typed), `<alias>_error` |
| `rpc_read_storage` | `rpc_storage_<id>` | `(address, slot)` | value stored 3 ways: raw hex, `value_uint`, `value_address` (last 20 bytes when top 12 are zero) |
| `rpc_get_code` | `rpc_code_<id>` | `(address)` | `code_hash` (keccak) clusters identical deployments; `is_eip1167`+`eip1167_impl`; EIP-1967 impl/admin/beacon slots |
| `rpc_scan_traces` | `rpc_traces_<id>` | `(block_number, tx_hash, trace_address)` | native xDAI flows — the Transfer-log blind spot; ~100-block node cap auto-chunked |
| `rpc_find_block` (bulk) | `rpc_blocks_<id>` | `(address, kind)` | deployment / storage-change blocks per address |
| `rpc_trace_transaction` | optional `rpc_traces_<id>` | — | sync call-tree markdown + net native flows |

Big integers use `UInt256` when the server supports it, else
`Decimal(76, 0)` (values that don't fit clamp to 0; the raw hex column stays
authoritative).

## Log decoding rules

Pass at most ONE of:

- **`event`** — an event signature.
  - Full form with `indexed` markers and names decodes into typed columns:
    `"Transfer(address indexed from, address indexed to, uint256 value)"`
    → `arg_from`, `arg_to`, `arg_value`.
  - Short form (types only) is accepted only for well-known events
    (Transfer, Approval, ApprovalForAll, TransferSingle, Deposit,
    Withdrawal). These carry every known indexed layout; the decoder picks
    per log line by topic count — a 3-topic `Transfer` decodes as ERC-20
    (`arg_value` from data), a 4-topic one as ERC-721 (`arg_tokenId` from
    topics). Promoted columns are the union of layouts.
  - Dynamic indexed args (string/bytes/arrays) are keccak hashes on chain:
    stored raw with a `_hash` column suffix, never abi-decoded.
- **`decode_abi_address`** — resolve that contract's ABI (ClickHouse seed →
  Blockscout) and decode every event it defines into `event_name` +
  `args_json`. No typed columns.
- **`topics`** — raw JSON-RPC override.
- none — raw scan.

**Address filters**: `filter_arg` names a decoded argument; the set comes
from `filter_addresses`/`filter_address_sql`. Indexed args filter
**server-side at any set size** (the set is chunked into ~600-entry topic
groups, one window pass per group). Non-indexed args filter engine-side
after decoding and require a window ≤ `RPC_SCAN_UNINDEXED_FILTER_MAX_BLOCKS`.

## Node limits (handled automatically)

- `eth_getLogs`: provider-dependent range/result caps → the engine halves
  the window on errors and grows back after success. Never pre-chunk.
- `trace_filter`: ~100 blocks/call → chunked + worker pool; requires a
  trace-capable archive node (Erigon, or Nethermind with the Trace module);
  capability is probed and a teaching error returned if missing.
- Multicall3 `aggregate3`: ~600 calls per `eth_call`, `allowFailure` per call.
- Native xDAI transfers emit no log — only traces see them.

## Worked flows (from the GP incident forensics)

### a. Slot-0 takeover sweep (was exp01)

```text
rpc_find_block(kind="timestamp", timestamp="2026-06-03T10:00:00Z")  -> anchor block
rpc_read_storage(slots=[0],
                 address_sql="SELECT safe_address FROM dbt.<safes model>",
                 block=<anchor>, label="slot0 takeover sweep")
-- the summary's top-values table on value_address IS the classification;
-- then:
execute_query("""
  SELECT multiIf(value_address IN ('<attacker impls>'), 'taken_over',
                 value_address IN ('<canonical singletons>'), 'genuine',
                 'other') AS klass,
         uniqExact((address, slot)) AS safes
  FROM scratch.rpc_storage_<id> GROUP BY klass""")
```

### b. Token drain attribution (was exp02)

```text
rpc_scan_logs(from_block=<start>, to_block=<end>,
              contracts=[<token addresses>],
              event="Transfer(address indexed from, address indexed to, uint256 value)",
              filter_arg="to", filter_addresses=[<attackers + whitehat>],
              label="drain attribution")
execute_query("""  -- split exploited vs rescued, join decimals from dbt
  SELECT l.address AS token,
         if(l.arg_to = lower('<whitehat>'), 'rescued', 'exploited') AS leg,
         uniqExact(l.arg_from) AS safes, sum(l.arg_value) AS raw
  FROM scratch.rpc_logs_<id> l FINAL
  GROUP BY token, leg""")
rpc_scan_traces(from_block=<start>, to_block=<end>,
                from_address_sql="SELECT safe_address FROM dbt.<safes model>",
                to_addresses=[<attackers + whitehat>])
-- closes the native-xDAI gap Transfer logs cannot see
```

### c. Safe setup sweep (was exp03)

```text
rpc_batch_call(
  calls=[{"function": "getOwners()(address[])",  "alias": "owners"},
         {"function": "getThreshold()(uint256)", "alias": "threshold"},
         {"function": "getModulesPaginated(address,uint256)(address[],address)",
          "args": ["0x0000000000000000000000000000000000000001", 100],
          "alias": "modules"}],
  address_sql="SELECT safe_address FROM dbt.<safes model>",
  block=<pre-incident anchor>)
rpc_get_code(
  address_sql="SELECT DISTINCT arrayJoin(modules_out_0) FROM scratch.rpc_calls_<id>",
  block=<anchor>)
-- join: modules whose eip1167_impl is a known master => at-risk safes
```

## Routing rubric

| Question shape | Plane |
|---|---|
| Historical aggregate / trend / USD | dbt via `execute_query` |
| Current/pinned state, ONE address | `contract_call_function` |
| Many-address pinned state, storage, code, traces, un-indexed events | `rpc_scan_*` → scratch → `execute_query` |
| "What did tx X do" | `contract_decode_*` / `rpc_trace_transaction` |

For incident investigations, adopt the `chain_forensics` persona
(`get_agent_persona("chain_forensics")`): pin anchors first, sweep, classify
in SQL, reconcile two independent ways, disclose residuals.
