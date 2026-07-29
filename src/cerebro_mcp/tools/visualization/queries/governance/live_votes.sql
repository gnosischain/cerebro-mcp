
SELECT id AS proposal_id,
       title,
       @gip_title AS gip,
       state,
       start_at,
       end_at,
       -- Whole hours left. The UI renders "ends in Nh"; a fractional hour would
       -- imply a precision the daily-ish ingest cadence does not have.
       toInt64(dateDiff('hour', now(), end_at)) AS hours_left,
       votes_count,
       scores_total,
       quorum,
       @quorum_status_sql AS quorum_status,
       @quorum_ratio_sql AS quorum_ratio
FROM @gov_db.snapshot_proposals FINAL
WHERE state = 'active' AND end_at > now() AND start_at <= now()
ORDER BY end_at, proposal_id
