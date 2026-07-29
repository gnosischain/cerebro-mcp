
SELECT choice_index, choice,
       if(choice_index <= length(scores), scores[choice_index], NULL) AS score,
       if(choice_index <= length(scores) AND scores_total > 0,
          scores[choice_index] / scores_total, NULL) AS score_share,
       scores_state
FROM (
  SELECT JSONExtract(raw_json, 'choices', 'Array(String)') AS choices,
         JSONExtract(raw_json, 'scores', 'Array(Float64)') AS scores,
         scores_total, scores_state
  FROM governance_db.snapshot_proposals FINAL
  WHERE id = {proposal_id:String}
)
ARRAY JOIN choices AS choice, arrayEnumerate(choices) AS choice_index
ORDER BY choice_index
