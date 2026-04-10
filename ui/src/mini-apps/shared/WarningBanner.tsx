interface Props {
  warnings: string[];
}

export function WarningBanner({ warnings }: Props) {
  if (!warnings || warnings.length === 0) return null;
  return (
    <div className="mini-app-warning-banner" role="status">
      <strong>Heads up</strong>
      <ul>
        {warnings.map((w, i) => (
          <li key={`${i}-${w.slice(0, 24)}`}>{w}</li>
        ))}
      </ul>
    </div>
  );
}
