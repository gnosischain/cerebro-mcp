export function shortAddr(addr: string, lead = 6, tail = 4): string {
  if (!addr) return "";
  if (addr.length <= lead + tail + 1) return addr;
  return `${addr.slice(0, lead)}…${addr.slice(-tail)}`;
}

/** Escape the five HTML metacharacters. Anything that reaches an HTML string —
 * an ECharts tooltip formatter, the toolbox data view — must go through here:
 * token symbols, wallet labels and series names are often attacker-authored
 * (ERC-20 `symbol()`/`name()` are whatever the deployer wrote). */
export function escapeHtml(value: unknown): string {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]!,
  );
}
