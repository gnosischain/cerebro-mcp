import type { HelpContent } from "./HelpDialog";

/**
 * Per-app usage guides shown by the ⓘ help button in each mini-app's nav bar.
 * Copy is intentionally concrete: what the view shows, what the symbols mean,
 * and how to drive the controls.
 */

export const GRAPH_EXPLORER_HELP: HelpContent = {
  title: "Graph Explorer",
  intro:
    "An interactive force-graph of how on-chain addresses and dbt entities relate across sectors — start from an address or a curated profile and expand outward hop by hop.",
  sections: [
    {
      heading: "What you're looking at",
      body: (
        <>
          <p>
            Each <strong>node</strong> is an entity (a contract, token, pool,
            protocol, or account). Each <strong>edge</strong> is an observed
            relationship — a transfer route, ownership link, or protocol
            membership. Edge thickness/weight reflects how strong or frequent
            that relationship is.
          </p>
          <p>
            The graph is <strong>time-aware</strong>: it is built from activity
            inside the selected window, so a quiet period shows fewer edges.
            Gaps in coverage are called out in the status bar.
          </p>
        </>
      ),
    },
    {
      heading: "Node kinds & status",
      body: (
        <>
          <p>
            Nodes are colour-tagged by <strong>profile / sector</strong>
            (bridges, DEX, lending, Circles, Gnosis Pay, …). The status dot
            marks each as <strong>approved</strong> (green — a curated, verified
            entity) or <strong>candidate</strong> (yellow — discovered but not
            yet vetted).
          </p>
        </>
      ),
    },
    {
      heading: "How to drive it",
      body: (
        <ul>
          <li>
            <strong>Seed:</strong> paste an address into “Start from an address”,
            or pick a profile row from the catalog.
          </li>
          <li>
            <strong>Expand:</strong> click a node to focus it; the panel shows
            its details and suggested next hops.
          </li>
          <li>
            <strong>Filter:</strong> use the chip strip to toggle sectors, and
            the segmented toggles for layout and approved/candidate status.
          </li>
          <li>
            <strong>Window / max-per-hop:</strong> the numeric pills bound the
            time range and how many neighbours each expansion pulls in.
          </li>
        </ul>
      ),
    },
  ],
};

export const MODEL_LINEAGE_HELP: HelpContent = {
  title: "Model Lineage",
  intro:
    "A left-to-right DAG of dbt model dependencies — trace how a model is built from its upstream sources and what depends on it downstream.",
  sections: [
    {
      heading: "What you're looking at",
      body: (
        <>
          <p>
            Each <strong>card</strong> is a dbt model or source. Arrows point in
            the direction data flows (upstream → downstream). The
            <strong> seed</strong> model you opened is highlighted, and its
            connected neighbourhood is emphasised while the rest dims.
          </p>
          <p>
            The badge on each card is its <strong>materialization</strong>
            (table, incremental, view, ephemeral, source).
          </p>
        </>
      ),
    },
    {
      heading: "How to drive it",
      body: (
        <ul>
          <li>
            <strong>Drag</strong> any card to rearrange the layout; positions
            persist until the graph is rebuilt.
          </li>
          <li>
            <strong>Click</strong> a card to select it and load its details;
            <strong> double-click</strong> to expand its lineage further.
          </li>
          <li>
            Use the <strong>direction</strong> and <strong>layer</strong>
            toggles plus the depth slider to scope how much of the graph loads.
          </li>
          <li>
            <strong>Column trace</strong> (details panel) follows a single
            column through the lineage.
          </li>
        </ul>
      ),
    },
  ],
};

export const PORTFOLIO_HELP: HelpContent = {
  title: "Portfolio",
  intro:
    "A cross-domain view of a single Gnosis Chain address — token holdings, relationships, and per-product activity (Yields, Gnosis Pay, Circles, Safe).",
  sections: [
    {
      heading: "What you're looking at",
      body: (
        <>
          <p>
            Enter an address to load its <strong>overview</strong> (balances and
            key metrics) plus <strong>relationships</strong> to other addresses
            and contracts.
          </p>
          <p>
            Domain sections only appear when the address is{" "}
            <strong>active in that product</strong>. If an address has no Gnosis
            Pay or Circles presence, that section is hidden rather than shown
            empty.
          </p>
        </>
      ),
    },
    {
      heading: "How to drive it",
      body: (
        <ul>
          <li>
            <strong>Load:</strong> paste an address and press Load (or Enter).
          </li>
          <li>
            <strong>Navigate:</strong> click a related address to pivot the view
            onto it.
          </li>
          <li>
            Tables scroll horizontally on narrow screens; long
            addresses/hashes are truncated — hover or copy for the full value.
          </li>
        </ul>
      ),
    },
    {
      heading: "Freshness",
      body: (
        <p>
          Snapshots come from curated dbt models; where available, a best-effort{" "}
          <strong>same-day overlay</strong> tops up recent Gnosis Pay and Safe
          owner activity. USD values may lag a day while daily prices settle.
        </p>
      ),
    },
  ],
};

export const CONTRACT_EXPLORER_HELP: HelpContent = {
  title: "Contract Explorer",
  intro:
    "Inspect and call any verified contract on Gnosis Chain — browse its ABI, read state, and simulate calls at a chosen block.",
  sections: [
    {
      heading: "What you're looking at",
      body: (
        <>
          <p>
            Load a contract address to list its <strong>functions</strong>. Each
            card shows the signature, mutability (read/write), and outputs. A
            badge marks whether the source/ABI was resolved.
          </p>
        </>
      ),
    },
    {
      heading: "How to drive it",
      body: (
        <ul>
          <li>
            <strong>Address bar:</strong> paste a contract, optionally pick a
            target/implementation, and load.
          </li>
          <li>
            <strong>Block:</strong> set a default block (or a preset like
            “latest”) to read historical state; individual cards can override
            it.
          </li>
          <li>
            <strong>Call:</strong> fill any inputs and run a read — the result
            and the block it was read at are shown inline, and added to history.
          </li>
        </ul>
      ),
    },
  ],
};

export const METRIC_LAB_HELP: HelpContent = {
  title: "Metric Lab",
  intro:
    "Build and compare metrics over the semantic layer — pick a metric, slice by dimensions, and chart it without writing SQL.",
  sections: [
    {
      heading: "What you're looking at",
      body: (
        <p>
          A metric is a curated, governed measure (volume, TVL, active users,
          revenue, …). The KPI row summarises the current selection; the chart
          shows it over time or broken down by a dimension.
        </p>
      ),
    },
    {
      heading: "How to drive it",
      body: (
        <ul>
          <li>
            <strong>Pick a metric</strong> (or open from SQL / from existing
            metrics) to seed the lab.
          </li>
          <li>
            <strong>Slice</strong> by a dimension to compare series, and adjust
            the time grain/range.
          </li>
          <li>
            <strong>Update chart</strong> re-runs the query; results reflect the
            governed metric definition, not ad-hoc SQL.
          </li>
        </ul>
      ),
    },
  ],
};
