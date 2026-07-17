// Report Studio root: Archive (gallery -> native preview) | Composer.
// Archive pages and selected entries are plain-dict tool results held in
// local state; only open_report_studio returns a typed INITIAL_LOAD payload.

import { useCallback, useMemo, useRef, useState } from "react";
import { MiniAppChrome, MaSkeletonKpiGrid } from "../shared/MiniAppChrome";
import { SegmentedControl } from "../shared/SegmentedControl";
import { WarningBanner } from "../shared/WarningBanner";
import { useMiniApp } from "../shared/useMiniApp";
import { ArchiveGallery } from "./ArchiveGallery";
import { ComposerPanel } from "./ComposerPanel";
import { ReportPreview } from "./ReportPreview";
import { buildMockPayload } from "./devFixture";
import { APP_ID, type ReportEntry, type StudioState } from "./types";

export default function ReportStudioApp() {
  const mock = useMemo(
    () => (import.meta.env.DEV ? buildMockPayload() : undefined),
    [],
  );
  const {
    view,
    callTool: rawCallTool,
    openLink,
    sendMessage,
  } = useMiniApp<StudioState>({ appId: APP_ID, mockPayload: mock });

  const callToolRef = useRef(rawCallTool);
  callToolRef.current = rawCallTool;
  const callTool = useCallback(
    <T,>(name: string, args: Record<string, unknown>): Promise<T | null> =>
      callToolRef.current<T>(name, args),
    [],
  );

  const state = view?.view_state ?? null;
  const mutationsEnabled = Boolean(state?.mutations_enabled);

  const [screen, setScreen] = useState<"archive" | "compose">("archive");
  const [entry, setEntry] = useState<ReportEntry | null>(null);
  const [entryError, setEntryError] = useState("");
  const [refreshNonce, setRefreshNonce] = useState(0);
  const adoptedEntry = useRef(false);

  // Deep-linked preview from the INITIAL_LOAD (open_report_studio(report=…)).
  if (!adoptedEntry.current && state?.selected_entry?.ok) {
    adoptedEntry.current = true;
    setEntry(state.selected_entry);
  }

  const openEntry = useCallback(
    async (reportRef: string) => {
      setEntryError("");
      try {
        const result = await callTool<ReportEntry>("get_report_archive_entry", {
          report_ref: reportRef,
        });
        if (result?.ok) {
          setEntry(result);
        } else {
          setEntryError(result?.error ?? "Could not open the report.");
        }
      } catch (err) {
        setEntryError(err instanceof Error ? err.message : "Preview failed");
      }
    },
    [callTool],
  );

  const askAgentForCharts = useCallback(() => {
    void sendMessage(
      "Generate a few charts for the topic I care about so I can compose a "
        + "report from them in the Report Studio.",
    );
  }, [sendMessage]);

  return (
    <MiniAppChrome activeTabId="reports">
      <div className="mini-app-root rst-root">
        {!view && <MaSkeletonKpiGrid />}

        {view && state && (
          <>
            <WarningBanner warnings={view.warnings ?? []} />
            {entryError && <div className="rst-error">{entryError}</div>}

            {!entry && (
              <div className="rst-nav">
                <SegmentedControl<"archive" | "compose">
                  ariaLabel="Report Studio section"
                  value={screen}
                  onChange={setScreen}
                  options={[
                    { value: "archive", label: "Archive" },
                    ...(mutationsEnabled
                      ? [{ value: "compose" as const, label: "Composer" }]
                      : []),
                  ]}
                />
              </div>
            )}

            {entry ? (
              <ReportPreview
                entry={entry}
                mutationsEnabled={mutationsEnabled}
                callTool={callTool}
                openLink={openLink}
                onBack={() => setEntry(null)}
                onDeleted={() => {
                  setEntry(null);
                  setRefreshNonce((n) => n + 1);
                }}
                onRenamed={(reportRef) => {
                  setRefreshNonce((n) => n + 1);
                  void openEntry(reportRef);
                }}
              />
            ) : screen === "compose" && mutationsEnabled ? (
              <ComposerPanel
                records={state.session_charts?.charts ?? []}
                callTool={callTool}
                onAskAgent={askAgentForCharts}
                onComposed={(reportId) => {
                  setScreen("archive");
                  setRefreshNonce((n) => n + 1);
                  void openEntry(reportId);
                }}
              />
            ) : (
              <ArchiveGallery
                initial={state.archive}
                callTool={callTool}
                onOpen={openEntry}
                refreshNonce={refreshNonce}
              />
            )}
          </>
        )}
      </div>
    </MiniAppChrome>
  );
}
