import { useMemo, useState } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { SegmentedControl } from "../../shared/SegmentedControl";
import { finite, rowsToObjects } from "../../shared/rowDataset";
import { GipBadge } from "../components/GipBadge";
import {
  GIP_STAGE_COLOR,
  GIP_STAGE_ORDER,
  drawableEdges,
  gipDegrees,
  gipTimelineOption,
  type GipEdge,
  type GipNode,
} from "../model/chartOptions";
import { GraphCanvas } from "../../graph-explorer/canvas/GraphCanvas";
import { buildGipGraphModel } from "../model/gipGraphModel";
import { GroupGate, fmtNum, useDataset, type GovViewContext } from "./common";

// The GIP knowledge graph.
//
// Nodes are GIP NUMBERS, not people. A GIP number is the one identifier that
// spans both planes — the forum topic and the Snapshot proposal each carry it
// in the title — so it is what lets the two sources be joined at all. People
// were deliberately left out: 5,370 forum users would swamp any layout, and the
// structure worth seeing is between proposals.
//
// Edges are citations pulled out of post bodies, and the wording everywhere
// here is "cites" / "mentions" — never "depends on" or "supersedes". GIP-122's
// thread mentioning GIP-98 twenty-one times is evidence that the discussion
// referenced it. Turning that into a dependency claim would be inventing a
// relationship the data does not record.
//
// Timeline is the default view because the graph's measured shape says so:
// 90.4% of citations point backward in time, so the chronology IS the
// structure. The force view answers a different question ("what clumps") and
// stays one click away.

const SRC = "governance_db (forum titles + post bodies, Snapshot titles)";

type LayoutId = "timeline" | "clusters";

function fmtDate(value: string): string {
  return String(value ?? "").slice(0, 10);
}

