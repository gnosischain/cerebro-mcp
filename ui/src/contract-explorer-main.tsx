import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ContractExplorerApp from "./mini-apps/contract-explorer/ContractExplorerApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "./mini-apps/contract-explorer/contract-explorer.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <ContractExplorerApp />
    </ThemeProvider>
  </StrictMode>,
);
