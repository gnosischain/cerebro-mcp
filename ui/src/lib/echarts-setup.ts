import * as echarts from "echarts/core";
import {
  LineChart,
  BarChart,
  PieChart,
  ScatterChart,
  HeatmapChart,
  GaugeChart,
  TreemapChart,
  SankeyChart,
  GraphChart,
  FunnelChart,
  CandlestickChart,
} from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  GraphicComponent,
  DataZoomComponent,
  ToolboxComponent,
  CalendarComponent,
  VisualMapComponent,
  // Mid/reference price markers on the CoW pair-depth chart.
  MarkLineComponent,
} from "echarts/components";
import { CanvasRenderer, SVGRenderer } from "echarts/renderers";

import { ECHARTS_LIGHT } from "../themes/echarts-light";
import { ECHARTS_DARK } from "../themes/echarts-dark";
import { ECHARTS_LIGHT_MINI } from "../themes/echarts-light-mini";
import { ECHARTS_DARK_MINI } from "../themes/echarts-dark-mini";

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  ScatterChart,
  HeatmapChart,
  GaugeChart,
  TreemapChart,
  SankeyChart,
  GraphChart,
  FunnelChart,
  CandlestickChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  GraphicComponent,
  DataZoomComponent,
  ToolboxComponent,
  CalendarComponent,
  VisualMapComponent,
  MarkLineComponent,
  CanvasRenderer,
  SVGRenderer,
]);

echarts.registerTheme("cerebro-light", ECHARTS_LIGHT);
echarts.registerTheme("cerebro-dark", ECHARTS_DARK);
echarts.registerTheme("cerebro-light-mini", ECHARTS_LIGHT_MINI);
echarts.registerTheme("cerebro-dark-mini", ECHARTS_DARK_MINI);

export default echarts;
