import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "./hooks/useTheme";
import GovernanceApp from "./mini-apps/governance/GovernanceApp";
import "./themes/fonts.css";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
// The Graph tab's Clusters view runs on graph-explorer's WebGL canvas
// (GraphCanvas), so this bundle needs its stylesheet too — .ge-canvas /
// .ge-graph-stage / the toolbar are all defined there. Safe to import because
// every selector in it is `.ge-*` scoped; the global document rules live in
// graph-explorer-shell.css, which only that app imports.
import "./mini-apps/graph-explorer/graph-explorer.css";
import "./mini-apps/governance/governance.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <GovernanceApp />
    </ThemeProvider>
  </StrictMode>,
);
