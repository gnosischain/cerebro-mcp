import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import MetricLabApp from "./mini-apps/metric-lab/MetricLabApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <MetricLabApp />
    </ThemeProvider>
  </StrictMode>,
);
