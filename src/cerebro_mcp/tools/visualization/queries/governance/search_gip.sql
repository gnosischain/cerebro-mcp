
SELECT entity_type, identifier, label, role, evidence_count, match_rank
FROM (
@gip_arms
)
ORDER BY entity_type, identifier
