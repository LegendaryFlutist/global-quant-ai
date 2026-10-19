import type { Candle } from "../types";

/** Calculate causal bar-count moving averages; periods are candle counts, not days. */
export function movingAverage(rows: Candle[], period: number): (number | null)[] {
  if (!Number.isInteger(period) || period < 1) return rows.map(() => null);
  let total = 0;
  return rows.map((row, index) => {
    total += Number(row.close);
    if (index >= period) total -= Number(rows[index - period].close);
    return index < period - 1 ? null : total / period;
  });
}

/** Map a UTC daily-close moving average back to each chart bar for cross-timeframe 5/10/20-day lines. */
export function dailyMovingAverage(rows: Candle[], period: number): (number | null)[] {
  if (!Number.isInteger(period) || period < 1) return rows.map(() => null);
  const byDay = new Map<number, { index: number; close: number }>();
  rows.forEach((row, index) => byDay.set(Math.floor(row.open_time / 86_400_000), { index, close: Number(row.close) }));
  const daily = [...byDay.entries()].sort(([left], [right]) => left - right);
  let total = 0;
  const values = new Map<number, number | null>();
  daily.forEach(([day, item], index) => {
    total += item.close;
    if (index >= period) total -= daily[index - period][1].close;
    values.set(day, index < period - 1 ? null : total / period);
  });
  return rows.map((row) => values.get(Math.floor(row.open_time / 86_400_000)) ?? null);
}

/** Calculate rolling Bollinger bands from closed or display-only live closes. */
export function bollingerBands(
  rows: Candle[],
  period = 20,
  deviations = 2,
): { middle: (number | null)[]; upper: (number | null)[]; lower: (number | null)[] } {
  const middle = movingAverage(rows, period);
  const upper: (number | null)[] = [];
  const lower: (number | null)[] = [];
  rows.forEach((_, index) => {
    const average = middle[index];
    if (average === null) {
      upper.push(null);
      lower.push(null);
      return;
    }
    const sample = rows.slice(index - period + 1, index + 1).map((row) => Number(row.close));
    const variance = sample.reduce((sum, value) => sum + (value - average) ** 2, 0) / period;
    const spread = Math.sqrt(variance) * deviations;
    upper.push(average + spread);
    lower.push(average - spread);
  });
  return { middle, upper, lower };
}

/** Calculate MACD (12,26,9 by default) with null warm-up values. */
export function macd(
  rows: Candle[],
  fast = 12,
  slow = 26,
  signalPeriod = 9,
): { line: (number | null)[]; signal: (number | null)[]; histogram: (number | null)[] } {
  if (![fast, slow, signalPeriod].every((period) => Number.isInteger(period) && period > 0) || fast >= slow) {
    return { line: rows.map(() => null), signal: rows.map(() => null), histogram: rows.map(() => null) };
  }
  const closes = rows.map((row) => Number(row.close));
  const ema = (period: number) => {
    const output: (number | null)[] = [];
    const alpha = 2 / (period + 1);
    let value: number | null = null;
    closes.forEach((close, index) => {
      value = value === null ? close : close * alpha + value * (1 - alpha);
      output.push(index < period - 1 ? null : value);
    });
    return output;
  };
  const fastEma = ema(fast);
  const slowEma = ema(slow);
  const line = fastEma.map((value, index) => value === null || slowEma[index] === null ? null : value - (slowEma[index] as number));
  const signal: (number | null)[] = [];
  const alpha = 2 / (signalPeriod + 1);
  let signalValue: number | null = null;
  line.forEach((value, index) => {
    if (value === null) signal.push(null);
    else {
      signalValue = signalValue === null ? value : value * alpha + signalValue * (1 - alpha);
      signal.push(index < slow + signalPeriod - 2 ? null : signalValue);
    }
  });
  return {
    line,
    signal,
    histogram: line.map((value, index) => value === null || signal[index] === null ? null : value - (signal[index] as number)),
  };
}

