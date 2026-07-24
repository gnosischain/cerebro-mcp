// Report Studio root: Archive (gallery -> native preview, the landing tab) |
// Templates (catalog -> detail). The template catalog is compile-time data
// (model/catalog.ts); only the archive talks to the server. There is
// deliberately NO construction surface — templates are copied and handed to
// an agent to execute.

import { useCallback, useMemo, useRef, useState } from "react";
import { MiniAppChrome, MaSkeletonKpiGrid } from "../shared/MiniAppChrome";
import { SegmentedControl } from "../shared/SegmentedControl";
import { WarningBanner } from "../shared/WarningBanner";
import { useMiniApp } from "../shared/useMiniApp";
import { ArchiveGallery } from "./ArchiveGallery";
import { CatalogScreen } from "./CatalogScreen";
import { ReportPreview } from "./ReportPreview";
import { TemplateDetail } from "./TemplateDetail";
import { buildMockPayload } from "./devFixture";
import { templateById } from "./model/catalog";
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

  const [tab, setTab] = useState<"templates" | "archive">("archive");
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [entry, setEntry] = useState<ReportEntry | null>(null);
  const [entryError, setEntryError] = useState("");
  const [refreshNonce, setRefreshNonce] = useState(0);
  const adoptedEntry = useRef(false);

  // Deep-linked preview from the INITIAL_LOAD (open_report_studio(report=…)).
  if (!adoptedEntry.current && state?.selected_entry?.ok) {
    adoptedEntry.current = true;
    setEntry(state.selected_entry);
    setTab("archive");
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

  const template = templateId ? templateById(templateId) : undefined;

  return (
    <MiniAppChrome activeTabId="reports">
      <div className="mini-app-root rst-root">
        {!view && <MaSkeletonKpiGrid />}

        {view && state && (
          <>
            <WarningBanner warnings={view.warnings ?? []} />
            {entryError && <div className="rst-error">{entryError}</div>}

            {!entry && !template && (
              <div className="rst-nav">
                <SegmentedControl<"templates" | "archive">
                  ariaLabel="Report Studio section"
                  value={tab}
                  onChange={setTab}
                  options={[
                    { value: "archive", label: "Archive" },
                    { value: "templates", label: "Templates" },
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
            ) : template ? (
              <TemplateDetail
                template={template}
                onBack={() => setTemplateId(null)}
                onSendToAgent={(instructions) => {
                  void sendMessage(instructions);
                }}
              />
            ) : tab === "archive" ? (
              <ArchiveGallery
                initial={state.archive}
                callTool={callTool}
                onOpen={openEntry}
                refreshNonce={refreshNonce}
              />
            ) : (
              <CatalogScreen onOpen={setTemplateId} />
            )}
          </>
        )}
      </div>
    </MiniAppChrome>
  );
}
