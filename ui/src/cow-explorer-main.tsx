import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { ThemeProvider } from "./hooks/useTheme";
import CowExplorerApp from "./mini-apps/cow-explorer/CowExplorerApp";
import "./themes/fonts.css";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "./mini-apps/cow-explorer/cow-explorer.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <CowExplorerApp />
    </ThemeProvider>
  </StrictMode>,
);
