import { useRef, useState } from "react";

interface AssistantBarProps {
  /** Human-readable context shown as placeholder hint. */
  contextHint: string;
  /** Fires when the user submits a question. */
  onSend: (text: string) => Promise<boolean>;
}

/**
 * Slim input bar for asking the assistant questions without leaving
 * the mini-app. Calls `sendMessage` under the hood — the LLM response
 * appears in the conversation below the app widget, and any tool calls
 * it makes flow back into the app via `ontoolresult`.
 */
export function AssistantBar({ contextHint, onSend }: AssistantBarProps) {
  const [value, setValue] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = async () => {
    const text = value.trim();
    if (!text || sending) return;
    setSending(true);
    setStatus("Sending to assistant…");
    const ok = await onSend(text);
    setSending(false);
    if (ok) {
      setValue("");
      setStatus("Sent — check the conversation below for the response.");
      setTimeout(() => setStatus(null), 4000);
    } else {
      setStatus("Could not reach the assistant.");
      setTimeout(() => setStatus(null), 4000);
    }
    inputRef.current?.focus();
  };

  return (
    <div className="mini-app-assistant">
      <input
        ref={inputRef}
        type="text"
        className="mini-app-assistant__input"
        placeholder={`Ask about ${contextHint}…`}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void handleSubmit();
          }
        }}
        disabled={sending}
      />
      <button
        type="button"
        className="mini-app-assistant__send"
        onClick={handleSubmit}
        disabled={sending || !value.trim()}
      >
        Ask
      </button>
      {status && <span className="mini-app-assistant__status">{status}</span>}
    </div>
  );
}
