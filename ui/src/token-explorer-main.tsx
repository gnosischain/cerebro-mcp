import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import TokenExplorerApp from "./mini-apps/token-explorer/TokenExplorerApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <TokenExplorerApp />
    </ThemeProvider>
  </StrictMode>,
);
