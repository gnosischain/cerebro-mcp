import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import GraphExplorerApp from "./mini-apps/graph-explorer/GraphExplorerApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "./mini-apps/graph-explorer/graph-explorer.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <GraphExplorerApp />
    </ThemeProvider>
  </StrictMode>,
);
