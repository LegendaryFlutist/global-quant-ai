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
import type { Candle, StructureEvent, StructureTimeframe } from "../types";
import { date } from "../services/api";
import { bollingerBands, dailyMovingAverage, macd, macdDivergences, movingAverage, tdSetup, threePivotTrendlines } from "./chartIndicators";
export { movingAverage } from "./chartIndicators";
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
/** 呈现真实蜡烛、成交量与可选均线；只用于展示，卸载时释放图表和尺寸监听。 */
export default function PriceChart({
  candles,
  timezone,
  showMA,
  structure,
  events = [],
  maPeriods,
  showBollinger,
  showMACD,
  showTD,
}: {
  candles: Candle[];
  timezone: string;
  showMA: boolean;
  structure?: StructureTimeframe;
  events?: StructureEvent[];
  maPeriods: number[];
  showBollinger: boolean;
  showMACD: boolean;
  showTD: boolean;
}) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const chart = echarts.init(element.current, "dark", { renderer: "canvas" });
    const analysisCandles = candles.filter((candle) => candle.is_closed !== false);
    const bands = bollingerBands(candles);
    const macdValues = macd(candles);
    const td = tdSetup(candles);
    const macdDivergenceEvents = showMACD ? macdDivergences(analysisCandles) : [];
    /** Find the first candle closing at or after an event time in O(log n) on sorted UTC candles. */
    function candleAtOrAfter(time: number) {
      let low = 0;
      let high = candles.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (candles[middle].close_time < time) low = middle + 1;
        else high = middle;
      }
      return candles[low];
    }
    const lineSeries = (["support", "resistance"] as const).flatMap((name) => {
      const line = structure?.trend_lines?.[name];
      if (!line || line.anchors.length < 2) return [];
      const [a, b] = line.anchors;
      const span = b.time - a.time;
      if (span <= 0) return [];
      return [{
        name: name === "support" ? "确认支撑趋势线" : "确认压力趋势线",
        type: "line" as const,
        data: candles.map((c) => c.open_time < a.time ? null :
          Number(a.price) + (Number(b.price) - Number(a.price)) * (c.close_time - a.time) / span),
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: 1.8, type: "dashed" as const, color: name === "support" ? "#46c8a6" : "#ee7485" },
        itemStyle: { color: name === "support" ? "#46c8a6" : "#ee7485" },
      }];
    });
    const localTrendLines = threePivotTrendlines(analysisCandles).map((line) => ({
      name: line.kind === "support" ? "三点支撑趋势线" : "三点压力趋势线",
      type: "line" as const,
      data: candles.map((_, index) => index < line.points[0].index ? null :
        line.points[0].price + line.slope * (index - line.points[0].index)),
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 2, color: line.kind === "support" ? "#46c8a6" : "#ee7485" },
    }));
    const tdMarkers = showTD ? (["buy", "sell"] as const).flatMap((direction) => {
      const points = td.flatMap((setup, index) => setup.count === 0 || setup.direction !== direction ? [] : [
        [date(candles[index].open_time, timezone), Number(candles[index].close), setup.count],
      ]);
      return points.length ? [{
        name: direction === "buy" ? "TD买入序列" : "TD卖出序列",
        type: "scatter" as const,
        data: points,
        symbolSize: (value: number[]) => value[2] === 9 ? 8 : 5,
        itemStyle: { color: direction === "buy" ? "#53c7a0" : "#e7838f" },
        label: { show: true, formatter: (params: { value: (string | number)[] }) => String(params.value[2]), position: direction === "buy" ? "bottom" : "top", color: "#dbe6f7", fontSize: 8 },
      }] : [];
    }) : [];
    const wavePoints = (structure?.wave?.points ?? []).flatMap((point, index) => {
      const target = candleAtOrAfter(Number(point.time));
      return target ? [{
        time: date(target.open_time, timezone),
        price: Number(point.price),
        label: structure?.wave?.labels?.[index] ?? "波",
      }] : [];
    });
    // Keep ECharts series count bounded: a separate series for every event freezes browsers on long histories.
    const eventGroups = [
      { name: "趋势破位", match: (event: StructureEvent) => event.kind === "TRENDLINE_BREAK", label: "破位", color: "#f2ae52", symbol: "diamond" as const, position: "top" as const },
      { name: "道氏123", match: (event: StructureEvent) => event.kind === "DOW123_CONFIRMED", label: "123", color: "#64a7ff", symbol: "circle" as const, position: "bottom" as const },
      { name: "底背离", match: (event: StructureEvent) => event.kind === "BULLISH_RSI_DIVERGENCE", label: "底背离", color: "#47d1a2", symbol: "circle" as const, position: "bottom" as const },
      { name: "顶背离", match: (event: StructureEvent) => event.kind === "BEARISH_RSI_DIVERGENCE", label: "顶背离", color: "#d87cf2", symbol: "circle" as const, position: "top" as const },
    ];
    const markers = eventGroups.flatMap((group) => {
      const points = events.filter(group.match).flatMap((event) => {
        const target = candleAtOrAfter(event.occurred_at);
        return target ? [[date(target.open_time, timezone), Number(target.close)]] : [];
      });
      return points.length ? [{
        name: group.name,
        type: "scatter" as const,
        data: points,
        symbol: group.symbol,
        symbolSize: 9,
        itemStyle: { color: group.color },
        label: { show: true, formatter: group.label, position: group.position, color: "#dbe6f7", fontSize: 9 },
      }] : [];
    });
    const macdMarkers = (["bullish", "bearish"] as const).flatMap((kind) => {
      const points = macdDivergenceEvents.filter((event) => event.kind === kind).map((event) => [
        date(analysisCandles[event.index].open_time, timezone), event.price,
      ]);
      return points.length ? [{
        name: kind === "bullish" ? "MACD底背驰" : "MACD顶背驰",
        type: "scatter" as const,
        data: points,
        symbol: "diamond",
        symbolSize: 10,
        itemStyle: { color: kind === "bullish" ? "#47d1a2" : "#ef8090" },
        label: { show: true, formatter: kind === "bullish" ? "MACD底背" : "MACD顶背", position: kind === "bullish" ? "bottom" : "top", color: "#dbe6f7", fontSize: 8 },
      }] : [];
    });
    const waveMarkers = wavePoints.length ? [{
      name: "波浪候选",
      type: "scatter" as const,
      data: wavePoints.map((point) => [point.time, point.price, point.label]),
      symbol: "diamond",
      symbolSize: 9,
      itemStyle: { color: "#c38cff" },
      label: { show: true, formatter: (params: { value: (string | number)[] }) => String(params.value[2]), position: "top", color: "#d6b8ff", fontSize: 10 },
    }] : [];
    chart.setOption({
      backgroundColor: "#111927",
      animation: false,
      legend: {
        data: ["价格", ...(showMA ? maPeriods.map((period) => `MA${period}日`) : []), ...(showBollinger ? ["布林上轨", "布林中轨", "布林下轨"] : []), ...(showMACD ? ["MACD", "Signal", "柱"] : []), ...lineSeries.map((s) => s.name), ...localTrendLines.map((s) => s.name), ...markers.map((m) => m.name), ...macdMarkers.map((m) => m.name), ...tdMarkers.map((m) => m.name), ...waveMarkers.map((m) => m.name)],
        top: 8,
        textStyle: { color: "#aab8ce" },
      },
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 72, right: 35, top: 48, height: showMACD ? "48%" : "61%" },
        ...(showMACD ? [{ left: 72, right: 35, top: "66%", height: "13%" }] : []),
        { left: 72, right: 35, top: showMACD ? "84%" : "77%", height: "10%" },
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
        ...(showMACD ? [{
          type: "category" as const,
          gridIndex: 1,
          data: candles.map((c) => date(c.open_time, timezone)),
          axisLabel: { show: false },
          axisTick: { show: false },
        }] : []),
        {
          type: "category",
          gridIndex: showMACD ? 2 : 1,
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
        ...(showMACD ? [{
          scale: true,
          gridIndex: 1,
          splitNumber: 2,
          axisLabel: { color: "#8392a9" },
          splitLine: { show: false },
        }] : []),
        {
          scale: true,
          gridIndex: showMACD ? 2 : 1,
          splitNumber: 2,
          axisLabel: { color: "#8392a9" },
          splitLine: { show: false },
        },
      ],
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: showMACD ? [0, 1, 2] : [0, 1],
          start: Math.max(0, 100 - 12000 / Math.max(1, candles.length)),
          end: 100,
        },
        {
          type: "slider",
          xAxisIndex: showMACD ? [0, 1, 2] : [0, 1],
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
        ...(showMA ? maPeriods.map((period, index) => ({
          name: `MA${period}日`,
          type: "line" as const,
          data: dailyMovingAverage(candles, period),
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.2, color: ["#ffcf78", "#8bafff", "#da91ff", "#5ac9e8", "#f0965a"][index % 5] },
        })) : []),
        ...(showBollinger ? [
          { name: "布林上轨", type: "line" as const, data: bands.upper, showSymbol: false, lineStyle: { width: 1, type: "dashed" as const, color: "#9a85da" } },
          { name: "布林中轨", type: "line" as const, data: bands.middle, showSymbol: false, lineStyle: { width: 1, color: "#9a85da" } },
          { name: "布林下轨", type: "line" as const, data: bands.lower, showSymbol: false, lineStyle: { width: 1, type: "dashed" as const, color: "#9a85da" } },
        ] : []),
        ...lineSeries,
        ...localTrendLines,
        ...markers,
        ...macdMarkers,
        ...tdMarkers,
        ...waveMarkers,
        ...(showMACD ? [
          { name: "MACD", type: "line" as const, xAxisIndex: 1, yAxisIndex: 1, data: macdValues.line, showSymbol: false, lineStyle: { color: "#65a9ff", width: 1.2 } },
          { name: "Signal", type: "line" as const, xAxisIndex: 1, yAxisIndex: 1, data: macdValues.signal, showSymbol: false, lineStyle: { color: "#ffbd68", width: 1.2 } },
          { name: "柱", type: "bar" as const, xAxisIndex: 1, yAxisIndex: 1, data: macdValues.histogram, itemStyle: { color: "#7a8ba6" } },
        ] : []),
        {
          name: "成交量",
          type: "bar",
          xAxisIndex: showMACD ? 2 : 1,
          yAxisIndex: showMACD ? 2 : 1,
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
  }, [candles, timezone, showMA, structure, events, maPeriods, showBollinger, showMACD, showTD]);
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
