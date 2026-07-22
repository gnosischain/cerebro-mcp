import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "./hooks/useTheme";
import GovernanceApp from "./mini-apps/governance/GovernanceApp";
import "./themes/fonts.css";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "./mini-apps/governance/governance.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <GovernanceApp />
    </ThemeProvider>
  </StrictMode>,
);
