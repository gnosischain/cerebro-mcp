interface Props {
  text?: string;
  inline?: boolean;
}

export function LoadingOverlay({ text = "Loading…", inline = false }: Props) {
  return (
    <div
      className={`loading ${inline ? "loading--inline" : ""}`}
      role="status"
      aria-live="polite"
    >
      <span className="loading__spinner" aria-hidden />
      <span>{text}</span>
    </div>
  );
}
