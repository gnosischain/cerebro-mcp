import { chainName } from "../../model/treasuryChains";

// The same wallet (or registry asset) on the other chain is one click away.
// A chain the entity does not exist on stays visible but greyed, with the
// reason — a missing button would read as "there is no other chain".

export interface ChainOption {
  chainId: number;
  enabled: boolean;
  /** Short figure shown on the button ("$1.2M", "3 tokens"). */
  note?: string;
  /** Tooltip, e.g. why the chain is disabled. */
  title?: string;
}

export function ChainSwitcher({
  current,
  options,
  onSelect,
  ariaLabel = "Chain",
}: {
  current: number;
  options: ChainOption[];
  onSelect: (chainId: number) => void;
  ariaLabel?: string;
}) {
  return (
    <div className="gov-trs-switcher" role="group" aria-label={ariaLabel}>
      {options.map((option) => {
        const active = option.chainId === current;
        return (
          <button
            key={option.chainId}
            type="button"
            className={active ? "gov-trs-switcher__btn is-active" : "gov-trs-switcher__btn"}
            aria-pressed={active}
            disabled={!option.enabled && !active}
            title={option.title}
            onClick={() => {
              if (!active && option.enabled) onSelect(option.chainId);
            }}
          >
            <span>{chainName(option.chainId)}</span>
            {option.note ? <span className="gov-trs-switcher__note">{option.note}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
