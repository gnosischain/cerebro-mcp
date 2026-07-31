#!/usr/bin/env python
"""Operator revocation CLI (connector plan R10 P0-4 / C6).

Emergency deprovisioning sequence — IN ORDER, all steps:

  1. LOCAL TOMBSTONE (this script, --deny) — written FIRST. This alone
     achieves the <=60 s local guarantee: it rejects both already-issued
     tokens and freshly REFRESHED ones, which the min_iat watermark cannot
     (a refresh mints a token newer than any watermark).
  2. Auth0: revoke the subject's refresh grants and sessions (Management
     API / dashboard — supportive, asynchronous, never the guarantee).
  3. IdP: remove the group assignment (passive path; 45-60 min operating
     target via Directory Sync).
  4. Optionally --min-iat to reject already-issued access tokens早 within
     their 15-minute lifetime.

Unblocking is a SEPARATE, separately-audited operation (--unblock) — a
second deliberate action, never a side effect. Every deny/unblock appends
to the immutable denial_events audit log.

The subject is identified by issuer+subject (hashed with the v1 owner key,
which must be in the environment) or by a raw owner hash (--owner-hash)
when the caller already has it.

Usage:
    scripts/cerebro_revoke.py --issuer https://t.auth0.com/ --subject "auth0|x" \
        --deny --reason "offboarded" --actor ops@gnosis.io
    scripts/cerebro_revoke.py --owner-hash v1:abcd... --min-iat now --actor ops@gnosis.io
    scripts/cerebro_revoke.py --owner-hash v1:abcd... --unblock --reason "rehired" --actor ops@gnosis.io
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    who = parser.add_argument_group("subject (either owner-hash OR issuer+subject)")
    who.add_argument("--owner-hash", help="v1:<hex> owner hash")
    who.add_argument("--issuer", help="token issuer URL")
    who.add_argument("--subject", help="token subject claim")
    parser.add_argument("--deny", action="store_true", help="write the tombstone")
    parser.add_argument("--unblock", action="store_true", help="audited unblock")
    parser.add_argument(
        "--min-iat",
        help="reject tokens issued before this unix timestamp ('now' accepted)",
    )
    parser.add_argument("--actor", required=True, help="who is performing this")
    parser.add_argument("--reason", default="", help="why (recorded in the audit log)")
    args = parser.parse_args()

    if args.owner_hash:
        owner = args.owner_hash
    elif args.issuer and args.subject:
        from cerebro_mcp.runtime.identity import owner_hash_v1

        owner = owner_hash_v1(args.issuer, args.subject)
    else:
        parser.error("provide --owner-hash OR both --issuer and --subject")
        return 2

    from cerebro_mcp.workflow.authz_store import get_authz_store

    store = get_authz_store()
    acted = False
    if args.deny:
        if not args.reason:
            parser.error("--deny requires --reason")
        store.deny_subject(owner, actor=args.actor, reason=args.reason)
        print(f"tombstone written for {owner[:16]}…")
        print(
            "NEXT (manual, in order): revoke Auth0 refresh grants + sessions; "
            "remove the IdP group assignment; consider --min-iat now."
        )
        acted = True
    if args.min_iat:
        ts = int(time.time()) if args.min_iat == "now" else int(args.min_iat)
        store.set_revocation_watermark(owner, ts)
        print(f"revocation watermark set to {ts} for {owner[:16]}…")
        acted = True
    if args.unblock:
        if not args.reason:
            parser.error("--unblock requires --reason")
        store.unblock_subject(owner, actor=args.actor, reason=args.reason)
        print(f"unblocked {owner[:16]}… (audited)")
        acted = True
    if not acted:
        parser.error("nothing to do: pass --deny, --unblock and/or --min-iat")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
