---
id: shared-stylesheet-unscoped-selectors
title: An unscoped selector in a shared stylesheet breaks apps that merely import it
status: enforced
layer: mini-app-ui
scope: any .css imported by more than one mini-app entry point
symptom: >-
  layout breakage in a DIFFERENT app, or a different tab of the same app, from the
  one being worked on — cause remote from symptom
last_verified: 2026-07-30
evidence:
  - ui/src/mini-apps/graph-explorer/graph-explorer-shell.css:1-16
  - ui/src/mini-apps/graph-explorer/__tests__/sharedCanvasCss.test.mjs
  - 'importing graph-explorer.css for its canvas applied html,body,#root{height:100%} and body{overflow:hidden} to every governance tab, misaligning .gov-grid-2 chart pairs two tabs away'
---
## Symptom

Charts or panels misaligned in an app you did not touch. The `.gov-grid-2` pairs
on the governance Delegations tab lost their shared height because the Graph tab
imported another app's stylesheet.

## Root cause

`graph-explorer.css` is 4,000 lines of `.ge-*` — except two rules,
`html, body, #root { height: 100% }` and `body { overflow: hidden }`. Those are
app-shell concerns. Importing the file to reuse the WebGL canvas applied them
globally and changed the height cascade app-wide.

The secondary trap: chasing the symptom produced a JS height-measuring hook to
compensate for a cascade that was only broken because of this import. Measuring
around a CSS bug is always the wrong instinct.

## Forbidden action

Leaving an unscoped selector (`html`, `body`, `#root`, or any bare element) in a
stylesheet that another app imports; and adding a JS measurement to work around a
layout value that CSS should be producing.

## Detection

Parse the stylesheet at brace depth 0 and assert every selector is namespaced.
`ui/src/mini-apps/graph-explorer/__tests__/sharedCanvasCss.test.mjs` does exactly
this — note it must strip comments first, or prose describing the moved rules
matches the regex.

## Safe remediation

Split app-shell globals into a `*-shell.css` imported only by that app's entry
point, leaving the shared file wholly namespaced.

## Enforcement

`sharedCanvasCss.test.mjs` guards `graph-explorer.css`. `governance.css`,
`mini-app-chrome.css`, `data-catalog.css` and `case-study.css` have **no
equivalent gate**.
