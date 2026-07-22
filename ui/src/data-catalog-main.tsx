import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import DataCatalogApp from "./mini-apps/data-catalog/DataCatalogApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/fonts.css";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
// React Flow base styles + the model-lineage node/canvas styles powering the
// reused LineageGraph component on the entity-profile Lineage tab.
import "@xyflow/react/dist/style.css";
import "./mini-apps/model-lineage/model-lineage.css";
import "./mini-apps/data-catalog/data-catalog.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <DataCatalogApp />
    </ThemeProvider>
  </StrictMode>,
);
