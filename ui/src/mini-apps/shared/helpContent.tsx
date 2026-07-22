import type { HelpContent } from "./HelpDialog";

export const COW_EXPLORER_HELP: HelpContent = {
  title: "CoW Data Explorer",
  intro: "Explore the CoW activity currently indexed in cow_db, with source-specific coverage and freshness shown beside every result.",
  sections: [
    {
      heading: "Prices and markets",
      body: (
        <p>
          <strong>Execution prices</strong> come only from settled fills. Auction
          price-vector ratios and native-price API observations are separate
          reference series; they are never blended into the execution candles.
        </p>
      ),
    },
    {
      heading: "Known intents",
      body: (
        <p>
          The Orders section is an <strong>observed snapshot</strong> of indexed
          open intents. It is not a live or complete orderbook. The snapshot
          observation time and incomplete-source warning remain visible.
        </p>
      ),
    },
    {
      heading: "Coverage",
      body: (
        <p>
          Historical backfill differs by chain and source. Read the indexed
          first/last timestamps, source observation time, checkpoint, row cap,
          and warning codes shown with each chart and table before comparing
          periods or networks.
        </p>
      ),
    },
    {
      heading: "Solver roles",
      body: (
        <p>
          Competition solvers and settlement executors are distinct roles.
          The flow diagram links token pairs to observed settlement executors
          by settled fill count and does not infer frontend, builder, OFA, PMM,
          or liquidity-source attribution.
        </p>
      ),
    },
  ],
};

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
          <li>
            <strong>Timeline:</strong> switch to the Timeline mode to play the
            current subgraph's interactions across time — a sliding window
            scrubs over day/week/month buckets; flows pulse with volume,
            ownership/trust edges appear when they began, and snapshot
            relationships show as dim always-on context (toggleable).
          </li>
          <li>
            <strong>Flows:</strong> forensic follow-the-money. Seed one or more
            addresses and trace value <em>out</em> (where funds went),
            <em> in</em> (who funded them), or both, hop by hop. The layout is
            left-to-right: upstream funders on the left, seeds in the middle,
            downstream recipients on the right. Edges carry token, USD amount,
            and transfer count; nodes show hop rank, in/out USD, and sector
            attribution (bridges, DEX, mixers, payments). Click an edge for
            transaction-level evidence (tx hash + timestamp); use
            <strong> Trace in/out</strong> on a node to extend the graph another
            hop — that also pushes through a DEX/bridge/mixer that a plain trace
            stops at.
          </li>
        </ul>
      ),
    },
    {
      heading: "Flows: coverage & caveats",
      body: (
        <ul>
          <li>
            <strong>Whitelisted tokens only:</strong> flows are built from
            whitelisted ERC-20 + WxDAI transfers with a USD price. A leg moved
            in a non-whitelisted token is invisible — a chain that goes dark may
            have hopped through one.
          </li>
          <li>
            <strong>Bridges are exits, not payouts:</strong> bridge edges are
            deposits INTO a bridge (funds leaving Gnosis Chain). The tool never
            fabricates a bridge→user payout — the destination chain is out of
            scope.
          </li>
          <li>
            <strong>Terminal sectors:</strong> DEX, bridges, and privacy/mixer
            nodes are attributed but not auto-expanded (their outflows aren't
            attributable). Payments (e.g. Gnosis Pay) stays walkable. Trace
            in/out overrides this when you want to push through.
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
    "Explore every dbt model in the database under its exact name — load one, chart its columns, and correlate them.",
  sections: [
    {
      heading: "What you're looking at",
      body: (
        <p>
          Every dbt model and source, by its real database name. Layer pills
          show where it sits in the pipeline (<strong>api</strong> = final
          metrics models, <strong>fct</strong> / <strong>int</strong> /{" "}
          <strong>stg</strong> = upstream, <strong>source</strong> = raw
          tables); <em>time series</em> vs <em>snapshot</em> tells you whether
          it has a date column.
        </p>
      ),
    },
    {
      heading: "How to drive it",
      body: (
        <ul>
          <li>
            <strong>Browse or search</strong> — pick a sector, filter by layer
            or dbt tag, and open <strong>Details</strong> for the full
            description, column schema, and qualified relation name.
          </li>
          <li>
            <strong>Load</strong> a model (nothing queries until you press
            Run). <strong>Aggregate</strong> mode runs{" "}
            <code>agg(Y) GROUP BY X</code> in ClickHouse — the correct way to
            chart big per-entity tables (balances per avatar, transfers per
            address); <strong>Raw rows</strong> samples the newest rows for
            inspection. Use <strong>Window</strong> to bound heavy views.
          </li>
          <li>
            <strong>Chart</strong>: pick type and X/Y columns, a{" "}
            <strong>Y2</strong> for a second axis, or a{" "}
            <strong>Series</strong> column for multi-series and heatmaps.
            Scatter supports a <strong>Color</strong> column for a third value.
          </li>
          <li>
            <strong>Correlate</strong>: the Analysis section below the chart
            has summary stats and the Pearson/Spearman matrix over all numeric
            columns — click any cell to drill into that pair with a fitted
            line. Add a second model to compare two tables on twin axes.
          </li>
        </ul>
      ),
    },
  ],
};
