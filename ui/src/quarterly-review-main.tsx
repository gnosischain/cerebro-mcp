import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QuarterlyReviewApp } from "./mini-apps/quarterly-review/QuarterlyReviewApp";
import { ThemeProvider } from "./hooks/useTheme";
import "./themes/global.css";
import "./mini-apps/shared/mini-apps.css";
import "./mini-apps/quarterly-review/quarterly-review.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QuarterlyReviewApp />
    </ThemeProvider>
  </StrictMode>,
);
