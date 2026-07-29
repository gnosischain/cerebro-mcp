
SELECT id AS vote_id, lower(voter) AS voter_key, voter, created_at, vp,
       vp_state,
       multiIf(JSONType(raw_json, 'choice') IN ('Int64', 'UInt64'), 'single',
               JSONType(raw_json, 'choice') = 'Array', 'ranked',
               'unsupported') AS choice_kind,
       if(choice_kind = 'single',
          JSONExtract(raw_json, 'choice', 'Int32'), NULL) AS choice_index,
       if(choice_kind = 'ranked',
          JSONExtract(raw_json, 'choice', 'Array(Int32)'), []) AS choice_indexes,
       JSONExtractString(raw_json, 'reason') AS reason
FROM governance_db.snapshot_votes FINAL
WHERE proposal_id = {proposal_id:String}
ORDER BY vp DESC, id
