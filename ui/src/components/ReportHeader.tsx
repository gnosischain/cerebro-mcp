interface Props {
  title: string;
  timestamp: string;
  subtitle?: string;
  fileUri?: string;
}

function splitTitle(t: string) {
  const i = t.indexOf("—");
  if (i === -1) return <>{t}</>;
  return (
    <>
      {t.slice(0, i)}
      <em>{t.slice(i)}</em>
    </>
  );
}

export function ReportHeader({ title, subtitle }: Props) {
  return (
    <header className="report-header">
      <div className="report-header-inner">
        <h1 className="report-header-title">{splitTitle(title)}</h1>
        {subtitle && <p className="report-header-subtitle">{subtitle}</p>}
      </div>
      <div className="report-header-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" fill="none">
          <circle className="owl-ring" cx="16" cy="16" r="13" strokeWidth="1.5" />
          <circle
            className="owl-feature"
            cx="11.5"
            cy="14"
            r="3"
            strokeWidth="1.5"
          />
          <circle
            className="owl-feature"
            cx="20.5"
            cy="14"
            r="3"
            strokeWidth="1.5"
          />
          <circle className="owl-pupil" cx="11.5" cy="14" r="0.9" />
          <circle className="owl-pupil" cx="20.5" cy="14" r="0.9" />
          <path
            className="owl-feature"
            d="M14 19.2 L16 21 L18 19.2"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </header>
  );
}
