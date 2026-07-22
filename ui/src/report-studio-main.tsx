import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ReportStudioApp from "./mini-apps/report-studio/ReportStudioApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/fonts.css";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "./mini-apps/report-studio/report-studio.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <ReportStudioApp />
    </ThemeProvider>
  </StrictMode>,
);
