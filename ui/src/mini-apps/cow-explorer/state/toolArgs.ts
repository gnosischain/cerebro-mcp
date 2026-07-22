import type { CowExplorerViewState, CowSection } from "../types";

export interface CowFilterDraft {
  base: string;
  quote: string;
  status: string;
  owner: string;
  token: string;
  solver: string;
}

export function buildSectionToolArgs(
  viewId: string,
  state: CowExplorerViewState,
  section: Exclude<CowSection, "entity">,
  draft: CowFilterDraft,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  // Chain defaulting is the SERVER's job: sections that support all-networks
  // accept chain 0 as-is; single-chain sections coerce server-side (with an
  // explicit warning), and Live picks the freshest-indexing default chain.
  const chainId = Number(overrides.chain_id ?? state.chain_id);
  const hasPair = ["markets", "orders", "solvers"].includes(section);
  return {
    __tool: "load_cow_explorer_section",
    view_id: viewId,
    section,
    environment_scope: state.environment_scope,
    chain_id: chainId,
    base_token: hasPair ? draft.base : "",
    quote_token: hasPair ? draft.quote : "",
    interval: state.interval,
    window_days: state.date_range.kind === "all" ? 0 : (state.date_range.window_days ?? -1),
    start_at: state.date_range.kind === "absolute" ? state.date_range.start_at : "",
    end_at: state.date_range.kind === "absolute" ? state.date_range.end_at : "",
    status: draft.status,
    owner: draft.owner,
    token: draft.token,
    solver: draft.solver,
    ...overrides,
  };
}
