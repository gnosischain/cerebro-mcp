import { classLabel } from "../model/format";

export function ClassBadge({ poolClass }: { poolClass: string | null | undefined }) {
  const key = String(poolClass ?? "");
  const modifier = key.replace(/_/g, "-") || "unknown";
  return (
    <span className={`plx-class plx-class--${modifier}`} title={key || "unknown class"}>
      {classLabel(key)}
    </span>
  );
}
