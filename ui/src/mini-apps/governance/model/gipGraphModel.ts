// Adapter: GIP citation data -> graph-explorer's GraphModel.
//
// The Clusters view runs on the SAME WebGL canvas as the Graph Explorer
// (@cosmos.gl/graph via GraphCanvas) rather than a second hand-rolled force
// chart. That buys fit-to-view, zoom, focus mode, a label overlay, a legend, an
// error boundary and a keyboard/table fallback — all of which this tab was
// missing and all of which already exist and are tested.
//
// buildGraphModel takes raw row ARRAYS in a fixed column order, so the adapter
// is a projection, not a rewrite:
//   node: [id, kind, label, profiles[]]
//   edge: [id, source, target, profile, weight, edge_count, directed]

import { buildGraphModel } from "../../graph-explorer/model/parseRows";
import type { GraphModel } from "../../graph-explorer/model/parseRows";
import type { GipEdge, GipNode } from "./chartOptions";

/** Every citation is the same kind of relationship, so there is one profile.
 * graph-explorer coloUrs links by profile and nodes by kind — here the
 * lifecycle stage IS the kind, which makes its legend our stage legend. */
export const GIP_EDGE_PROFILE = "cites";

export function buildGipGraphModel(nodes: GipNode[], edges: GipEdge[]): GraphModel {
  const nodeRows = nodes.map((n) => [
    String(n.gip),
    // kind -> node colour + legend entry. Stage is the lifecycle, which is
    // exactly what a reader wants the colour to mean.
    n.stage,
    `GIP-${n.gip} ${n.label}`,
    [GIP_EDGE_PROFILE],
  ]);
  const edgeRows = edges.map((e) => [
    `${e.src}->${e.dst}`,
    String(e.src),
    String(e.dst),
    GIP_EDGE_PROFILE,
    e.weight,
    e.weight,
    // Directed: A citing B is not the same fact as B citing A, and 6 of the
    // pairs in this graph are reciprocal — collapsing direction would silently
    // merge two distinct citations into one.
    true,
  ]);
  return buildGraphModel(nodeRows, edgeRows, [GIP_EDGE_PROFILE], {});
}
