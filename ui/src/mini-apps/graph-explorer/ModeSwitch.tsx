// Atlas | Investigate segmented control. The parent wires onChange to BOTH
// dispatch SET_MODE (immediate local flip; clears selection) and
// update_graph_explorer_focus({mode}) (server clears selection + persists).

import { SegmentedControl } from "../shared/SegmentedControl";
import type { GraphMode } from "./types";

interface Props {
  mode: GraphMode;
  onChange: (mode: GraphMode) => void;
}

/** The mode switch is APP CHROME, not mode content. Each view renders it via
 * its `modeSwitch` prop, and views disagreed about where — Transactions put it
 * first, Timeline/Flows last — so the tab bar jumped as you changed modes.
 * The `ge-mode-switch` wrapper lets CSS pin it to the far right of whatever
 * bar it lands in (flex `order`), so its position is a property of the shell
 * rather than a decision each new mode has to remember to get right. */
export function ModeSwitch({ mode, onChange }: Props) {
  return (
    <span className="ge-mode-switch">
    <SegmentedControl<GraphMode>
      options={[
        { value: "atlas", label: "Atlas", ariaLabel: "Atlas — browse profile samples" },
        {
          value: "investigate",
          label: "Investigate",
          ariaLabel: "Investigate — explore around a seed address",
        },
        {
          value: "timeline",
          label: "Timeline",
          ariaLabel: "Timeline — play the subgraph's interactions across time",
        },
        {
          value: "flows",
          label: "Flows",
          ariaLabel: "Flows — trace fund movements hop by hop from seed addresses",
        },
        {
          value: "transactions",
          label: "Transactions",
          ariaLabel:
            "Transactions — open whole transactions and read every transfer leg in chain order",
        },
      ]}
      value={mode}
      onChange={onChange}
      ariaLabel="Explorer mode"
      size="sm"
    />
    </span>
  );
}
