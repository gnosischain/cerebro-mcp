# Connector work — changes that affect EXISTING deployments

Most of the connector hardening is gated behind
`MCP_SURFACE_PROFILE=team_analytics_v1`. These changes are **not** — they
apply to the current stdio / `internal_full` deployments too, and each is a
deliberate breaking change rather than an oversight. (An earlier summary
claimed "off-profile behavior is byte-for-byte unchanged"; that was wrong,
and this file is the correction.)

## 1. Report URLs no longer carry the shared token

- **Was:** `/reports/<id>?token=<MCP_AUTH_TOKEN>`, and the route accepted
  the shared token as a Bearer header or a query param.
- **Now:** `/reports/<full-id>?cap=<signed capability>`. The route accepts
  the capability only.
- **Why:** the token in the URL was the *whole-server* credential (GDPR
  H4) — a leaked report link was an arbitrary-SELECT-and-delete credential.
- **Action:** set `CEREBRO_SIGNING_KEY` (≥ 32 bytes) wherever reports are
  served over HTTP. Without it **no HTTP report link is emitted at all**
  (the tool falls back to a `file://` path); a link that cannot be signed
  is never handed out, because it would 401 forever.
- Reports created before this change have no authz row: run
  `scripts/backfill_report_index.py` once, or their links will not resolve.

## 2. `/mcp?token=` is removed

- **Was:** `/mcp` accepted `?token=<MCP_AUTH_TOKEN>` as well as the header.
- **Now:** the `Authorization` header only, compared constant-time.
- **Why:** a credential in a query string lands in ALB access logs. The
  original justification (a connector UI with no header field) does not
  hold — `static_headers` allowlists `authorization`, and OAuth always
  sends the header.
- **Action:** any client configured with `?token=` must move the value to
  an `Authorization: Bearer` header.

## 3. Two defaults flipped fail-closed

| Setting | Was | Now | Effect |
|---|---|---|---|
| `REPORT_STUDIO_ALLOW_MUTATIONS` | `True` | `False` | Report rename/delete and the composer return a structured "disabled" error until explicitly enabled. `delete_report_archive_entry` was browser-reachable on the shared credential. |
| `MINI_APP_BROWSER_ENABLED` | (new) | `False` | `/app/{id}`, its POST tool dispatch, and the `/` + `/apps` catalog are **not registered**. The mini-apps stay reachable through their `ui://` MCP resources. |

Set either to `true` for a trusted single-user/local deployment that wants
the old behavior.

## 4. `CEREBRO_AUTHZ_DB_PATH` is home-anchored

Default `~/.cerebro/cerebro_authz.db` — deliberately not cwd-relative, so
it cannot drift away from `~/.cerebro/reports` when the server is started
from a different directory. Off-profile the authorization store is **not**
on the report-creation path at all; only the connector profile requires it.

## 5. SQL relation checks are stricter for everyone

`validate_query` now rejects a database-qualified reference to a database
outside `ALLOWED_DATABASES` (previously only the *connection* database was
checked — GDPR audit M1), extracts relations via the sqlglot AST so
comma cross-joins/subqueries/UNION arms are all seen, and denies a much
wider set of table functions (`remoteSecure`, `sqlite`, `merge`,
`cluster*`, `dict*`, …).

Legitimate analyst SQL is unaffected — alias-qualified columns
(`a.user_pseudonym`) and aggregate combinators (`sumMerge(`) were verified
not to trip it — but any query that genuinely reached across databases will
now be refused.