export function GraphSection({ ctx }: { ctx: GovViewContext }) {
  const [focus, setFocus] = useState<number | null>(null);
  const [layout, setLayout] = useState<LayoutId>("timeline");
  const [hideIsolated, setHideIsolated] = useState(true);
  const [stages, setStages] = useState<string[]>([]);
  const [showSql, setShowSql] = useState(false);

  const nodesDs = useDataset(ctx, "graph_nodes");
  const edgesDs = useDataset(ctx, "graph_edges");

  const nodes = useMemo<GipNode[]>(
    () => rowsToObjects(nodesDs).flatMap((row) => {
      const gip = finite(row.gip);
      if (gip === null) return [];
      return [{
        gip,
        label: String(row.label ?? ""),
        stage: String(row.stage ?? "unstaged"),
        posts: finite(row.posts),
        participants: finite(row.participants),
        views: finite(row.views),
        votes: finite(row.votes),
        quorumStatus: String(row.quorum_status ?? ""),
        author: String(row.author ?? ""),
        proposalState: String(row.proposal_state ?? ""),
        firstSeen: String(row.first_seen ?? ""),
        lastActivity: String(row.last_activity ?? ""),
        topicId: finite(row.topic_id),
        proposalId: String(row.proposal_id ?? ""),
      }];
    }),
    [nodesDs],
  );

  const edges = useMemo<GipEdge[]>(
    () => rowsToObjects(edgesDs).flatMap((row) => {
      const src = finite(row.src_gip);
      const dst = finite(row.dst_gip);
      const weight = finite(row.weight);
      if (src === null || dst === null || weight === null) return [];
      return [{
        src, dst, weight,
        topics: finite(row.topics),
        firstMention: String(row.first_mention ?? ""),
        lastMention: String(row.last_mention ?? ""),
      }];
    }),
    [edgesDs],
  );

  const drawable = useMemo(() => drawableEdges(nodes, edges), [nodes, edges]);
  const degrees = useMemo(() => gipDegrees(drawable), [drawable]);
  const isolated = useMemo(
    () => nodes.filter((n) => !degrees.has(n.gip)).length,
    [nodes, degrees],
  );

  // No measured height. Both layouts are grid items in a flex chain that
  // reaches the fold, exactly as graph-explorer's canvas does — see
  // `.gov-content--flush` / `.gov-graph-layout`. The hook that used to live
  // here only existed to compensate for a cascade I had broken myself.
  const opts = useMemo(
    () => ({ focus, hideIsolated, stages }),
    [focus, hideIsolated, stages],
  );
  const spec = useMemo(() => gipTimelineOption(nodes, edges, opts), [nodes, edges, opts]);
  // The Clusters view runs on the Graph Explorer's WebGL canvas, which already
  // has fit-to-view, zoom, focus mode, labels, a legend, an error boundary and
  // a table fallback. Rebuilding those on a second ECharts force chart was the
  // wrong instinct.
  const canvasModel = useMemo(
    () => buildGipGraphModel(
      hideIsolated ? nodes.filter((n) => degrees.has(n.gip)) : nodes,
      drawable,
    ),
    [nodes, drawable, degrees, hideIsolated],
  );

  const byGip = useMemo(() => new Map(nodes.map((n) => [n.gip, n])), [nodes]);
  const selected = focus === null ? null : byGip.get(focus) ?? null;
  // Both directions, resolved to real nodes so every row is clickable.
  const cites = useMemo(
    () => (focus === null ? [] : drawable.filter((e) => e.src === focus)
      .sort((a, b) => b.weight - a.weight)),
    [drawable, focus],
  );
  const citedBy = useMemo(
    () => (focus === null ? [] : drawable.filter((e) => e.dst === focus)
      .sort((a, b) => b.weight - a.weight)),
    [drawable, focus],
  );

  /** Fallback content for the side panel: the GIPs the forum returns to most.
   * An empty aside beside a full-height chart reads as a broken layout. */
  const topCited = useMemo(() => {
    const rows = [...degrees.entries()]
      .filter(([, d]) => d.inbound > 0)
      .sort((a, b) => b[1].inbound - a[1].inbound)
      .slice(0, 12);
    return rows.map(([gip, d]) => ({ gip, weight: d.inbound, last: "" }));
  }, [degrees]);

  const toggleStage = (stage: string) =>
    setStages((current) => (current.includes(stage)
      ? current.filter((s) => s !== stage)
      : [...current, stage]));

  const stageCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of nodes) counts.set(n.stage, (counts.get(n.stage) ?? 0) + 1);
    return counts;
  }, [nodes]);

  return (
    <GroupGate ctx={ctx} section="graph" group="core">
      <div className="gov-toolbar gov-graph-bar">
        <span className="gov-graph-summary">
          <strong>{fmtNum(nodes.length)}</strong> GIPs ·{" "}
          <strong>{fmtNum(drawable.reduce((sum, e) => sum + e.weight, 0))}</strong> citations ·{" "}
          <strong>{fmtNum(nodes.length - isolated)}</strong> connected
        </span>
        <label>
          Layout
          <SegmentedControl<LayoutId>
            size="sm"
            ariaLabel="Graph layout"
            value={layout}
            options={[
              { value: "timeline", label: "Timeline" },
              { value: "clusters", label: "Clusters" },
            ]}
            onChange={setLayout}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={hideIsolated}
            onChange={(event) => setHideIsolated(event.target.checked)}
          />
          Hide the {isolated} isolated
        </label>
        <span className="gov-graph-legend">
          {GIP_STAGE_ORDER.map((stage) => {
            const active = stages.length === 0 || stages.includes(stage);
            return (
              <button
                key={stage}
                type="button"
                className={active ? "gov-graph-chip" : "gov-graph-chip gov-graph-chip--off"}
                onClick={() => toggleStage(stage)}
                title={`${stageCounts.get(stage) ?? 0} GIPs`}
              >
                <span className="gov-graph-dot" style={{ background: GIP_STAGE_COLOR(stage) }} />
                {stage}
                <span className="gov-graph-chip__n">{stageCounts.get(stage) ?? 0}</span>
              </button>
            );
          })}
        </span>
        {/* The chart is rendered flush — no card head or foot — so provenance
            lives here. It opens over the graph rather than above or below it. */}
        <button
          type="button"
          className="gov-graph-chip"
          aria-pressed={showSql}
          onClick={() => setShowSql((current) => !current)}
        >
          {showSql ? "Hide SQL" : "View SQL"}
        </button>
      </div>

      <div className="gov-graph-layout">
        {layout === "timeline" ? (
          /* No DatasetPanel, no carded ChartCard: between them they cost a
             section head, a chart head and a foot — ~178px on the one tab where
             the chart IS the view. The section-level GroupGate already covers
             loading and failure; the only state left to handle here is "the
             query returned nothing". */
          <div className="gov-graph-chart">
            {nodes.length === 0 ? (
              <div className="gov-empty">
                No GIP numbers found in forum or proposal titles.
              </div>
            ) : (
              <ChartCard
                chartId="gov-gip-timeline"
                hideId
                flush
                spec={spec}
                onEvents={{
                  click: (params: unknown) => {
                    const p = params as {
                      seriesType?: string;
                      name?: string;
                      data?: Record<string, unknown>;
                    };
                    // An arc pins the CITING GIP — the arc's own detail then
                    // shows up in that node's "Cites" list.
                    if (p.seriesType === "lines") {
                      const src = finite(p.data?.srcGip);
                      if (src !== null) setFocus(src);
                      return;
                    }
                    const gip = finite(p.data?.gip)
                      ?? finite(String(p.name ?? "").replace(/^GIP-/, ""));
                    if (gip === null) return;
                    setFocus((current) => (current === gip ? null : gip));
                  },
                }}
              />
            )}
            {showSql && <GraphSql ctx={ctx} onClose={() => setShowSql(false)} />}
          </div>
        ) : (
          <div className="ge-canvas gov-graph-canvas">
            <GraphCanvas
              model={canvasModel}
              stateKey="governance:gip-citations"
              selectedNodeId={focus === null ? "" : String(focus)}
              emptyHint="No GIP cites another — nothing to lay out."
              onSelectNode={(id) => setFocus(finite(id))}
              onSelectEdge={(id) => {
                // Edge ids are "<src>-><dst>"; pin the citing GIP.
                const src = finite(String(id).split("->")[0]);
                if (src !== null) setFocus(src);
              }}
              onExpandNode={() => undefined}
              onViewClick={() => setFocus(null)}
              fallbackNodeActionLabel="Inspect GIP"
              // The toolbar above already carries every stage swatch WITH its
              // count, so the canvas's own NODE KINDS strip was the same legend
              // twice — and it cost ~85px of graph. Still one click away.
              legendDefaultOpen={false}
              // No force sliders. They are tuned for graph-explorer's
              // hundred-thousand-node transaction graphs; at 149 GIPs the
              // layout settles instantly and the four sliders are noise — and
              // at this canvas width they sit two disclosures deep (Advanced ->
              // ⚙ Forces), which is worse than not offering them.
              showSimControls={false}
              // No `stats`: CanvasStatsData wants hopsUsed / maxHops /
              // activeProfileCount / catalogSize, which are graph-explorer
              // traversal concepts with no meaning for a citation graph. The
              // counts already sit in the summary line above.
            />
            {showSql && <GraphSql ctx={ctx} onClose={() => setShowSql(false)} />}
          </div>
        )}

        {/* Beside the chart, not below it. A pinned GIP's detail used to render
            under a full-height canvas, so reading it meant scrolling the graph
            you were reading it ABOUT off the screen. */}
        <aside className="gov-graph-side">
          {selected ? (
            <>
              <h3 className="gov-graph-side__head">
                <GipBadge gip={selected.gip} />
                <button type="button" className="gov-graph-side__clear" onClick={() => setFocus(null)}>
                  clear
                </button>
              </h3>
              <p className="gov-graph-focus">{selected.label}</p>

              <dl className="gov-graph-facts">
                <div><dt>Stage</dt><dd>{selected.stage}</dd></div>
                <div><dt>Cited by</dt><dd>{fmtNum(citedBy.length)}</dd></div>
                <div><dt>Cites</dt><dd>{fmtNum(cites.length)}</dd></div>
                <div><dt>Forum posts</dt><dd>{fmtNum(selected.posts)}</dd></div>
                <div><dt>Participants</dt><dd>{fmtNum(selected.participants)}</dd></div>
                <div>
                  <dt>Votes</dt>
                  <dd>{selected.proposalId ? fmtNum(selected.votes) : "—"}</dd>
                </div>
              </dl>
              <p className="gov-caption">
                First seen {fmtDate(selected.firstSeen)} · last activity{" "}
                {fmtDate(selected.lastActivity)}
                {selected.quorumStatus ? ` · quorum ${selected.quorumStatus}` : ""}
              </p>

              <div className="gov-graph-actions">
                {selected.topicId !== null && selected.topicId > 0 && (
                  <button type="button" onClick={() => ctx.onEntity("forum_topic", String(selected.topicId))}>
                    Forum topic
                  </button>
                )}
                {selected.proposalId !== "" && (
                  <button type="button" onClick={() => ctx.onEntity("proposal", selected.proposalId)}>
                    Snapshot proposal
                  </button>
                )}
              </div>

              {selected.proposalId === "" && (
                <p className="gov-caption">
                  No Snapshot proposal carries this GIP number — discussed but never put to a
                  vote, or the vote predates what is indexed here.
                </p>
              )}

              <CitationList
                title={`Cited by (${citedBy.length})`}
                empty="Nothing cites this GIP."
                rows={citedBy.map((e) => ({ gip: e.src, weight: e.weight, last: e.lastMention }))}
                byGip={byGip}
                onPick={setFocus}
              />
              <CitationList
                title={`Cites (${cites.length})`}
                empty="This GIP's thread cites no other GIP."
                rows={cites.map((e) => ({ gip: e.dst, weight: e.weight, last: e.lastMention }))}
                byGip={byGip}
                onPick={setFocus}
              />
            </>
          ) : (
            <>
              <h3 className="gov-graph-side__head">Most cited</h3>
              <HowToRead layout={layout} />
              <p className="gov-caption">
                Click a node or an arc to inspect it. These are the GIPs the forum returns to
                most — citations received, which is the y-axis.
              </p>
              <CitationList
                title=""
                empty="No citations recorded."
                rows={topCited}
                byGip={byGip}
                onPick={setFocus}
              />
            </>
          )}
        </aside>
      </div>

    </GroupGate>
  );
}

