// Probe status is a first-class badge: only ~427 of ~2,500 CL pools are
// probed for ticks; the rest are STATE-ONLY (`cl_below_active_threshold`) and
// have no liquidity profile. Balancer pools are reserves-only by design.

export interface ProbeBadgeProps {
  family: string | null | undefined;
  probed: boolean;
  /** A CL state row exists at this publication. `false` is NOT "liquidity 0". */
  hasState?: boolean;
}

export function probeStatus(
  family: string | null | undefined,
  probed: boolean,
  hasState = true,
): "probed" | "state_only" | "reserves_only" | "no_state" {
  if (family === "reserves_only") return "reserves_only";
  if (!hasState) return "no_state";
  return probed ? "probed" : "state_only";
}

const COPY: Record<ReturnType<typeof probeStatus>, { label: string; title: string }> = {
  probed: { label: "probed", title: "Initialized ticks were probed at this publication — a liquidity profile exists" },
  state_only: { label: "state only", title: "Below the active-liquidity threshold: slot0 state only, ticks not probed (cl_below_active_threshold)" },
  reserves_only: { label: "reserves only", title: "Balancer pool: raw token reserves only, no tick liquidity" },
  no_state: { label: "no state row", title: "No concentrated-liquidity state row was published for this pool at this date — not the same as liquidity being zero" },
};

export function ProbeBadge({ family, probed, hasState }: ProbeBadgeProps) {
  const status = probeStatus(family, probed, hasState ?? true);
  const copy = COPY[status];
  return (
    <span className={`plx-badge plx-badge--${status.replace("_", "-")}`} title={copy.title}>
      {copy.label}
    </span>
  );
}
