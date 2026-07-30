---
id: echarts-curveness-sign-is-relative-to-the-chord
title: An ECharts curveness sign picks a side relative to the CHORD, so flipping both cancels
status: enforced
layer: mini-app-ui
scope: >-
  every `lines`-on-cartesian2d arc surface — the governance GIP timeline, and any
  chart that draws directed edges over a value axis
symptom: >-
  edges near an axis boundary are simply absent; arcs that should fan to both
  sides all bow the same way
last_verified: 2026-07-30
evidence:
  - 'measured with echarts 5.6 SSR on a 600x400 canvas: both curveness families apexed at y=418.8 with the grid floor at y=356 — 62px outside the grid, clipped away'
  - 'inverting the sign put the same arcs at apex 301.3, inside the plot'
  - 'ui/src/mini-apps/governance/__tests__/gipTimelineArcs.test.ts — 3 of its 4 assertions fail against the pre-fix code'
---
## Symptom

Directed edges vanish. Specifically, edges between nodes sitting on or near an axis
boundary: the arc is drawn, bows past the boundary, and a cartesian series clips it
to nothing. Reported as "there are edges that cross the 0 level and we never see
them".

## Root cause

ECharts places the quadratic control point at

```
cpx = midX - (y1 - y2) * curveness
cpy = midY - (x2 - x1) * curveness        # pixel space, y grows DOWNWARD
```

so which side an arc falls on is decided by the sign of `curveness` **times the
sign of the chord**, not by `curveness` alone.

The governance timeline read `curveness: backward ? 0.4 : -0.4` with
`coords: [src, dst]` — and `backward` was defined as `dst < src`, the same ordering
that determines the chord's direction. The two flips cancelled: every arc bowed the
same way, downward. The comment claiming they were drawn "on the opposite side so
they stand out" described something that never happened.

Downward is the one direction that could not work: the y axis is a citation count,
so it floors at 0 and most nodes sit on the floor.

## Forbidden action

Deriving an arc's side from a data property (direction, category, sign of a value)
while the endpoint order in `coords` depends on that same property. Assuming
`curveness > 0` means a fixed side.

## Detection

Measure, do not reason — the formula is easy to misremember and the answer depends
on pixel-space y growing downward. `echarts.init(null, null, {renderer: "svg",
ssr: true, width, height})` renders headless and `renderToSVGString()` gives real
path data, so the apex is checkable in a unit test:

```
apexY = 0.25 * y1 + 0.5 * cy + 0.25 * y2     # the quadratic at t = 0.5
```

Compare against `height - grid.bottom`. A fixture also has to be built to the real
geometry: two mutually-citing nodes are NOT both at 0 (being cited is what the axis
counts), and a two-node fixture collapses the axis to [0,1] and puts them at the
top — the opposite of the case under test.

## Safe remediation

Take the sign from the chord (`Date.parse(to.x) >= Date.parse(from.x)`) so the arc
always bows into the plot, and move the categorical distinction to colour and
curvature **magnitude**, which survive both families being on the same side. Add
`clip: false` so an arc near the axis *max* cannot vanish the same way — a silently
dropped edge is the failure being fixed.

## Enforcement

`ui/src/mini-apps/governance/__tests__/gipTimelineArcs.test.ts` renders the real
option through ECharts and asserts the measured apex is inside the grid, that the
bow direction survives either endpoint ordering, and that `clip` is `false`. It was
negative-tested: 3 of 4 assertions fail against the pre-fix code. The older
option-level assertion in `gipGraph.test.ts` was rewritten — it had asserted the
two signs were opposite, which was true and yet exactly the bug.
