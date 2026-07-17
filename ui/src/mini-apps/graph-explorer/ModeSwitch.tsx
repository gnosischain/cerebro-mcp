// Atlas | Investigate segmented control. The parent wires onChange to BOTH
// dispatch SET_MODE (immediate local flip; clears selection) and
// update_graph_explorer_focus({mode}) (server clears selection + persists).

import { SegmentedControl } from "../shared/SegmentedControl";
import type { GraphMode } from "./types";

interface Props {
  mode: GraphMode;
  onChange: (mode: GraphMode) => void;
}

export function ModeSwitch({ mode, onChange }: Props) {
  return (
    <SegmentedControl<GraphMode>
      options={[
        { value: "atlas", label: "Atlas", ariaLabel: "Atlas — browse profile samples" },
        {
          value: "investigate",
          label: "Investigate",
          ariaLabel: "Investigate — explore around a seed address",
        },
      ]}
      value={mode}
      onChange={onChange}
      ariaLabel="Explorer mode"
      size="sm"
    />
  );
}
