import { describe, expect, it } from "vitest";
import { datasetDateError, retainDatasetSelection, type DatasetVersion } from "./datasets";
import { datasetCloseOption } from "../components/DatasetCloseChart";

describe("dataset version selection and collection boundaries", () => {
  it("validates real UTC dates and bounded history without accepting future periods", () => {
    expect(datasetDateError("2025-02-29", "2025-03-01", "2026-10-21")).toBe("请填写有效的开始和结束日期。");
    expect(datasetDateError("2026-10-20", "2026-10-19", "2026-10-21")).toBe("结束日期不能早于开始日期。");
    expect(datasetDateError("2010-01-01", "2026-10-21", "2026-10-21")).toBe("单次日期范围不能超过 3650 天。");
    expect(datasetDateError("2026-10-20", "2026-10-22", "2026-10-21")).toBe("结束日期不能晚于今天（UTC）。");
    expect(datasetDateError("2024-02-29", "2026-10-21", "2026-10-21")).toBeNull();
  });

  it("keeps the inspected historical version when newer versions arrive", () => {
    const versions = [{ id: "new" }, { id: "pinned" }] as DatasetVersion[];
    expect(retainDatasetSelection(versions, "pinned")).toBe("pinned");
    expect(retainDatasetSelection(versions, "other-product-version")).toBe("new");
    expect(retainDatasetSelection([], "pinned")).toBe("");
  });

  it("keeps source session labels and native observations without filling weekends", () => {
    const bars = [
      { open_time: Date.parse("2026-10-16T13:30:00Z"), close_time: Date.parse("2026-10-16T20:00:00Z"), open: "10", high: "12", low: "10", close: "11.25", volume: "20", session_date: "2026-10-16", available_at_ms: Date.parse("2026-10-21T09:00:00Z") },
      { open_time: Date.parse("2026-10-19T13:30:00Z"), close_time: Date.parse("2026-10-19T20:00:00Z"), open: "11", high: "12", low: "10", close: "11.50", volume: "25", session_date: "2026-10-19", available_at_ms: Date.parse("2026-10-21T09:00:00Z") },
    ];
    const option = datasetCloseOption(bars, "1d", "USD");
    expect(option.xAxis.data).toEqual(["2026-10-16", "2026-10-19"]);
    expect(option.series[0].data).toEqual([11.25, 11.5]);
    expect(bars[0].close).toBe("11.25");
    expect(datasetCloseOption([{ ...bars[0], close: "" }], "1d", "USD").series[0].data).toEqual([null]);
    expect(datasetCloseOption(bars, "15m", "USDT").xAxis.data[0]).toBe("2026-10-16T13:30:00.000Z");
  });
});
