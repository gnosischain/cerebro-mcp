import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";

import "../lib/echarts-setup";
import { getWatermarkGraphic } from "../assets/watermark";
import { useTheme } from "../hooks/useTheme";
import { isNumberDisplay, type ChartSpec } from "../types";
import { NumberDisplay } from "./NumberDisplay";
import { ErrorBoundary } from "./ErrorBoundary";

interface Props {
  chartId: string;
  spec: ChartSpec;
  title?: string;
}

function ChartCardInner({ chartId, spec, title }: Props) {
  const { isDark } = useTheme();

  if (isNumberDisplay(spec)) {
    return (
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-base)",
          boxShadow: "var(--shadow-sm)",
          overflow: "hidden",
          margin: "1rem 0",
        }}
      >
        {title && (
          <div
            style={{
              padding: "0.75rem 1rem 0",
              fontSize: "0.875rem",
              fontWeight: 600,
              color: "var(--text-primary)",
            }}
          >
            {title}
          </div>
        )}
        <NumberDisplay spec={spec} />
      </div>
    );
  }

  const echartsOption = useMemo(() => {
    const opt = { ...(spec as EChartsOption) };
    opt.graphic = getWatermarkGraphic(isDark);
    opt.animation = true;
    opt.animationDuration = 1000;
    opt.animationEasing = "cubicOut";
    opt.toolbox = {
      show: true,
      right: 16,
      top: 8,
      feature: {
        saveAsImage: {
          title: "Save as image",
          pixelRatio: 2,
        },
        dataView: {
          title: "View data",
          lang: ["Data view", "Close", "Refresh"],
          readOnly: true,
        },
      },
      iconStyle: {
        borderColor: isDark
          ? "rgba(255,255,255,0.5)"
          : "rgba(0,0,0,0.4)",
      },
    };
    return opt;
  }, [spec, isDark]);

  return (
    <div
      id={`chart-${chartId}`}
      style={{
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-base)",
        boxShadow: "var(--shadow-sm)",
        overflow: "hidden",
        margin: "1rem 0",
        transition: "border-color 0.2s, box-shadow 0.2s",
      }}
    >
      {title && (
        <div
          style={{
            padding: "0.75rem 1rem 0",
            fontSize: "0.875rem",
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {title}
        </div>
      )}
      <ReactECharts
        option={echartsOption}
        theme={isDark ? "cerebro-dark" : "cerebro-light"}
        style={{ width: "100%", height: "400px" }}
        notMerge
      />
    </div>
  );
}

export function ChartCard(props: Props) {
  return (
    <ErrorBoundary fallbackLabel="Chart">
      <ChartCardInner {...props} />
    </ErrorBoundary>
  );
}
