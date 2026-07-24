// Two-level grouped navigation bar. Row 1 = nav groups + the universal
// search form (passed through as a slot, unchanged). Row 2 = the active
// group's subtabs, rendered only when the group has more than one
// destination. Clicking a group jumps to its last-visited destination
// (seeded with each group's first entry).

import { useEffect, useRef, type ReactNode } from "react";
import {
  NAV_GROUPS,
  SECTION_TO_GROUP,
  type CowNavDestination,
  type CowNavGroup,
} from "../model/navGroups";

interface CowNavProps {
  activeDestination: CowNavDestination;
  onNavigate: (dest: CowNavDestination) => void;
  /** The existing universal search form, rendered untouched in row 1. */
  searchSlot?: ReactNode;
}

export function CowNav({ activeDestination, onNavigate, searchSlot }: CowNavProps) {
  const lastVisitedRef = useRef<Record<CowNavGroup["id"], CowNavDestination>>(
    Object.fromEntries(
      NAV_GROUPS.map((group) => [group.id, group.destinations[0].id]),
    ) as Record<CowNavGroup["id"], CowNavDestination>,
  );
  const activeGroupId = SECTION_TO_GROUP[activeDestination];
  // Track last-visited per group, including navigations that did not come
  // through this component (URL deep links, entity back, pair click-through).
  useEffect(() => {
    if (activeGroupId) lastVisitedRef.current[activeGroupId] = activeDestination;
  }, [activeDestination, activeGroupId]);
  const activeGroup = NAV_GROUPS.find((group) => group.id === activeGroupId);

  return (
    <div className="cow-nav">
      <div className="cow-subbar">
        <div className="cow-nav-groups" role="tablist" aria-label="CoW Explorer sections">
          {NAV_GROUPS.map((group) => (
            <button
              key={group.id}
              type="button"
              role="tab"
              aria-selected={group.id === activeGroupId}
              className={group.id === activeGroupId ? "is-active" : ""}
              onClick={() => onNavigate(lastVisitedRef.current[group.id] ?? group.destinations[0].id)}
            >
              {group.label}
            </button>
          ))}
        </div>
        {searchSlot}
      </div>
      {activeGroup && activeGroup.destinations.length > 1 && (
        <div className="cow-nav-subtabs" role="tablist" aria-label={`${activeGroup.label} views`}>
          {activeGroup.destinations.map((entry) => (
            <button
              key={entry.id}
              type="button"
              role="tab"
              aria-selected={entry.id === activeDestination}
              className={entry.id === activeDestination ? "is-active" : ""}
              onClick={() => onNavigate(entry.id)}
            >
              {entry.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
