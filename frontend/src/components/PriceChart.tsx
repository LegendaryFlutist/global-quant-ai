import { useEffect, useRef } from "react";
import * as echarts from "echarts/core";
import { CandlestickChart, BarChart, LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Candle } from "../types";
import { date } from "../services/api";
echarts.use([
  CandlestickChart,
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  DataZoomComponent,
  AxisPointerComponent,
  LegendComponent,
  CanvasRenderer,
]);
/** 只用截至当前行的已收盘价格计算完整窗口均值；预热不足返回null。 */
export function movingAverage(
  rows: Candle[],
  period: number,
): (number | null)[] {
  return rows.map((_, i) =>
    i < period - 1
      ? null
      : rows
          .slice(i - period + 1, i + 1)
          .reduce((total, r) => total + Number(r.close), 0) / period,
  );
}
/** 呈现真实蜡烛、成交量与可选均线；只用于展示，卸载时释放图表和尺寸监听。 */
export default function PriceChart({
  candles,
  timezone,
  showMA,
}: {
  candles: Candle[];
  timezone: string;
  showMA: boolean;
}) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current, "dark", { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "#111927",
      animation: false,
      legend: {
        data: ["价格", ...(showMA ? ["MA20", "MA60"] : [])],
        top: 8,
        textStyle: { color: "#aab8ce" },
      },
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 72, right: 35, top: 48, height: "61%" },
        { left: 72, right: 35, top: "77%", height: "12%" },
      ],
      xAxis: [
        {
          type: "category",
          data: candles.map((c) => date(c.open_time, timezone)),
          boundaryGap: true,
          axisLabel: { color: "#8392a9" },
          axisLine: { lineStyle: { color: "#303e54" } },
          min: "dataMin",
          max: "dataMax",
        },
        {
          type: "category",
          gridIndex: 1,
          data: candles.map((c) => date(c.open_time, timezone)),
          axisLabel: { show: false },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          scale: true,
          splitLine: { lineStyle: { color: "#202b3d" } },
          axisLabel: { color: "#8392a9" },
        },
        {
          scale: true,
          gridIndex: 1,
          splitNumber: 2,
          axisLabel: { color: "#8392a9" },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: [0, 1],
          start: Math.max(0, 100 - 12000 / Math.max(1, candles.length)),
          end: 100,
        },
        {
          type: "slider",
          xAxisIndex: [0, 1],
          bottom: 8,
          height: 20,
          borderColor: "#27354a",
          textStyle: { color: "#8796ae" },
        },
      ],
      series: [
        {
          name: "价格",
          type: "candlestick",
          data: candles.map((c) => [
            Number(c.open),
            Number(c.close),
            Number(c.low),
            Number(c.high),
          ]),
          itemStyle: {
            color: "#38cda4",
            color0: "#ed7583",
            borderColor: "#38cda4",
            borderColor0: "#ed7583",
          },
        },
        ...(showMA
          ? [
              {
                name: "MA20",
                type: "line",
                data: movingAverage(candles, 20),
                showSymbol: false,
                lineStyle: { width: 1.3, color: "#ffcf78" },
                itemStyle: { color: "#ffcf78" },
              },
              {
                name: "MA60",
                type: "line",
                data: movingAverage(candles, 60),
                showSymbol: false,
                lineStyle: { width: 1.3, color: "#8bafff" },
                itemStyle: { color: "#8bafff" },
              },
            ]
          : []),
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: candles.map((c) => ({
            value: Number(c.volume),
            itemStyle: {
              color: Number(c.close) >= Number(c.open) ? "#268c76" : "#9e4e63",
            },
          })),
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [candles, timezone, showMA]);
  return (
    <div
      ref={element}
      className="price-chart"
      aria-label="真实历史蜡烛图与成交量"
      data-testid="price-chart"
      data-candles={candles.length}
    />
  );
}
