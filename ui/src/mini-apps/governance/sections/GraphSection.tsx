import { useCallback, useMemo, useRef, useState } from "react";

import { ChartCard } from "../../../components/ChartCard";
import { SegmentedControl } from "../../shared/SegmentedControl";
import { finite, rowsToObjects } from "../../shared/rowDataset";
import { DatasetPanel } from "../components/DatasetPanel";
import { GipBadge } from "../components/GipBadge";
import {
  GIP_STAGE_COLOR,
  GIP_STAGE_ORDER,
  drawableEdges,
  gipDegrees,
  gipGraphOption,
  gipTimelineOption,
  type GipEdge,
  type GipNode,
} from "../model/chartOptions";
import { GroupGate, KpiRow, fmtNum, useDataset, type GovViewContext } from "./common";

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

/** The slice of the ECharts instance this section touches. */
interface EChartsLike {
  getZr?: () => {
    on: (event: string, handler: (e: { offsetX: number; offsetY: number }) => void) => void;
  } | null;
  convertToPixel?: (finder: unknown, value: unknown) => unknown;
}

type LayoutId = "timeline" | "clusters";

function fmtDate(value: string): string {
  return String(value ?? "").slice(0, 10);
}

export function GraphSection({ ctx }: { ctx: GovViewContext }) {
  const groups = ctx.state.loaded_groups ?? {};
  const retry = () => ctx.retryGroup("graph", "core");
  const [focus, setFocus] = useState<number | null>(null);
  const [layout, setLayout] = useState<LayoutId>("timeline");
  const [hideIsolated, setHideIsolated] = useState(true);
  const [stages, setStages] = useState<string[]>([]);

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

  const opts = useMemo(
    () => ({ focus, hideIsolated, stages }),
    [focus, hideIsolated, stages],
  );
  const spec = useMemo(
    () => (layout === "timeline"
      ? gipTimelineOption(nodes, edges, opts)
      : gipGraphOption(nodes, edges, opts)),
    [layout, nodes, edges, opts],
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

  const toggleStage = (stage: string) =>
    setStages((current) => (current.includes(stage)
      ? current.filter((s) => s !== stage)
      : [...current, stage]));

  // ECharts does not emit a series `click` for a graph on a CARTESIAN
  // coordinate system, so the handler wired through onEvents never fired in the
  // timeline view and every node looked inert. ChartCard exposes the instance
  // for exactly this case: hit-test in pixel space off a zrender click, which
  // works in both layouts and does not depend on the series emitting anything.
  const hitRef = useRef<GipNode[]>(nodes);
  hitRef.current = nodes;
  const degreeRef = useRef(degrees);
  degreeRef.current = degrees;
  // The hit-test only applies to the cartesian layout. In the force view there
  // are no axes, so convertToPixel returns null for every node — and an
  // unguarded handler would then treat EVERY click as "hit nothing" and clear
  // the focus that the series click had just set, one frame earlier. That is
  // what made nodes look inert after the refactor.
  const layoutRef = useRef<LayoutId>(layout);
  layoutRef.current = layout;

  const onChartReady = useCallback((instance: unknown) => {
    const chart = instance as EChartsLike;
    const zr = chart.getZr?.();
    if (!zr) return;
    zr.on("click", (event) => {
      if (layoutRef.current !== "timeline") return;
      let best: { gip: number; d2: number } | null = null;
      let converted = 0;
      for (const n of hitRef.current) {
        let px: unknown = null;
        try {
          // Finder is the AXIS PAIR, not the series: a graph series on a
          // cartesian system does not register as a convertible coordinate
          // system, so { seriesIndex: 0 } returns null for every node.
          px = chart.convertToPixel?.({ xAxisIndex: 0, yAxisIndex: 0 }, [
            n.firstSeen.replace(" ", "T"),
            degreeRef.current.get(n.gip)?.inbound ?? 0,
          ]);
        } catch {
          px = null;
        }
        if (!Array.isArray(px) || !Number.isFinite(px[0]) || !Number.isFinite(px[1])) continue;
        converted += 1;
        const d2 = (Number(px[0]) - event.offsetX) ** 2 + (Number(px[1]) - event.offsetY) ** 2;
        if (!best || d2 < best.d2) best = { gip: n.gip, d2 };
      }
      // Nothing convertible means the coordinate system is not ready, not that
      // the reader clicked empty space — leave the pin alone.
      if (converted === 0) return;
      // ~20px: generous enough for the smallest node, tight enough that a click
      // on empty canvas clears the pin instead of snapping to something distant.
      if (best && best.d2 <= 20 * 20) {
        const hit = best.gip;
        setFocus((current) => (current === hit ? null : hit));
      } else {
        setFocus(null);
      }
    });
  }, []);

  const stageCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of nodes) counts.set(n.stage, (counts.get(n.stage) ?? 0) + 1);
    return counts;
  }, [nodes]);

  return (
    <GroupGate ctx={ctx} section="graph" group="core">
      <KpiRow
        items={[
          { label: "GIPs", value: fmtNum(nodes.length) },
          { label: "Reached a vote", value: fmtNum(nodes.filter((n) => n.stage === "voted").length) },
          { label: "Citations", value: fmtNum(drawable.reduce((sum, e) => sum + e.weight, 0)) },
          { label: "In the citation web", value: fmtNum(nodes.length - isolated) },
        ]}
        meta={`${isolated} GIPs neither cite nor are cited — isolated, not missing`}
      />

      <div className="gov-toolbar">
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
      </div>

      <DatasetPanel
        title={layout === "timeline" ? "GIP citations over time" : "GIP citation clusters"}
        descriptor={ctx.descriptors.graph_nodes}
        groupLoaded={groups["graph.core"]}
        onRetry={retry}
        emptyLabel="No GIP numbers found in forum or proposal titles."
      >
        <ChartCard
          chartId={`gov-gip-${layout}`}
          hideId
          sql={ctx.descriptors.graph_edges?.sql}
          sourceModel={SRC}
          spec={spec}
          onChartReady={onChartReady}
          onEvents={{
            click: (params: unknown) => {
              const p = params as {
                dataType?: string;
                name?: string;
                data?: Record<string, unknown>;
              };
              if (p.dataType === "edge") {
                // Clicking an arc used to do nothing at all. Pin the CITING
                // GIP — the arc's own detail then appears in that node's
                // "Cites" list, which is where an edge's story actually lives.
                const src = finite((p.data as { srcGip?: unknown })?.srcGip);
                if (src !== null) setFocus(src);
                return;
              }
              // `params.name` — not `params.data.gip`. A graph series on a
              // cartesian coordinate system reshapes each item, so `data` is
              // not the object we handed in and reading `.gip` off it came back
              // undefined for every click. `name` is always the series item's
              // name, which is "GIP-<n>" by construction.
              const gip = finite(p.data?.gip)
                ?? finite(String(p.name ?? "").replace(/^GIP-/, ""));
              if (gip === null) return;
              setFocus((current) => (current === gip ? null : gip));
            },
          }}
        />
        <p className="gov-caption">
          An edge means one GIP&apos;s forum thread <strong>mentioned</strong> another&apos;s
          number — evidence that the discussion referenced it, <strong>not</strong> that one
          depends on or supersedes the other. Node size is citations <em>received</em>.
          {layout === "timeline" ? (
            <> The x-axis is each GIP&apos;s first-seen date, not its number: GIP numbers run only
            89% in date order. Arcs curve up when a newer GIP cites an older one — the normal
            case, 141 of 156 — and curve down in amber for the {" "}
            <span className="gov-graph-anomaly">15 that point forward</span>, which happens when a
            thread was edited after the fact.</>
          ) : (
            <> No chronology in this view — it answers &ldquo;what clumps together&rdquo;, which
            the timeline cannot show. Scroll to zoom, drag to pan.</>
          )}{" "}
          Click a node to pin it, or an arc to pin the GIP that made the citation.
          Plain scroll moves the page; hold <kbd>ctrl</kbd> and scroll to zoom the chart,
          and drag to pan.
        </p>
      </DatasetPanel>

      {selected && (
        <DatasetPanel title={`GIP-${selected.gip}`} descriptor={ctx.descriptors.graph_nodes} groupLoaded>
          <p className="gov-graph-focus">{selected.label}</p>
          <KpiRow
            items={[
              { label: "Stage", value: selected.stage },
              { label: "Cited by", value: fmtNum(citedBy.length) },
              { label: "Cites", value: fmtNum(cites.length) },
              { label: "Forum posts", value: fmtNum(selected.posts) },
              { label: "Participants", value: fmtNum(selected.participants) },
              {
                label: "Votes",
                value: selected.proposalId ? fmtNum(selected.votes) : "—",
              },
            ]}
            meta={`First seen ${fmtDate(selected.firstSeen)} · last activity ${
              fmtDate(selected.lastActivity)
            }${selected.quorumStatus ? ` · quorum ${selected.quorumStatus}` : ""}`}
          />

          <div className="gov-graph-actions">
            {selected.topicId !== null && selected.topicId > 0 && (
              <button type="button" onClick={() => ctx.onEntity("forum_topic", String(selected.topicId))}>
                Open forum topic
              </button>
            )}
            {selected.proposalId !== "" && (
              <button type="button" onClick={() => ctx.onEntity("proposal", selected.proposalId)}>
                Open Snapshot proposal
              </button>
            )}
            <button type="button" onClick={() => setFocus(null)}>Clear</button>
          </div>

          {selected.proposalId === "" && (
            <p className="gov-caption">
              No Snapshot proposal carries this GIP number — it was discussed but never put to a
              vote, or the vote predates what is indexed here.
            </p>
          )}

          <div className="gov-grid-2">
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
          </div>
        </DatasetPanel>
      )}
    </GroupGate>
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
      <h4 className="gov-cites__head">{title}</h4>
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
                    {row.weight}&times; · {fmtDate(row.last)}
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
