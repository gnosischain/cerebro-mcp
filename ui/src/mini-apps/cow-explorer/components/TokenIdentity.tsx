import { useState } from "react";

function shortAddress(value: string): string {
  return /^0x[0-9a-f]{40}$/i.test(value) ? `${value.slice(0, 6)}…${value.slice(-4)}` : value;
}

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
      <span>{symbol || shortAddress(address)}</span>
    </span>
  );
}
