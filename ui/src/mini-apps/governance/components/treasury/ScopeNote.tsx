import { SCOPE_NOTE } from "../../model/treasuryCopy";

// What the totals do and do not cover. Shown wherever a total is: the Overview
// and every wallet page. Native ETH/xDAI and non-tokenized positions are not
// indexed, so a total without this line overstates what it measures.

export function ScopeNote() {
  return (
    <p className="gov-trs-scope" role="note">
      <span className="gov-trs-scope__tag">Scope</span>
      {SCOPE_NOTE}
    </p>
  );
}
