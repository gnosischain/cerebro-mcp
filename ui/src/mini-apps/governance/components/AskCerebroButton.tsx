import { useState } from "react";
import { AsyncButton } from "../../shared/AsyncButton";
import { buildAskPrompt, type GovAggregates } from "../model/contextPrompt";
import type { GovernanceViewState } from "../types";

export type AskDelivery = "sent" | "fallback";

/** Pure delivery helper (unit-tested with fakes, no DOM): attempt host
 * delivery via `sendMessage`; a `false` return (no ext-apps host) or a throw
 * means the caller must open the copyable-prompt fallback. */
export async function deliverAskPrompt(
  send: (text: string) => Promise<boolean>,
  prompt: string,
): Promise<AskDelivery> {
  try {
    return (await send(prompt)) ? "sent" : "fallback";
  } catch {
    return "fallback";
  }
}

/** "Ask Cerebro" — hands the current view context to the host chat via
 * sendMessage; hosts without the capability get a copyable-prompt modal. */
export function AskCerebroButton({ state, aggregates, sendMessage }: {
  state: GovernanceViewState;
  aggregates: GovAggregates;
  sendMessage: (text: string) => Promise<boolean>;
}) {
  const [fallbackPrompt, setFallbackPrompt] = useState("");
  const [copied, setCopied] = useState(false);

  const close = () => {
    setFallbackPrompt("");
    setCopied(false);
  };

  return (
    <>
      <AsyncButton
        variant="secondary"
        loadingLabel="Asking"
        onClick={async () => {
          const prompt = buildAskPrompt(state, aggregates);
          if ((await deliverAskPrompt(sendMessage, prompt)) === "fallback") {
            setFallbackPrompt(prompt);
          }
        }}
      >
        Ask Cerebro
      </AsyncButton>
      {fallbackPrompt && (
        <div
          className="gov-ask-overlay mini-app-scope"
          role="dialog"
          aria-modal="true"
          aria-label="Copy the Cerebro prompt"
          onClick={close}
        >
          <div className="gov-ask-dialog" onClick={(event) => event.stopPropagation()}>
            <header>
              <strong>Chat host unavailable</strong>
              <button type="button" aria-label="Close" onClick={close}>×</button>
            </header>
            <p>This host cannot receive chat messages. Copy the prompt below into your Cerebro chat:</p>
            <textarea readOnly rows={12} value={fallbackPrompt} onFocus={(event) => event.target.select()} />
            <div className="gov-ask-actions">
              <button
                type="button"
                onClick={async () => {
                  try {
                    await navigator.clipboard?.writeText(fallbackPrompt);
                    setCopied(true);
                  } catch {
                    // Clipboard unavailable — the textarea stays selectable.
                  }
                }}
              >
                {copied ? "Copied" : "Copy prompt"}
              </button>
              <button type="button" onClick={close}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
