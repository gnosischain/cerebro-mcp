import { SIGNALING_DISCLAIMER } from "../model/contextPrompt";

/** The standing provenance line: Snapshot signaling + forum activity only —
 * never binding on-chain execution. Rendered on every detail view. */
export function SignalingNote() {
  return <p className="gov-signaling">{SIGNALING_DISCLAIMER}</p>;
}
