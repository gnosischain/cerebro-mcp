import { SegmentedControl } from "../../shared/SegmentedControl";
import { ZOOM_PRESETS, type AxisMode, type ZoomPreset } from "../model/liquidityProfile";
import type { ProfileView } from "../model/navGroups";
import type { PlxClientState } from "../urlState";

// Profile view controls. Every option is a pure client re-projection of the
// same tick axis (no reload): the axis label mode relabels, the zoom sets the
// window, the y scale toggles linear/log, the orientation flips prices.
// Only the view toggle (Snapshot / Over time) triggers a load — the heatmap
// group is on demand.

type YScale = "linear" | "log";

export interface ProfileControlsProps {
  client: PlxClientState;
  onChange: (patch: Partial<PlxClientState>) => void;
  yLog: boolean;
  onYLog: (next: boolean) => void;
  /** The over-time heatmap needs a probed CL pool. */
  overTimeAvailable: boolean;
}

export function ProfileControls({ client, onChange, yLog, onYLog, overTimeAvailable }: ProfileControlsProps) {
  return (
    <div className="plx-profile-controls">
      <label>
        View
        <SegmentedControl<ProfileView>
          size="sm"
          ariaLabel="Profile view"
          value={client.view}
          options={[
            { value: "snapshot", label: "Snapshot", ariaLabel: "The liquidity profile at one publication date" },
            { value: "over_time", label: "Over time", ariaLabel: overTimeAvailable ? "Active liquidity by tick across sampled publication dates (loads on demand)" : "Requires a probed concentrated-liquidity pool" },
          ]}
          onChange={(view) => {
            if (view === "over_time" && !overTimeAvailable) return;
            onChange({ view });
          }}
        />
      </label>
      <label>
        Axis
        <SegmentedControl<AxisMode>
          size="sm"
          ariaLabel="Axis labels"
          value={client.axis}
          options={[
            { value: "tick", label: "Tick", ariaLabel: "Raw tick index (price = 1.0001^tick)" },
            { value: "price", label: "Price", ariaLabel: "Price at each tick — adjusted when both decimals are known, raw otherwise" },
            { value: "pct", label: "% from current", ariaLabel: "Percent distance from the current price" },
          ]}
          onChange={(axis) => onChange({ axis })}
        />
      </label>
      {client.view === "snapshot" && (
        <label>
          Zoom
          <SegmentedControl<ZoomPreset>
            size="sm"
            ariaLabel="Zoom window around the current tick"
            value={client.zoom}
            options={ZOOM_PRESETS.map((preset) => ({
              value: preset.id,
              label: preset.label,
              ariaLabel: preset.ticks === null
                ? "Every inner range (full-range positions never set the window)"
                : `${preset.label} of price around the current tick (${preset.ticks} ticks)`,
            }))}
            onChange={(zoom) => onChange({ zoom })}
          />
        </label>
      )}
      {client.view === "snapshot" && (
        <label>
          Y
          <SegmentedControl<YScale>
            size="sm"
            ariaLabel="Liquidity axis scale"
            value={yLog ? "log" : "linear"}
            options={[
              { value: "linear", label: "Linear" },
              { value: "log", label: "Log", ariaLabel: "Log scale — reveals thin ranges beside a full-range floor" },
            ]}
            onChange={(scale) => onYLog(scale === "log")}
          />
        </label>
      )}
      <button
        type="button"
        className={`plx-flip${client.inverted ? " is-active" : ""}`}
        title="Flip the price orientation (token0 per token1) — a pure relabeling, no reload"
        onClick={() => onChange({ inverted: !client.inverted })}
      >
        ⇄ Flip price
      </button>
    </div>
  );
}
