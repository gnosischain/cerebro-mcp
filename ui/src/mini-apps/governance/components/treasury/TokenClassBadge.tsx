import { spamReasonText, TOKEN_CLASS_COPY } from "../../model/treasuryCopy";
import type { SpamReason, TokenClass } from "../../model/treasuryRows";

// The token's class as a small badge, with the reason for spam. The wording
// lives in treasuryCopy so every surface says the same thing.

export function TokenClassBadge({
  tokenClass,
  spamReason = "",
  compact = false,
}: {
  tokenClass: TokenClass;
  spamReason?: SpamReason;
  /** Short label ("hub") for dense table rows. */
  compact?: boolean;
}) {
  const copy = TOKEN_CLASS_COPY[tokenClass];
  if (tokenClass === "spam") {
    const reason = spamReasonText(spamReason);
    return (
      <span
        className="gov-trs-badge gov-trs-badge--spam"
        title={`${copy.description} ${reason.label}: ${reason.description}`}
      >
        {compact ? reason.label.toLowerCase() : `Spam · ${reason.label}`}
      </span>
    );
  }
  return (
    <span className={`gov-trs-badge gov-trs-badge--${tokenClass}`} title={copy.description}>
      {compact ? copy.short : copy.label}
    </span>
  );
}
