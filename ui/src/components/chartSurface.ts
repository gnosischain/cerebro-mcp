// Which rendering surface a ChartCard is mounted on. Mini-apps (inside
// MiniAppChrome / `.mini-app-scope`) use the cool near-black palette and need
// the `cerebro-*-mini` ECharts themes; reports keep the indigo "Terminal"
// themes. Lives in its own module to avoid a MiniAppChrome ↔ ChartCard
// import cycle.
import { createContext } from "react";

export type ChartSurface = "report" | "mini";

export const ChartSurfaceContext = createContext<ChartSurface>("report");
