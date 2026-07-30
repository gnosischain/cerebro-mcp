# Mini-app front-ends — scoped guide

React + ECharts + WebGL. Run
`get_cerebro_change_context(paths="ui/src/mini-apps")` for the live hazard list.

## The bundles are prebuilt — this is the number-one trap

The server serves git-tracked bundles from `src/cerebro_mcp/static/`, **not**
`ui/src`. So:

- a source edit changes nothing until `make build-ui-<app>` (which builds *and*
  copies into `static/`);
- `make dev` serves live source and therefore **cannot reproduce a bundle bug**;
- shared code (`components/ChartCard.tsx`, `themes/global.css`, the graph-explorer
  canvas) means one edit can require rebuilding all 11 apps.

Never claim a UI change works without having rebuilt.

## CSS

- **No unscoped selectors in a stylesheet another app imports.** Two rules
  (`html, body, #root { height: 100% }`, `body { overflow: hidden }`) in
  graph-explorer.css misaligned chart grids in a *different app, two tabs away*. App
  shell globals belong in a `*-shell.css` imported only by that entry point.
- **Fill the fold with the flex chain, not a measured height.** `100vh` is wrong
  inside `.ma-body`; `.ma-body.clientHeight` can read 46px if an ancestor chain is
  broken. If you are reaching for JS measurement, suspect the cascade first.
- Grid `stretch` matches a sibling panel to a chart **with no height declared** —
  `align-items: start` is what cuts it short.
- An `auto` cross-axis margin inside a flex column **absorbs free space instead of
  stretching**, so `margin: 0 auto` silently shrinks a flex child to fit-content.
- `overflow-x: auto` with unspecified `overflow-y` clips **both** axes, and clipping
  beats any `z-index`.

## Charts

- An ECharts `graph` series on `coordinateSystem: 'cartesian2d'` emits **no click
  events**. Use `scatter` + `lines` (and register `LinesChart`).
- A `lines` series with no explicit `lineStyle.color` falls back to the palette.
- ECharts **invents a node** for an unknown link endpoint, rendering a phantom entity
  as real-but-empty. Filter edges to those whose both endpoints exist.
- No zoom slider bars — `dataZoom` inside-type only.

## WebGL canvas

- Cosmos's GPU point pick does not report hits on this stack; selection goes through
  a CPU fallback (`getPointsInRect`). Do not "simplify" that away.
- A memo held in a ref **outlives the renderer it describes** — key it on the graph
  instance or a remount hands the fresh graph no buffers.
- The force sim never fully stops (keep-warm re-injects alpha), so node positions
  drift between reading them and clicking them. Compute and act in the same tick.

## Before you finish

`npm test --prefix ui` then `make build-ui-<app>`.
