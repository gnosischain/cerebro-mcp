---
{
  "id": "current_onchain_state",
  "label": "Current on-chain state",
  "purpose": "Read live chain state for one contract or address — balance, owner, supply, paused, proxy target — straight from RPC.",
  "category": "answer",
  "tier": "quick_answer",
  "deliverable": "The current value(s) read directly from the chain, each attributed to the block it was read at.",
  "params": [
    {"name": "WHAT", "description": "The state to read", "example": "total supply and owner"},
    {"name": "TARGET", "description": "Contract address or well-known name (chain if not Gnosis)", "example": "the GNO token contract on Gnosis Chain"}
  ],
  "personas": ["chain_state_analyst"],
  "verify_personas": ["chain_state_analyst"],
  "requires": [],
  "benchmark": {"runs": 3, "timeout_s": 300, "budget_usd": 1.5, "verify": "answer"}
}
---

Adopt the chain-state persona first: call `get_agent_persona("chain_state_analyst")` and follow its rules.

Read the current on-chain state: {{WHAT}} for {{TARGET}}.

This is a point-in-time chain read — the RPC plane, not the warehouse:

1. Resolve the target if needed (`resolve_address` / `contract_explore`), then read the state with `contract_call_function` — one round-trip per value.
2. If the target is a proxy, say so and read through the implementation as the persona prescribes.
3. Attribute every value to the block it was read at.

Reply with the value(s), the block number/timestamp of the read, and the contract address you actually queried. No charts, no report, no historical analysis — if the question turns historical or incident-shaped, say that it needs a different workflow instead of attempting it.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
