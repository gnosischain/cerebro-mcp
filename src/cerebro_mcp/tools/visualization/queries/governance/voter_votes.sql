
SELECT v.id AS vote_id, v.proposal_id AS proposal_id,
       p.title AS proposal_title, p.state AS proposal_state,
       v.created_at AS created_at, v.vp AS vp, v.vp_state AS vp_state,
       multiIf(JSONType(v.raw_json, 'choice') IN ('Int64', 'UInt64'), 'single',
               JSONType(v.raw_json, 'choice') = 'Array', 'ranked',
               'unsupported') AS choice_kind,
       if(choice_kind = 'single',
          JSONExtract(v.raw_json, 'choice', 'Int32'), NULL) AS choice_index,
       if(choice_kind = 'ranked',
          JSONExtract(v.raw_json, 'choice', 'Array(Int32)'), []) AS choice_indexes,
       if(choice_kind = 'single' AND choice_index >= 1
          AND choice_index <= length(p.choices),
          p.choices[choice_index], '') AS choice_label,
       JSONExtractString(v.raw_json, 'reason') AS reason
FROM governance_db.snapshot_votes AS v FINAL
LEFT JOIN (
  SELECT id, title, state,
         JSONExtract(raw_json, 'choices', 'Array(String)') AS choices
  FROM governance_db.snapshot_proposals FINAL
) AS p ON p.id = v.proposal_id
WHERE lower(v.voter) = {voter:String}
ORDER BY v.created_at DESC, vote_id
