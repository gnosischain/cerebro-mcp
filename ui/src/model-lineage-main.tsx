import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ModelLineageApp from "./mini-apps/model-lineage/ModelLineageApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "@xyflow/react/dist/style.css";
import "./mini-apps/model-lineage/model-lineage.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <ModelLineageApp />
    </ThemeProvider>
  </StrictMode>,
);
