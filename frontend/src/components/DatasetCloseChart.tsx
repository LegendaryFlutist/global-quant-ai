import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { DatasetBar } from "../services/datasets";

echarts.use([LineChart, DataZoomComponent, GridComponent, TooltipComponent, CanvasRenderer]);

/** Build a close-only display without aggregating sessions or synthesizing missing observations. */
export function datasetCloseOption(bars: DatasetBar[], interval: string, currency: string) {
  return {
    backgroundColor: "#111927",
    animation: false,
    grid: { left: 80, right: 30, top: 40, bottom: 75 },
    tooltip: { trigger: "axis", renderMode: "richText" },
    xAxis: {
      type: "category",
      data: bars.map((bar) => interval === "1d" ? bar.session_date : new Date(bar.open_time).toISOString()),
      axisLabel: { color: "#8392a9" },
    },
    yAxis: { type: "value", name: currency, scale: true, splitLine: { lineStyle: { color: "#202b3d" } } },
    dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
    series: [{
      name: `收盘价（${currency}）`, type: "line", showSymbol: false, connectNulls: false,
      lineStyle: { color: "#80c9c7", width: 2 },
      data: bars.map((bar) => bar.close.trim() && Number.isFinite(Number(bar.close)) ? Number(bar.close) : null),
    }],
  };
}

/** Render one frozen version's native closes; decimal conversion is used for chart display only. */
export default function DatasetCloseChart({ bars, interval, currency }: { bars: DatasetBar[]; interval: string; currency: string }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current, "dark", { renderer: "canvas" });
    chart.setOption(datasetCloseOption(bars, interval, currency));
    /** Resize the canvas without querying data or altering the pinned dataset version. */
    const resize = () => chart.resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [bars, interval, currency]);
  return <div ref={element} role="img" aria-label={`不可变数据版本的 ${interval} 收盘价，计价币 ${currency}`} style={{ width: "100%", height: 420 }} />;
}
