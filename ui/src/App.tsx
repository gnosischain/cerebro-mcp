import { useState, useMemo } from "react";
import { useReportData } from "./hooks/useReportData";
import { ReportHeader } from "./components/ReportHeader";
import { ReportContent } from "./components/ReportContent";
import { Sidebar } from "./components/Sidebar";
import { ReportFooter } from "./components/ReportFooter";
import { LoadingState } from "./components/LoadingState";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { parseHtmlSections } from "./utils/parseHtmlSections";

export default function App() {
  const data = useReportData();
  const [activeSection, setActiveSection] = useState(0);

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
        <Sidebar
          sections={sections}
          activeIndex={activeSection}
          onChange={setActiveSection}
        />
        <div className="dashboard-main">
          <ReportHeader title={data.title} timestamp={data.timestamp} fileUri={data.file_uri} />
          <ReportContent
            data={data}
            sections={sections}
            activeIndex={activeSection}
          />
          <ReportFooter />
        </div>
      </div>
    </ErrorBoundary>
  );
}
