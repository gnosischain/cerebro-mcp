-- How the vote accumulated PER CHOICE over the voting window, plus the quorum
-- threshold — the Snapshot proposal page's two questions on one time axis:
-- which way is it going, and has it cleared quorum.
--
-- RESOLVING THE CHOICE. `choice` lives only in the vote's raw_json and its shape
-- depends on the proposal type. Measured over the whole gnosis.eth space:
--   basic          204 proposals, 47,539 votes -> Int64 (1-based index)
--   single-choice    7 proposals,    522 votes -> Int64
--   ranked-choice    1 proposal,      75 votes -> Array
-- The index is resolved against THIS proposal's OWN `choices` array, read from
-- the same document, never against a hardcoded position. That distinction is the
-- whole point: "For" is index 1 on most proposals but nothing guarantees it, and
-- `versioned-payload-positional-index` records what assuming otherwise costs.
-- Anything that is not an Int64 in range yields NULL, which surfaces as an
-- explicit "unsupported choice shape" series rather than being dropped — the one
-- ranked-choice proposal must stay visible and countable in its own trend, not
-- silently vanish from it.
--
-- QUORUM IS NULL, NEVER 0, WHEN UNSPECIFIED. 106 of 253 proposals carry
-- quorum = 0 — every proposal before 2024-01-19, when the space had no quorum
-- configured. Rendering that as a threshold line at zero would assert a bar that
-- every proposal trivially clears. NULL lets the UI say "unspecified", the third
-- term in this repo's met / missed / unspecified vocabulary.
--
-- Verified against GIP-151 (0x657fbf...74c4): summing vote VP per choice
-- reproduces Snapshot's own `scores` array to the rounding —
-- For 157,747 / Against 2,499 / Abstain 1,492 against
-- scores [157748.63, 2499.57, 1492.02].
--
-- FINAL is mandatory on both tables: governance_db is re-inserted daily and
-- neither snapshot_votes nor snapshot_proposals dedups on read.
WITH proposal AS (
  SELECT JSONExtract(raw_json, 'choices', 'Array(String)') AS choices,
         nullIf(quorum, 0) AS quorum_vp
  FROM governance_db.snapshot_proposals FINAL
  WHERE id = {proposal_id:String}
),
per_bucket AS (
  SELECT toStartOfHour(v.created_at) AS bucket,
         multiIf(
           JSONType(v.raw_json, 'choice') != 'Int64', NULL,
           JSONExtract(v.raw_json, 'choice', 'Int64') < 1, NULL,
           JSONExtract(v.raw_json, 'choice', 'Int64') > length(p.choices), NULL,
           p.choices[JSONExtract(v.raw_json, 'choice', 'Int64')]
         ) AS choice_label,
         count() AS votes,
         sum(v.vp) AS vp,
         any(p.quorum_vp) AS quorum_vp
  FROM governance_db.snapshot_votes AS v FINAL
  CROSS JOIN proposal AS p
  WHERE v.proposal_id = {proposal_id:String}
  GROUP BY bucket, choice_label
)
SELECT bucket,
       coalesce(choice_label, 'unsupported choice shape') AS choice,
       votes,
       round(vp) AS vp,
       -- Cumulative per choice. PARTITION BY the raw label, not the coalesced
       -- one, so the unsupported bucket accumulates as its own series.
       toUInt64(sum(votes) OVER (PARTITION BY choice_label ORDER BY bucket)) AS cumulative_votes,
       round(sum(vp) OVER (PARTITION BY choice_label ORDER BY bucket)) AS cumulative_vp,
       round(quorum_vp) AS quorum_vp,
       'hour' AS bucket_unit
FROM per_bucket
ORDER BY bucket, choice
