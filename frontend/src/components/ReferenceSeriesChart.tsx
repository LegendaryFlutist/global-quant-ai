import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent, DataZoomComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { MarketObservationPoint } from "../types";

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

/** Draw provider-published scalar observations without inventing candle OHLC values. */
export default function ReferenceSeriesChart({
  observations,
  unit,
}: {
  observations: MarketObservationPoint[];
  unit: string;
}) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current, "dark", { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "#111927",
      animation: false,
      grid: { left: 72, right: 35, top: 32, bottom: 66 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: observations.map((point) => new Date(point.observed_at_ms).toISOString().slice(0, 10)),
        axisLabel: { color: "#8392a9" },
        axisLine: { lineStyle: { color: "#303e54" } },
      },
      yAxis: {
        type: "value",
        name: unit,
        scale: true,
        splitLine: { lineStyle: { color: "#202b3d" } },
        axisLabel: { color: "#8392a9" },
      },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
      series: [{
        name: `官方日度参考值 (${unit})`,
        type: "line",
        showSymbol: false,
        connectNulls: false,
        lineStyle: { color: "#63b3ed", width: 2 },
        data: observations.map((point) => Number(point.value)),
      }],
    });
    /** Keep the chart fitted to its parent without retaining observers after unmount. */
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.dispose();
    };
  }, [observations, unit]);
  return <div ref={element} role="img" aria-label={`官方日度参考值折线图，单位 ${unit}`} style={{ width: "100%", height: 520 }} />;
}
