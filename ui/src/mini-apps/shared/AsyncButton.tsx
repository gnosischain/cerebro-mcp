import { useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost";

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onClick"> {
  onClick: () => Promise<unknown> | unknown;
  loadingLabel?: string;
  variant?: Variant;
  children: ReactNode;
}

/**
 * Async-aware button with an internal request-id guard. Rapid clicks are
 * serialised: subsequent clicks while a promise is pending are ignored
 * (the `disabled` attribute already blocks re-entry; the reqId fence is a
 * belt-and-braces guard for callers that ignore `disabled`).
 *
 * If the underlying promise throws, the button logs the error and resets
 * its pending state so the user can retry. Callers that need custom
 * error handling should wrap their own onClick in try/catch and swallow.
 */
export function AsyncButton({
  onClick,
  loadingLabel = "Loading",
  variant = "primary",
  children,
  className,
  disabled,
  ...rest
}: Props) {
  const [pending, setPending] = useState(false);
  const reqId = useRef(0);

  const handle = async () => {
    if (pending) return;
    const id = ++reqId.current;
    setPending(true);
    try {
      await onClick();
    } catch (err) {
      console.error("[AsyncButton] click handler threw", err);
    } finally {
      if (id === reqId.current) setPending(false);
    }
  };

  const classes = `btn btn--${variant} ${pending ? "btn--pending" : ""} ${className ?? ""}`.trim();
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || pending}
      onClick={handle}
      className={classes}
    >
      {pending ? `${loadingLabel}…` : children}
    </button>
  );
}
