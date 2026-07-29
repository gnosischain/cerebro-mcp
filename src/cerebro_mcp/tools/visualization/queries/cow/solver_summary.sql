
SELECT uniqExact(auction_id) AS competitions,
       count() AS solutions,
       countIf(is_winner) AS wins,
       countIf(is_winner)/nullIf(uniqExact(auction_id),0) AS win_rate,
       countIf(is_winner AND ranking!=1) AS multi_winner_solutions,
       countIf(is_winner AND ranking!=1)/nullIf(countIf(is_winner),0) AS multi_winner_share,
       countIf(toUInt256OrZero(score)=0 AND score NOT IN ('','0')) AS score_parse_failures,
       avg(toFloat64(ranking)) AS average_ranking,
       min(ranking) AS best_ranking,
       (SELECT uniq(tx_hash) FROM cow_db.settlements
        WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}) AS executed_settlements,
       max(observed_at) AS source_observed_at
FROM cow_db.competition_solutions FINAL
WHERE environment={env:String} AND chain_id={chain_id:UInt64} AND solver={id:String}
ORDER BY competitions
