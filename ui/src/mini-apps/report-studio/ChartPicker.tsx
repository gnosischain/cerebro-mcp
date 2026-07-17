// Composer chart picker: the server-wide chart-record registry (2h TTL),
// lazily hydrating a small ECharts thumbnail per record on demand.

import { useState } from "react";
import type { EChartsOption } from "echarts";
import ReactECharts from "echarts-for-react";
import { useTheme } from "../../hooks/useTheme";
import type { ChartRecord } from "./types";

type CallTool = <T = unknown>(
  name: string,
  args: Record<string, unknown>,
) => Promise<T | null>;

interface ChartPickerProps {
  records: ChartRecord[];
  selected: Set<string>;
  onToggle: (chartId: string) => void;
  callTool: CallTool;
  onAskAgent?: () => void;
}

/** Strip labels/legend/toolbox for a ~160px thumbnail. */
function thumbnailOption(option: EChartsOption): EChartsOption {
  return {
    ...option,
    legend: { show: false },
    toolbox: { show: false },
    tooltip: { show: false },
    grid: { left: 4, right: 4, top: 6, bottom: 4 },
    xAxis: Array.isArray(option.xAxis)
      ? option.xAxis.map((a) => ({ ...a, show: false }))
      : option.xAxis
        ? { ...option.xAxis, show: false }
        : undefined,
    yAxis: Array.isArray(option.yAxis)
      ? option.yAxis.map((a) => ({ ...a, show: false }))
      : option.yAxis
        ? { ...option.yAxis, show: false }
        : undefined,
  };
}

export function ChartPicker({
  records,
  selected,
  onToggle,
  callTool,
  onAskAgent,
}: ChartPickerProps) {
  const { isDark } = useTheme();
  const [thumbs, setThumbs] = useState<Record<string, EChartsOption | "loading" | "kpi">>({});

  const hydrate = async (chartId: string, chartType: string) => {
    if (thumbs[chartId]) return;
    if (chartType === "numberDisplay") {
      setThumbs((prev) => ({ ...prev, [chartId]: "kpi" }));
      return;
    }
    setThumbs((prev) => ({ ...prev, [chartId]: "loading" }));
    try {
      const record = await callTool<{ ok: boolean; option?: EChartsOption }>(
        "get_session_chart",
        { chart_id: chartId },
      );
      if (record?.ok && record.option) {
        setThumbs((prev) => ({ ...prev, [chartId]: thumbnailOption(record.option!) }));
      }
    } catch {
      // thumbnail is best-effort; the record row still works
    }
  };

  if (records.length === 0) {
    return (
      <div className="rst-empty">
        No recent chart records (server-wide, 2h TTL) — ask the agent to
        generate charts first.
        {onAskAgent && (
          <button type="button" className="rst-toggle" onClick={onAskAgent}>
            Ask the agent
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="rst-picker" role="listbox" aria-label="Chart records">
      {records.map((record) => {
        const checked = selected.has(record.chart_id);
        const thumb = thumbs[record.chart_id];
        return (
          <label
            key={record.chart_id}
            className={`rst-picker-item${checked ? " is-checked" : ""}`}
            onMouseEnter={() => void hydrate(record.chart_id, record.chart_type)}
          >
            <input
              type="checkbox"
              checked={checked}
              onChange={() => onToggle(record.chart_id)}
            />
            <span className="rst-picker-body">
              <span className="rst-picker-title">
                {record.title || record.chart_id}
              </span>
              <span className="rst-picker-meta">
                <code>{record.chart_id}</code> · {record.chart_type}
                {record.source_model ? ` · ${record.source_model}` : ""}
              </span>
              {thumb && thumb !== "loading" && thumb !== "kpi" && (
                <span className="rst-picker-thumb">
                  <ReactECharts
                    option={thumb}
                    theme={isDark ? "cerebro-dark" : "cerebro-light"}
                    style={{ height: 90, width: "100%" }}
                    opts={{ renderer: "canvas" }}
                  />
                </span>
              )}
              {thumb === "kpi" && (
                <span className="rst-picker-kpi">KPI counter</span>
              )}
            </span>
          </label>
        );
      })}
    </div>
  );
}
