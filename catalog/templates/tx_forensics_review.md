---
{
  "id": "tx_forensics_review",
  "label": "Transaction forensics (reviewed)",
  "purpose": "What one transaction actually did — decoded leg by leg, then adversarially adjudicated by the forensic reviewer.",
  "category": "attribution",
  "tier": "persona_workflow",
  "deliverable": "A written forensic brief: the transaction's atomic shape, value reconciliation, evidence-tiered findings, and the reviewer's per-claim verdicts.",
  "params": [
    {"name": "TX_HASH", "description": "Transaction hash to decode (Gnosis Chain unless stated)", "example": "the largest GNO transfer transaction of the last 7 days on Gnosis Chain"}
  ],
  "personas": ["transaction_forensics", "forensic_reviewer"],
  "verify_personas": ["transaction_forensics", "forensic_reviewer"],
  "requires": [],
  "benchmark": {"runs": 2, "timeout_s": 1200, "budget_usd": 8.0, "verify": "answer"}
}
---

This is a two-persona forensic chain with a mandatory adversarial adjudication.

**Decode.** Call `get_agent_persona("transaction_forensics")` and follow its SOP for: {{TX_HASH}}. If given a description rather than a hash, first locate the transaction with one warehouse query, then decode THAT hash. Work the persona's discipline: legs in `(block, tx_index, log_index)` order, the atomic shape identified, value-in vs value-out reconciled including the residual, every finding tiered by evidence (E0-E3) with calibrated confidence and an alternative hypothesis. Build the evidence ledger as you go — the reviewer instantly blocks a submission without one. No intent words ("attacker", "stolen") without a cited external basis.

**Adjudicate.** Call `get_agent_persona("forensic_reviewer")` and, as that reviewer, run its checks against your own submission: re-derive the headline numbers independently, verify evidence tiers, adjudicate each claim (KEEP / DOWNGRADE / RESTATE / STRIKE). The reviewer may only downgrade, never upgrade. A review that keeps everything untouched is suspect — look harder.

**Deliver.** A single written brief: the standing header, what the transaction did (adjudicated), the value reconciliation table, findings with final evidence tiers and confidence, the reviewer's per-claim verdict list, and open questions. No report artifact — the brief is the deliverable.

Publishing discipline (non-negotiable):
- Every number you state in prose must come from a query you actually ran in THIS session, and its supporting query or data must be visible in the deliverable. Never state a statistic (correlation, median, share, trend percent) you did not compute.
- Never name a method (first-differencing, deduplication, medians, cohorting) unless your SQL actually implements it.
- Avoid trend or stability words ("trending up", "held steady", "spike") unless you computed the supporting statistic (slope, stddev, or an explicit period comparison) — otherwise report the plain values and let them speak.
- If the data's definition differs from what was asked (proxy metric, partial final period, different grain), say so explicitly next to the number.
- Final cross-check before you publish or reply: re-read every number in your prose and re-verify it against the exact values in your chart data or query results; fix any mismatch, and for superlatives ("largest", "first", "record") confirm against the full series, not memory.
