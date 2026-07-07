import { useMemo } from "react";
import { useReportData } from "./hooks/useReportData";
import { ReportHeader } from "./components/ReportHeader";
import { ReportContent } from "./components/ReportContent";
import { ReportFooter } from "./components/ReportFooter";
import { StatusStrip } from "./components/StatusStrip";
import { ResearchReportLayout } from "./components/ResearchReportLayout";
import { CaseStudyLayout } from "./components/CaseStudyLayout";
import { LoadingState } from "./components/LoadingState";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { parseHtmlSections } from "./utils/parseHtmlSections";

export default function App() {
  const { data, timedOut } = useReportData();

  const sections = useMemo(
    () =>
      data &&
      data.presentation_mode !== "research" &&
      data.presentation_mode !== "scrollytelling"
        ? parseHtmlSections(data.sections_html)
        : [],
    [data]
  );

  if (!data) {
    return <LoadingState timedOut={timedOut} />;
  }

  if (data.presentation_mode === "research") {
    return (
      <ErrorBoundary fallbackLabel="Research report">
        <ResearchReportLayout data={data} />
      </ErrorBoundary>
    );
  }

  if (data.presentation_mode === "scrollytelling") {
    return (
      <ErrorBoundary fallbackLabel="Case study">
        <CaseStudyLayout data={data} />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary fallbackLabel="Report">
      <div className="dashboard">
        <div className="dashboard-main">
          <StatusStrip
            kind="dashboard"
            chartCount={Object.keys(data.charts).length}
            queryCount={Object.keys(data.queries ?? {}).length}
            timestamp={data.timestamp}
          />
          <ReportHeader
            title={data.title}
            timestamp={data.timestamp}
            subtitle={data.subtitle}
            fileUri={data.file_uri}
          />
          <ReportContent data={data} sections={sections} />
          <ReportFooter
            fileUri={data.file_uri}
            timestamp={data.timestamp}
          />
        </div>
      </div>
    </ErrorBoundary>
  );
}
