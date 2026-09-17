import { fmtFee } from "../model/format";

export function FeeChip({ fee, poolClass }: { fee: number | null | undefined; poolClass?: string | null }) {
  const dynamic = poolClass === "swapr_v3_algebra";
  return (
    <span
      className={`plx-fee${dynamic ? " plx-fee--dyn" : ""}`}
      title={dynamic ? "Algebra dynamic fee — the value at this publication, in pips" : fee === null || fee === undefined ? "No fee (reserves-only pool)" : `${fee} pips`}
    >
      {fmtFee(fee ?? null, poolClass)}
    </span>
  );
}
