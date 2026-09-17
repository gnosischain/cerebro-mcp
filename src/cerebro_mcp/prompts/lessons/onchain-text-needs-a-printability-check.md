---
id: onchain-text-needs-a-printability-check
title: >-
  A decoded on-chain string is attacker-authored bytes, and the obvious guard
  (a replacement character) does not catch the common failure
status: observed
layer: mcp-tool
scope: >-
  every place a contract-supplied string reaches a surface a person reads —
  ERC-20 symbol/name over RPC (tools/visualization/token_rpc.py), decoded call
  returns in rpc_scan/decoding.py, contract_explorer output, and any future
  token-labelling path. Not the SQL planes, where symbols arrive already
  resolved by an indexer.
symptom: >-
  a token symbol that renders as blank, as boxes, as a run of invisible
  characters, or as a line of text far longer than a symbol — with no error
  anywhere, and only for some tokens
last_verified: 2026-09-17
evidence:
  - >-
    verified 2026-09-17: a bytes32 symbol field holding bytes 0x00-0x1f decodes
    through `.decode("utf-8", "replace")` with NO replacement character, because
    every one of those bytes IS valid UTF-8. The mojibake guard passed it and
    the resolver returned a symbol made of control characters
  - >-
    caught by tests/test_token_rpc.py::test_bytes_that_are_not_text_are_not_passed_off_as_a_symbol
    while the guard was still the replacement-char check — the test was written
    first and failed against the implementation, which is how the gap surfaced
  - src/cerebro_mcp/tools/visualization/token_rpc.py `_clean_text`
  - tests/test_token_rpc.py::test_a_symbol_of_control_characters_is_rejected
  - tests/test_token_rpc.py::test_an_absurdly_long_symbol_is_rejected
  - >-
    fix in tree 2026-09-17, pending deploy — status stays observed until merged
---

## Symptom

A token label renders as nothing, as replacement boxes, as invisible characters
that break the row's alignment, or as a paragraph where a four-character symbol
belongs. Only some tokens are affected, nothing raises, and the decode step
reports success.

## Root cause

Two separate mistakes that look like one.

First: `symbol()` and `name()` have two on-chain shapes. The current ABI says
`string`; the original ERC-20 said `bytes32`, and long-lived tokens never
migrated. A string-only decoder reports every bytes32 token as nameless, which
is indistinguishable from a token that genuinely has no symbol.

Second, and the subtle one: the natural guard for the bytes32 branch is to
decode with `errors="replace"` and reject anything containing `�`. That
does not work. Bytes `0x00`-`0x1f` are all **valid UTF-8**, so a field holding
raw control bytes decodes cleanly, produces no replacement character, and sails
through into the UI as a "symbol". Length is unguarded by the same reasoning —
a contract may return a kilobyte of text and the decode still succeeds.

Underneath both: this is attacker-authored data. A contract chooses what its
`symbol()` returns, and nothing on chain constrains it to be short, printable,
or unique.

## Forbidden action

Treating a successful decode as a usable label. Specifically: relying on a
replacement-character check to prove text is displayable, decoding only the
`string` shape, or passing a contract-supplied string through without a length
bound.

## Detection

Feed the decoder a bytes32 field of `bytes(range(32))` and a 500-character
string. If either comes back as a value rather than as absent, the guard is the
replacement-char one and the gap is live. In rendered output, look for rows
whose label column is blank or whose height differs from its neighbours.

## Safe remediation

Decode `string` first, fall back to `bytes32`, and put both through one
acceptance check before either is returned:

* `str.isprintable()` — this is the load-bearing test, not the replacement-char
  one.
* a length bound (64 characters is generous for a symbol or a name).
* reject `�` as well, for the genuinely invalid-UTF-8 case.

Return `None`, never `""` and never a placeholder: the consumer renders a short
address from the absence, and an unreadable token must not become
indistinguishable from a nameless one. Record which shape decoded, so a value
that went through the fallback can be seen rather than inferred.

Sanitising at the source beats hoping every consumer remembers to — the client
already has its own `sanitizeSymbol`, and relying on it alone leaves any other
consumer of the same value exposed.

## Enforcement

`tests/test_token_rpc.py` covers both shapes, the control-byte case, the
oversized case, the empty-string case and the invalid-UTF-8 case, and each was
mutation-checked by removing the guard and confirming the failure. Reaching
`enforced` needs the same acceptance check applied wherever else this repo
surfaces a contract-supplied string, which today it does not.