/** Provenance for both layouts, drawn OVER the graph. The nodes and the edges
 * come from two different queries, so both are shown — reading one without the
 * other would misrepresent where an arc came from. */
function GraphSql({ ctx, onClose }: { ctx: GovViewContext; onClose: () => void }) {
  const parts = [
    ["Nodes", ctx.descriptors.graph_nodes?.sql],
    ["Edges", ctx.descriptors.graph_edges?.sql],
  ].filter(([, sql]) => Boolean(sql)) as Array<[string, string]>;
  return (
    <div className="gov-graph-sql">
      <div className="gov-graph-sql__head">
        <span>{SRC}</span>
        <button type="button" onClick={onClose}>close</button>
      </div>
      {parts.length === 0 ? (
        <p>SQL not available for this view.</p>
      ) : (
        parts.map(([label, sql]) => (
          <div key={label}>
            <h4>{label}</h4>
            <pre><code>{sql}</code></pre>
          </div>
        ))
      )}
    </div>
  );
}

/** What the marks mean. Lives in the side panel rather than under the chart:
 * as a caption it was three lines of prose between the graph and the fold, and
 * the graph needed that height more than the prose did. */
function HowToRead({ layout }: { layout: LayoutId }) {
  return (
    <p className="gov-caption">
      An edge means one GIP&apos;s forum thread <strong>mentioned</strong> another&apos;s number
      — evidence the discussion referenced it, <strong>not</strong> that one depends on or
      supersedes the other. Node size is forum posts.
      {layout === "timeline" ? (
        <> The x-axis is each GIP&apos;s first-seen date, not its number: GIP numbers run only
        89% in date order. Arcs curve up when a newer GIP cites an older one — 141 of 156 — and
        down in <span className="gov-graph-anomaly">amber for the 15 pointing forward</span>,
        which happens when a thread was edited after the fact.</>
      ) : (
        <> No chronology here — this answers &ldquo;what clumps together&rdquo;, which the
        timeline cannot show.</>
      )}{" "}
      Plain scroll moves the page; <kbd>ctrl</kbd>+scroll zooms, drag pans.
    </p>
  );
}

function CitationList({ title, empty, rows, byGip, onPick }: {
  title: string;
  empty: string;
  rows: Array<{ gip: number; weight: number; last: string }>;
  byGip: Map<number, GipNode>;
  onPick: (gip: number) => void;
}) {
  return (
    <div className="gov-cites">
      {title && <h4 className="gov-cites__head">{title}</h4>}
      {rows.length === 0 ? (
        <p className="gov-caption">{empty}</p>
      ) : (
        <ul className="gov-cites__list">
          {rows.map((row) => {
            const other = byGip.get(row.gip);
            return (
              <li key={row.gip}>
                <button type="button" onClick={() => onPick(row.gip)} title={other?.label ?? ""}>
                  <GipBadge gip={row.gip} />
                  <span className="gov-cites__title">{other?.label ?? `GIP-${row.gip}`}</span>
                  <span className="gov-cites__n">
                    {row.weight}&times;{row.last ? ` · ${fmtDate(row.last)}` : ""}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
