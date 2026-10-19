import { describe, it, expect } from "vitest";
import { movingAverage } from "./PriceChart";
import { bollingerBands, dailyMovingAverage, macd, macdDivergences, tdSetup, threePivotTrendlines } from "./chartIndicators";
import type { Candle } from "../types";
const candles = [1, 2, 3, 4, 100].map(
  (n, i) =>
    ({
      open_time: i,
      close_time: i,
      open: String(n),
      high: String(n),
      low: String(n),
      close: String(n),
      volume: "1",
    }) satisfies Candle,
);
describe("causal moving average", () => {
  it("requires a full past window", () =>
    expect(movingAverage(candles, 3)).toEqual([null, null, 2, 3, 107 / 3]));
  it("future rows do not change the prefix", () =>
    expect(movingAverage(candles, 3).slice(0, 4)).toEqual(
      movingAverage(candles.slice(0, 4), 3),
    ));
});

describe("chart studies", () => {
  it("projects UTC daily moving averages across intraday bars", () => {
    const day = 86_400_000;
    const bars = [10, 12, 20, 22].map((close, index) => ({
      ...candles[0], open_time: Math.floor(index / 2) * day + (index % 2) * 900_000,
      close_time: Math.floor(index / 2) * day + (index % 2 + 1) * 900_000 - 1,
      open: String(close), high: String(close), low: String(close), close: String(close),
    }));
    expect(dailyMovingAverage(bars, 2)).toEqual([null, null, 17, 17]);
  });

  it("uses a complete window for Bollinger bands and has a causal MACD prefix", () => {
    const bands = bollingerBands(candles, 3, 2);
    expect(bands.middle.slice(0, 2)).toEqual([null, null]);
    expect(bands.middle[2]).toBe(2);
    const full = macd(candles, 2, 3, 2);
    const prefix = macd(candles.slice(0, 4), 2, 3, 2);
    expect(full.line.slice(0, 4)).toEqual(prefix.line);
    expect(full.histogram).toHaveLength(candles.length);
  });

  it("confirms MACD divergence only after the right-side pivot bars arrive", () => {
    const rows = Array.from({ length: 100 }, (_, index) => {
      const close = 100 + Math.sin(index / 4) * (5 + index / 100);
      return { ...candles[0], open_time: index, close_time: index, open: String(close),
        high: String(close + 1), low: String(close - 1), close: String(close) };
    });
    const full = macdDivergences(rows);
    const prefix = macdDivergences(rows.slice(0, 80));
    expect(full.filter((event) => event.confirmedIndex < 80)).toEqual(prefix);
  });

  it("marks a nine-bar TD setup and requires three independent pivots for a line", () => {
    const descending = Array.from({ length: 14 }, (_, index) => ({
      ...candles[0], open_time: index, close_time: index, open: String(30 - index),
      high: String(31 - index), low: String(29 - index), close: String(30 - index),
    }));
    expect(tdSetup(descending)[12]).toEqual({ direction: "buy", count: 9 });

    const lows = [100, 99, 97, 99, 100, 100, 99, 98, 99, 100, 101, 100, 99, 100, 101];
    const pivots = lows.map((low, index) => ({
      ...candles[0], open_time: index, close_time: index, low: String(low),
      high: String(low + 3), open: String(low + 1), close: String(low + 2),
    }));
    expect(threePivotTrendlines(pivots).find((line) => line.kind === "support")?.points.map((point) => point.index)).toEqual([2, 7, 12]);
    expect(threePivotTrendlines(pivots.slice(0, 8)).some((line) => line.kind === "support")).toBe(false);
  });
});
