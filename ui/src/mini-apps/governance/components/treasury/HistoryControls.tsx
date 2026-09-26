import { SegmentedControl } from "../../../shared/SegmentedControl";
import { measureAllowed, type HistoryRange, type Measure, type StackMode } from "../../model/treasuryHistory";
import type { TreasuryViewState } from "../../state/treasuryView";

// History controls: what to stack by, in which unit, over which window. The
// range slices the data; the chart itself only zooms with the wheel/pinch
// (inside zoom) — never a slider bar.

const STACKS: Array<{ value: StackMode; label: string }> = [
  { value: "asset", label: "Asset" },
  { value: "chain", label: "Chain" },
  { value: "wallet", label: "Wallet" },
  { value: "class", label: "Class" },
];

const RANGES: Array<{ value: HistoryRange; label: string }> = [
  { value: "1y", label: "1Y" },
  { value: "3y", label: "3Y" },
  { value: "all", label: "All" },
];

export function HistoryControls({
  view,
  onChange,
}: {
  view: Pick<TreasuryViewState, "stackBy" | "measure" | "range">;
  onChange: (patch: Partial<TreasuryViewState>) => void;
}) {
  const gnoAllowed = measureAllowed(view.stackBy, "gno");
  return (
    <div className="gov-trs-controls" role="group" aria-label="History controls">
      <div className="gov-trs-controls__field">
        <span>Stack by</span>
        <SegmentedControl<StackMode>
          size="sm"
          ariaLabel="Stack by"
          value={view.stackBy}
          options={STACKS}
          onChange={(stackBy) => onChange({ stackBy })}
        />
      </div>
      <div className="gov-trs-controls__field">
        <span>Measure</span>
        <SegmentedControl<Measure>
          size="sm"
          ariaLabel="Measure"
          value={view.measure}
          options={[
            { value: "usd", label: "USD" },
            {
              value: "gno",
              label: "GNO units",
              ariaLabel: gnoAllowed
                ? "GNO units held"
                : "GNO units stack by chain or wallet only — picking it switches the stack to Chain",
            },
          ]}
          onChange={(measure) => onChange({ measure })}
        />
      </div>
      <div className="gov-trs-controls__field">
        <span>Range</span>
        <SegmentedControl<HistoryRange>
          size="sm"
          ariaLabel="Range"
          value={view.range}
          options={RANGES}
          onChange={(range) => onChange({ range })}
        />
      </div>
    </div>
  );
}
