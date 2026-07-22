// Thin CoW binding of the shared deferred-group loader (promoted to
// ../../shared/useGroupLoader.ts). Keeps every existing CoW import site
// working unchanged: same export names, same behavior, toolName pinned to
// the CoW app-only group tool.

import {
  useGroupLoader as useSharedGroupLoader,
  type GroupCallTool,
  type GroupLoader,
} from "../../shared/useGroupLoader";

export type { GroupCallTool, GroupLoader };

export function useGroupLoader(callTool: GroupCallTool): GroupLoader {
  return useSharedGroupLoader(callTool, "load_cow_explorer_datasets");
}
