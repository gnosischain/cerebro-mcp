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
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { ECHARTS_LIGHT } from "../themes/echarts-light";
import { ECHARTS_DARK } from "../themes/echarts-dark";

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
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  GraphicComponent,
  DataZoomComponent,
  ToolboxComponent,
  CalendarComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

echarts.registerTheme("cerebro-light", ECHARTS_LIGHT);
echarts.registerTheme("cerebro-dark", ECHARTS_DARK);

export default echarts;
