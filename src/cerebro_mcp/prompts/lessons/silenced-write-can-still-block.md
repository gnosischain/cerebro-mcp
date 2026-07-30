---
id: silenced-write-can-still-block
title: Catching every exception does not make a write non-blocking
status: enforced
layer: runtime
scope: workflow/event_store_sync.py, tools/storyteller/, tools/research/
symptom: a tool call never returns while a read-only tool on the same server answers instantly; the client times out with no error and no log line
last_verified: 2026-07-30
evidence: |
  A storyteller pipeline that had passed every gate (12 charts, context brief,
  big idea, 8-scene storyboard, 8 visual specs, final story, clarity review
  8/8) was stranded because `storyteller_record_accessibility_pass` never
  returned. Three attempts, including a minimal payload, each ended in a ~4
  minute client-side timeout. `storyteller_status`, hitting the same process
  and the same lock, returned instantly throughout.
---

## Symptom

One tool hangs indefinitely. A read-only sibling on the same server responds
normally, which rules out the connection, the event loop and the process. There
is no exception, no traceback and no log line, because nothing failed — the call
is parked.

## Root cause

`event_store_sync`'s module contract is "failures NEVER raise: event-log writes
are observability and must not break a tool". Every writer is wrapped in
`try/except`. That handles the wrong half of the problem: **a write that blocks
is not an exception**, so no `except` clause is reached.

Nothing else in the path bounded it either:

- `sqlite3.connect(..., timeout=30.0)` governs only SQLite's BUSY handler, i.e.
  lock contention. It does not bound `mkdir`, `stat`, `open`, fsync, WAL/`-shm`
  setup, or the checkpoint SQLite performs when the last connection to a WAL
  database closes. On a wedged or full filesystem those block with no deadline.
- `runtime/offload.py` has no timeout, and anyio **shields** the worker thread,
  so a client disconnect does not abort the work.
- No request timeout exists at the transport layer.

The asymmetry that makes this confusing is diagnostic, not coincidental. The
mutating tool calls `_emit_phase_event_if_changed`, which runs **outside** the
state lock; the read-only tool takes the same lock and does zero filesystem
work. So the wedged thread holds nothing, and reads stay fast while writes hang.

## Forbidden action

Do not conclude "the tool is broken" from the fact that the tool hangs. This
repo's own `offload.py` docstring records the same trap twice: a bare `SELECT 1`
timed out because the *request* was never dispatched. The hanging tool is
frequently not the blocking one.

Do not treat a broad `except Exception` as evidence that a code path is safe.
It proves the path cannot raise, not that it can return.

## Detection

- A read-only tool on the same server answers while a mutating one does not:
  look for I/O that only the mutating path performs.
- `system_status` reports the event store's path, writability, last write
  latency, timeout count and last error.
- Grep for a deadline: if a blocking call has no timeout that covers
  *filesystem* operations, the documented timeout probably covers something
  narrower.

## Safe remediation

Give the write a hard deadline on a worker thread and let the caller walk away
(`EVENT_STORE_WRITE_TIMEOUT_SECONDS`, default 2s). On expiry, drop the event,
mark the store degraded for a cooldown so subsequent calls short-circuit rather
than each paying the deadline, and continue.

Two details that are easy to get wrong:

- **The worker must be a daemon thread, not a `ThreadPoolExecutor`.**
  `concurrent.futures` registers an atexit hook that JOINS its workers, so a
  wedged pool thread blocks interpreter exit even after `shutdown(wait=False)` —
  measured at 8s for an 8s stall. That converts a tool hang into a shutdown
  hang: SIGTERM ignored until the pod's grace period expires, which with a
  ReadWriteOnce PVC and `strategy = "Recreate"` stalls the whole rollout.
- **Carry the caller's context.** `contextvars.copy_context()` is required or
  the owner contextvar silently does not cross the thread and every workflow row
  loses its owner. The write still "succeeds", so nothing surfaces it. This was
  caught only by an existing test.

Probe the path at boot: `bootstrap.ensure_writable_dir` covers only
`RESEARCH_DIR`, so a bad `EVENT_STORE_PATH` used to surface as a hang on the
first storyteller or research write rather than as a startup error.

## Enforcement

`tests/test_event_store_write_deadline.py` — a wedged `_connect` returns within
the deadline, the store degrades, subsequent calls short-circuit, it recovers
after the cooldown, the worker is a daemon, and a subprocess with a 30s-wedged
write still exits promptly. The negative proof is `append_event_safe.__wrapped__`
(preserved by `functools.wraps`): the unguarded function blocks for the full
stall, the guarded one returns at the deadline.

`tests/test_metadata_status.py` locks the healthy, unwritable and degraded
branches of the `system_status` section.

Related: [[default-off-flag-fails-silently]] — same class one layer up, where a
capability that is simply not running reads as healthy.
