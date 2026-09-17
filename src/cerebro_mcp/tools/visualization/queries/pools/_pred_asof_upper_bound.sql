-- "The latest snapshot published on or before the requested date" — never an
-- equality. A requested day the indexer did not publish resolves backwards to
-- the newest day it did, and every dataset echoes the resolved as_of so the
-- caller can see the shift rather than an empty panel.
snapshot_date <= {as_of:Date}
