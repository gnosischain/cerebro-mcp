observed_at>=(
          SELECT max(observed_at) FROM cow_db.native_prices FINAL
          WHERE environment={env:String} AND chain_id={chain_id:UInt64}
            AND token IN ({base:String},{quote:String})
        )-toIntervalDay({window_days:UInt32})
