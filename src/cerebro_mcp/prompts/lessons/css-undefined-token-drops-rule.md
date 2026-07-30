---
id: css-undefined-token-drops-rule
title: A var() on an undefined custom property silently drops the whole declaration
status: enforced
layer: mini-app-ui
scope: every .css under ui/src — mini-app stylesheets and the shared chrome
symptom: >-
  an element that clearly carries the right class renders with none of that rule's
  styling — no border, no background, default colour — and nothing appears in the
  console
last_verified: 2026-07-30
evidence:
  - 'measured in the browser 2026-07-30: .gov-livenow__col computed borderTopWidth 0px and backgroundColor rgba(0,0,0,0) while carrying the class, because it set them via --border-subtle and --surface-raised'
  - 'ui/src/mini-apps/governance/governance.css had 10 uses of --border-subtle, 6 of --surface-raised, 7 of --accent — none of the three is assigned anywhere in the repo'
  - ui/src/mini-apps/__tests__/cssTokensExist.test.mjs
---
## Symptom

A styled panel renders as bare text. The class is present in the DOM, the rule is
present in the stylesheet, and the computed style shows the property at its initial
value — `border-top-width: 0px`, `background-color: rgba(0,0,0,0)`. No console
warning, no devtools strikethrough that draws the eye, nothing to grep for.

The governance Overview's two top cards ("Open votes", "Moving toward a GIP") looked
like unstyled prose on the page background for exactly this reason.

## Root cause

`var(--x)` where `--x` was never assigned makes the declaration **invalid at
computed-value time**. That is not the same as a syntax error: the parser accepts
it, so the rule exists and the declaration is discarded at cascade time. The
property falls back to its inherited or initial value.

Invented token names are easy to write because the real palette is large (69
tokens) and plausible names like `--border-subtle`, `--surface-raised`, `--accent`
are *not* among them. The real ones here are `--border`, `--surface-2`, `--primary`
/ `--accent-text`.

## Forbidden action

Writing `var(--token)` without confirming the token is assigned somewhere, and
using a fallback-free `var()` for anything load-bearing.

## Detection

Grep, because the browser will not tell you:

```
grep -rhoE 'var\(--[a-z0-9-]+' ui/src --include='*.css' | sort -u
```

and compare against the assigned set (`--x:`). Or read the computed style of a
element you believe is styled — a `0px` border on a bordered class is the tell.

## Safe remediation

Use a token that exists, or give the `var()` a fallback
(`var(--maybe, rgba(255,255,255,0.12))`), which is valid even when the token is
absent. Note `--rr-*` / `--cs-*` tokens ARE defined, but only on their own scoped
root — a file-local token is fine as long as the element is inside that root.

## Enforcement

`ui/src/mini-apps/__tests__/cssTokensExist.test.mjs` asserts every fallback-free
`var()` in every stylesheet resolves to an assigned token, with a **shrink-only**
allowlist for pre-existing debt in other apps (`--text`, `--danger`,
`--surface-subtle`, `--radius-md`, `--mono`): an allowlist entry that no longer
corresponds to a real violation fails the test, so the backlog can only go down.
