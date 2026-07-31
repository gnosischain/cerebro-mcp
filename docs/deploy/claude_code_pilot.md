# Claude Code pilot — internal, pre-connector (R10 stage 0b)

The pilot runs BEFORE any public exposure: 3–5 analysts self-add the
internal endpoint over Twingate. No Owner action, no org connector, no
public DNS — a Claude Team custom connector is organization-wide and
Owner-added, so a scoped claude.ai pilot does not exist; Claude Code is
the pilot surface.

## Add the server

```bash
claude mcp add --transport http cerebro https://mcp.analytics.gnosis.io/mcp \
  --header "Authorization: Bearer $MCP_AUTH_TOKEN" \
  --header "X-Cerebro-Owner: <your-email>"
```

## What the pilot proves — and what it cannot

- **`X-Cerebro-Owner` is SELF-ATTESTED.** The code does not verify it
  (`server.py`, `BearerAuthMiddleware`: "We do NOT verify the claim
  here"). On the shared token it is usage telemetry, not an authorization
  boundary — any pilot user can impersonate any other by editing the
  header. Real identity arrives with the OAuth cutover
  (`MCP_SURFACE_PROFILE=team_analytics_v1`).
- Working over Twingate proves NOTHING about claude.ai reachability:
  public DNS for this hostname serves RFC1918 addresses, which claude.ai
  rejects before sending a single request ("works in curl and Claude Code
  but not claude.ai" is exactly this fault).
- Emergency response for a leaked pilot token is GLOBAL rotation of
  `MCP_AUTH_TOKEN` — per-user revocation starts with OAuth (the ≤60 s
  tombstone SLA begins only once the legacy token is retired).

## Exit criteria (before the Owner adds the org connector)

- Each analyst's workflows/reports carry a DISTINCT owner hash
  (`event_store.list_workflows`), count equal to the pilot size.
- No cross-owner report access in the audit trail.
- Report links open for their owner and are denied for everyone else
  (signed capabilities, once deployed).
- `tools/list` shows the 44-tool profile surface once
  `team_analytics_v1` is active on the pilot deployment.
