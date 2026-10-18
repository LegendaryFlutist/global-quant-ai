import { describe, it, expect } from "vitest";
import { movingAverage } from "./PriceChart";
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
