import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import PortfolioApp from "./mini-apps/portfolio/PortfolioApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/fonts.css";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <PortfolioApp />
    </ThemeProvider>
  </StrictMode>,
);
