"""Scan runners. Each takes (job, spec, *, rpc, store[, ch]) and streams rows
into the job's scratch table with unit-based checkpointing (jobs.commit_unit)."""
