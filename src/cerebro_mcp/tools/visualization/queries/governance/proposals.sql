
SELECT id, title, state, type, author, created_at, start_at, end_at,
       snapshot_block, scores_total, quorum, votes_count, scores_state,
       @quorum_ratio_sql AS quorum_ratio,
       @quorum_status_sql AS quorum_status,
       @gip_title AS gip_number,
       discussion,
       @discussion_topic_sql AS discussion_topic_id,
       JSONExtract(raw_json, 'choices', 'Array(String)') AS choices,
       JSONExtract(raw_json, 'scores', 'Array(Float64)') AS scores,
       length(choices) = length(scores) AND length(scores) > 0 AS len_ok,
       if(len_ok AND arrayMax(scores) > 0,
          choices[indexOf(scores, arrayMax(scores))], '') AS leading_choice,
       if(len_ok AND scores_total > 0,
          arrayMax(scores) / scores_total, NULL) AS leading_choice_share,
       length(choices) != length(scores) AND length(scores) > 0 AS choice_shape_flagged
FROM governance_db.snapshot_proposals FINAL
WHERE @where
ORDER BY @sort_fragment
