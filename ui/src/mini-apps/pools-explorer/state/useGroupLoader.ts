// Thin binding of the shared deferred-group loader to the pools app-only
// group tool. Same export names as the CoW binding so the app wiring reads
// identically across v2 mini-apps.

import {
  useGroupLoader as useSharedGroupLoader,
  type GroupCallTool,
  type GroupLoader,
} from "../../shared/useGroupLoader";
import { TOOL_DATASETS } from "./toolArgs";

export type { GroupCallTool, GroupLoader };

export function useGroupLoader(callTool: GroupCallTool): GroupLoader {
  return useSharedGroupLoader(callTool, TOOL_DATASETS);
}
