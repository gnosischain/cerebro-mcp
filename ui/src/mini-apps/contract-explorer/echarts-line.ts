// Minimal ECharts registration for the Contract Explorer's history chart.
//
// Deliberately NOT importing ui/src/lib/echarts-setup.ts: that registers 11
// chart types and 10 components for the apps that need them, which would add
// ~1MB to this single-file bundle (already ~1.35MB) for one line chart.
// Only LineChart + the components that chart actually uses are pulled in.

import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  DataZoomInsideComponent,
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { ECHARTS_LIGHT_MINI } from "../../themes/echarts-light-mini";
import { ECHARTS_DARK_MINI } from "../../themes/echarts-dark-mini";

echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  // Inside-only: wheel/pinch zoom. Slider zoom bars are banned across every
  // chart surface in this codebase.
  DataZoomInsideComponent,
  MarkLineComponent,
  CanvasRenderer,
]);

echarts.registerTheme("cerebro-light-mini", ECHARTS_LIGHT_MINI);
echarts.registerTheme("cerebro-dark-mini", ECHARTS_DARK_MINI);

export default echarts;
