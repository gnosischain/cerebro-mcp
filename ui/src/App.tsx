import { useMemo } from "react";
import { useReportData } from "./hooks/useReportData";
import { ReportHeader } from "./components/ReportHeader";
import { ReportContent } from "./components/ReportContent";
import { ReportFooter } from "./components/ReportFooter";
import { LoadingState } from "./components/LoadingState";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { parseHtmlSections } from "./utils/parseHtmlSections";

export default function App() {
  const data = useReportData();

  const sections = useMemo(
    () => (data ? parseHtmlSections(data.sections_html) : []),
    [data]
  );

  if (!data) {
    return <LoadingState />;
  }

  return (
    <ErrorBoundary fallbackLabel="Report">
      <div className="dashboard">
        <div className="dashboard-main">
          <ReportHeader
            title={data.title}
            timestamp={data.timestamp}
            fileUri={data.file_uri}
          />
          <ReportContent data={data} sections={sections} />
          <ReportFooter />
        </div>
      </div>
    </ErrorBoundary>
  );
}
