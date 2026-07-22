import { useState } from "react";

import { shortAddr } from "../../../utils/format";

export function TokenIdentity({ address, iconUrl, symbol }: { address: string; iconUrl?: string; symbol?: string }) {
  const [failedUrl, setFailedUrl] = useState("");
  const showImage = Boolean(iconUrl && iconUrl !== failedUrl);
  const fallback = (symbol || address.slice(2, 4) || "?").slice(0, 2).toUpperCase();
  return (
    <span className="cow-token" title={address}>
      {showImage ? (
        <img src={iconUrl} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailedUrl(iconUrl!)} />
      ) : (
        <span className="cow-token__fallback" aria-hidden="true">{fallback}</span>
      )}
      <span>{symbol || shortAddr(address)}</span>
    </span>
  );
}