/** Emit MACD line divergences only after a unique two-sided price pivot is confirmed. */
export function macdDivergences(
  rows: Candle[],
  width = 2,
): { kind: "bullish" | "bearish"; index: number; confirmedIndex: number; price: number; macd: number }[] {
  const line = macd(rows).line;
  const previous: { high?: { price: number; oscillator: number }; low?: { price: number; oscillator: number } } = {};
  const events: { kind: "bullish" | "bearish"; index: number; confirmedIndex: number; price: number; macd: number }[] = [];
  for (let index = width; index < rows.length - width; index += 1) {
    const window = rows.slice(index - width, index + width + 1);
    const high = Number(rows[index].high);
    const low = Number(rows[index].low);
    const closes = line[index];
    if (closes === null) continue;
    const pivots: { side: "high" | "low"; price: number }[] = [];
    if (window.filter((row) => Number(row.high) === high).length === 1 && high === Math.max(...window.map((row) => Number(row.high)))) pivots.push({ side: "high", price: high });
    if (window.filter((row) => Number(row.low) === low).length === 1 && low === Math.min(...window.map((row) => Number(row.low)))) pivots.push({ side: "low", price: low });
    if (pivots.length !== 1) continue;
    const pivot = pivots[0];
    const prior = previous[pivot.side];
    if (prior && pivot.side === "low" && pivot.price < prior.price && closes > prior.oscillator) {
      events.push({ kind: "bullish", index, confirmedIndex: index + width, price: pivot.price, macd: closes });
    } else if (prior && pivot.side === "high" && pivot.price > prior.price && closes < prior.oscillator) {
      events.push({ kind: "bearish", index, confirmedIndex: index + width, price: pivot.price, macd: closes });
    }
    previous[pivot.side] = { price: pivot.price, oscillator: closes };
  }
  return events;
}

/** Find three distinct confirmed fractal pivots that align within a relative residual tolerance. */
export function threePivotTrendlines(
  rows: Candle[],
  width = 2,
  tolerance = 0.015,
): { kind: "support" | "resistance"; points: { index: number; price: number; time: number }[]; slope: number }[] {
  const output: { kind: "support" | "resistance"; points: { index: number; price: number; time: number }[]; slope: number }[] = [];
  for (const [kind, field] of [["support", "low"], ["resistance", "high"]] as const) {
    const pivots: { index: number; price: number; time: number }[] = [];
    for (let index = width; index < rows.length - width; index += 1) {
      const price = Number(rows[index][field]);
      const neighbors = rows.slice(index - width, index + width + 1).map((row) => Number(row[field]));
      if (neighbors.filter((value) => value === price).length !== 1) continue;
      if (price === (kind === "support" ? Math.min(...neighbors) : Math.max(...neighbors))) {
        pivots.push({ index, price, time: rows[index].close_time });
      }
    }
    const points = pivots.slice(-3);
    if (points.length !== 3) continue;
    const meanX = points.reduce((sum, point) => sum + point.index, 0) / points.length;
    const meanY = points.reduce((sum, point) => sum + point.price, 0) / points.length;
    const denominator = points.reduce((sum, point) => sum + (point.index - meanX) ** 2, 0);
    const slope = points.reduce((sum, point) => sum + (point.index - meanX) * (point.price - meanY), 0) / denominator;
    const intercept = meanY - slope * meanX;
    const residual = Math.max(...points.map((point) => Math.abs(point.price - (intercept + slope * point.index)) / point.price));
    if (residual <= tolerance) output.push({ kind, points, slope });
  }
  return output;
}

/** Count consecutive DeMark-style close-vs-four-bars setup observations, resetting on a failed comparison. */
export function tdSetup(rows: Candle[]): { direction: "buy" | "sell"; count: number }[] {
  let buy = 0;
  let sell = 0;
  return rows.map((row, index) => {
    if (index < 4) return { direction: "buy", count: 0 };
    const close = Number(row.close);
    const reference = Number(rows[index - 4].close);
    if (close < reference) {
      buy = buy >= 9 ? 1 : buy + 1;
      sell = 0;
      return { direction: "buy", count: buy };
    }
    if (close > reference) {
      sell = sell >= 9 ? 1 : sell + 1;
      buy = 0;
      return { direction: "sell", count: sell };
    }
    buy = 0;
    sell = 0;
    return { direction: "buy", count: 0 };
  });
}
