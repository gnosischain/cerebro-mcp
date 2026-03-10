import * as echarts from "echarts/core";
import { LineChart, BarChart, PieChart } from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  GraphicComponent,
  DataZoomComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

import { ECHARTS_LIGHT } from "../themes/echarts-light";
import { ECHARTS_DARK } from "../themes/echarts-dark";

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  GraphicComponent,
  DataZoomComponent,
  CanvasRenderer,
]);

echarts.registerTheme("cerebro-light", ECHARTS_LIGHT);
echarts.registerTheme("cerebro-dark", ECHARTS_DARK);

export default echarts;
