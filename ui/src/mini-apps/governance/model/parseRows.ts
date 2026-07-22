// Column-name-keyed row parsing (order-robust — never index into rows by
// position), built on the shared RowDataset helpers.

import { finite, rowsToObjects, type RowDataset } from "../../shared/rowDataset";
import type {
  ActivityRow,
  ConcentrationRow,
  LinkRow,
  SpaceSummaryRow,
} from "../types";

export function parseSpaceSummary(dataset?: RowDataset): SpaceSummaryRow | null {
  const row = rowsToObjects(dataset)[0];
  if (!row) return null;
  return {
    proposal_count: finite(row.proposal_count) ?? 0,
    vote_count: finite(row.vote_count) ?? 0,
    voter_count: finite(row.voter_count) ?? 0,
    follower_count: finite(row.follower_count) ?? 0,
    topic_count: finite(row.topic_count) ?? 0,
    post_count: finite(row.post_count) ?? 0,
    forum_user_count: finite(row.forum_user_count) ?? 0,
  };
}

const BUCKET_UNITS = new Set(["day", "week", "month"]);

/** Pivot LONG-format activity rows ({bucket, metric, metric_value, …}) into
 * one wide row per bucket (first-seen bucket order preserved). Buckets with a
 * missing metric simply lack the field — chart builders apply `?? 0`. */
function pivotLongActivity(
  objects: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  const byBucket = new Map<string, Record<string, unknown>>();
  for (const row of objects) {
    if (!row.bucket) continue;
    const key = String(row.bucket);
    let target = byBucket.get(key);
    if (!target) {
      target = { bucket: row.bucket, bucket_unit: row.bucket_unit };
      byBucket.set(key, target);
    }
    if (typeof row.metric === "string" && row.metric !== "" && row.metric !== "bucket" && row.metric !== "bucket_unit") {
      target[row.metric] = row.metric_value;
    }
  }
  return [...byBucket.values()];
}

export function parseActivity(dataset?: RowDataset): ActivityRow[] {
  const objects = rowsToObjects(dataset);
  const isLong = (dataset?.columns ?? []).includes("metric")
    && (dataset?.columns ?? []).includes("metric_value");
  const wide = isLong ? pivotLongActivity(objects) : objects;
  return wide.flatMap((row) => {
    if (!row.bucket) return [];
    const unit = BUCKET_UNITS.has(String(row.bucket_unit)) ? String(row.bucket_unit) : "day";
    const parsed: ActivityRow = {
      ...row,
      bucket: String(row.bucket),
      bucket_unit: unit as ActivityRow["bucket_unit"],
    };
    for (const [name, value] of Object.entries(row)) {
      if (name === "bucket" || name === "bucket_unit") continue;
      const n = finite(value);
      if (n !== null) parsed[name] = n;
      else if (typeof value !== "string") delete parsed[name];
    }
    return [parsed];
  });
}

export function parseConcentration(dataset?: RowDataset): ConcentrationRow[] {
  return rowsToObjects(dataset).flatMap((row) => {
    const tier = finite(row.tier);
    const metric = row.metric === "vp" || row.metric === "votes" ? row.metric : null;
    if (tier === null || !metric) return [];
    return [{ tier, metric, share: finite(row.share) }];
  });
}

/** Cross-link rows (`proposal_forum_links` / `topic_proposal_links`).
 * `proposal_forum_links` carries an explicit `linked_type`
 * ('forum_topic' | 'proposal'); `topic_proposal_links` rows have none — they
 * are all proposals. */
export function parseLinks(dataset?: RowDataset): LinkRow[] {
  return rowsToObjects(dataset).flatMap((row) => {
    const source = row.link_source === "discussion" || row.link_source === "gip" ? row.link_source : null;
    if (!source || row.linked_id === null || row.linked_id === undefined || row.linked_id === "") return [];
    return [{
      linked_type: row.linked_type === "forum_topic" ? "forum_topic" as const : "proposal" as const,
      linked_id: String(row.linked_id),
      linked_title: row.linked_title === undefined || row.linked_title === null ? undefined : String(row.linked_title),
      link_source: source,
      activity_count: finite(row.activity_count),
      activity_at: typeof row.activity_at === "string" ? row.activity_at : undefined,
      state: typeof row.state === "string" ? row.state : undefined,
      votes_count: finite(row.votes_count),
      created_at: typeof row.created_at === "string" ? row.created_at : undefined,
    }];
  });
}
