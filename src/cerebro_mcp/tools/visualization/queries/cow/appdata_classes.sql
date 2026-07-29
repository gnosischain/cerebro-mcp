
WITH ad AS (
  SELECT app_data_hash,
         JSONExtractString(JSONExtractString(argMax(full_app_data,observed_at),'fullAppData'),
                           'metadata','orderClass','orderClass') AS order_class
  FROM cow_db.app_data
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY app_data_hash
),
od AS (
  SELECT chain_id,app_data_hash,uniq(order_uid) AS orders,
         uniqExactState(owner) AS owners_state,max(observed_at) AS obs_at
  FROM cow_db.orders
  WHERE environment={env:String} AND chain_id IN (@ids)
  GROUP BY chain_id,app_data_hash
)
SELECT od.chain_id AS chain_id,
       multiIf(od.app_data_hash='','unresolved',
               ad.app_data_hash='','unresolved',
               ad.order_class='','untagged',ad.order_class) AS order_class,
       sum(od.orders) AS orders,uniqExactMerge(od.owners_state) AS owners,
       uniqExactIf(od.app_data_hash,ad.app_data_hash!='') AS appdata_hashes,
       max(od.obs_at) AS source_observed_at
FROM od
LEFT JOIN ad ON ad.app_data_hash=od.app_data_hash
GROUP BY od.chain_id,order_class
ORDER BY od.chain_id,orders DESC
