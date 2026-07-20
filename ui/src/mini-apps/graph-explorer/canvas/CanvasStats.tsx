// Canvas-corner stats chip. COMPACT by default and opt-out: click it to
// collapse to a tiny "⊞" pill so it never sits in the way, click that to bring
// it back. Every segment keeps a tooltip explaining what the number means.

export interface CanvasStatsData {
  /** Nodes currently on canvas (model truth, post status-filter). */
  nodeCount: number;
  /** Edges currently on canvas (model truth). */
  edgeCount: number;
  hopsUsed: number;
  maxHops: number;
  /** Active edge types after the status filter. */
  activeProfileCount: number;
  catalogSize: number;
}

interface Props {
  stats: CanvasStatsData;
  open: boolean;
  onToggleOpen: () => void;
}

export function CanvasStats({ stats, open, onToggleOpen }: Props) {
  if (!open) {
    return (
      <button
        type="button"
        className="ge-canvas-stats collapsed"
        onClick={onToggleOpen}
        title="Show graph stats"
        aria-label="Show graph stats"
      >
        ⊞
      </button>
    );
  }
  return (
    <button
      type="button"
      className="ge-canvas-stats"
      onClick={onToggleOpen}
      title="Hide graph stats"
      aria-label="Graph statistics — click to hide"
    >
      <span title="Nodes currently on the canvas">
        <b>{stats.nodeCount.toLocaleString()}</b>n
      </span>
      <span title="Edges currently on the canvas">
        <b>{stats.edgeCount.toLocaleString()}</b>e
      </span>
      <span
        title={`Hops used out of the cap (${stats.maxHops}). Each expand advances the frontier by one hop.`}
      >
        h<b>{stats.hopsUsed}</b>/{stats.maxHops}
      </span>
      <span title="Active relationship/token types out of all available">
        <b>{stats.activeProfileCount}</b>/{stats.catalogSize}
      </span>
    </button>
  );
}
