// Forum-poll grouping for the topic drill-down. `topic_polls` rows are
// per-OPTION (poll-level fields repeated per row by design) — this module
// pivots them into one view per poll for a ChoiceBars render. `voters` is the
// poll-level participant total, taken from the group's first row and NEVER
// summed across options.

import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import type { ChoiceEntry } from "./choices";

export interface PollView {
  pollId: number;
  pollName: string;
  pollType: string;
  status: string;
  resultsVisibility: string;
  closeAt: string | null;
  /** Poll-bearing post number (polls are NOT always in the opening post);
   * null when the post row is unmapped (the SQL LEFT JOIN lands 0). */
  postNumber: number | null;
  voters: number;
  entries: ChoiceEntry[];
  /** Every option score is null — Discourse hides results until vote/close. */
  resultsHidden: boolean;
  /** At least one option has a positive score ("No votes yet" otherwise). */
  hasVotes: boolean;
}

export function groupPollOptions(dataset?: RowDataset): PollView[] {
  const byPoll = new Map<number, PollView>();
  for (const row of rowsToObjects(dataset)) {
    const pollId = finite(row.poll_id);
    if (pollId === null) continue;
    let view = byPoll.get(pollId);
    if (!view) {
      const postNumber = finite(row.post_number);
      view = {
        pollId,
        pollName: String(row.poll_name ?? ""),
        pollType: String(row.poll_type ?? ""),
        status: String(row.status ?? ""),
        resultsVisibility: String(row.results_visibility ?? ""),
        closeAt: typeof row.close_at === "string" && row.close_at !== "" ? row.close_at : null,
        postNumber: postNumber !== null && postNumber > 0 ? postNumber : null,
        voters: finite(row.voters) ?? 0,
        entries: [],
        resultsHidden: false,
        hasVotes: false,
      };
      byPoll.set(pollId, view);
    }
    view.entries.push({
      index: view.entries.length + 1,
      label: String(row.option_label ?? ""),
      score: finite(row.option_votes),
    });
  }
  const views = [...byPoll.values()];
  for (const view of views) {
    view.resultsHidden = view.entries.length > 0 && view.entries.every((e) => e.score === null);
    view.hasVotes = view.entries.some((e) => (e.score ?? 0) > 0);
  }
  return views;
}
