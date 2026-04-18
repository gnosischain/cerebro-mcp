import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import YieldOpportunitiesApp from "./mini-apps/yield-opportunities/YieldOpportunitiesApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <YieldOpportunitiesApp />
    </ThemeProvider>
  </StrictMode>,
);
