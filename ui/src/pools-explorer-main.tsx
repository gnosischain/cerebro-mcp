import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "./hooks/useTheme";
import PoolsExplorerApp from "./mini-apps/pools-explorer/PoolsExplorerApp";
import "./themes/fonts.css";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "./mini-apps/pools-explorer/pools-explorer.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <PoolsExplorerApp />
    </ThemeProvider>
  </StrictMode>,
);
