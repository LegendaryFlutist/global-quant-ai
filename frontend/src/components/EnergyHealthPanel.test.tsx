import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { LiveMarketResult } from "../types";
import EnergyHealthPanel from "./EnergyHealthPanel";

const live: LiveMarketResult = {
  asset_id: "yahoo_energy:CL", interval: "15m", closed_candles: [], live_candle: null,
  research_only: true, stale: true, source_event_at_ms: 1792789198000, updated_at_ms: 1792893600000,
  health: { version: 1, checked_at_ms: 1792893600000, collector_status: "OK", collector_error: null,
    last_success_at_ms: 1792893600000, next_attempt_at_ms: 1792893900000, source_status: "STALE",
    source_age_seconds: 104402, cache_status: "PRESENT", stale_before_closure: false, stale: true,
    session: { status: "WEEKEND", version: "fixture", timezone: "America/Chicago", holiday_calendar_verified: false,
      display_only: true, closure_start_at_ms: 1792789200000, nominal_reopen_at_ms: 1792965600000 } },
};

describe("Energy source diagnostics", () => {
  it("shows successful collection separately from an old weekend price", () => {
    const html = renderToStaticMarkup(<EnergyHealthPanel live={live} timezone="Asia/Shanghai" />);
    expect(html).toContain("后台采集正常");
    expect(html).toContain("源报价偏旧");
    expect(html).toContain("常规周末休市");
    expect(html).toContain("下次采集 / 重试计划");
    expect(html).toContain("节假日、临时停市及连续代码换月未核验");
  });
  it("does not keep a green current collector badge after page polling fails", () => {
    const html = renderToStaticMarkup(<EnergyHealthPanel live={{ ...live, poll_error: "无法读取服务" }} timezone="UTC" />);
    expect(html).toContain("页面状态读取失败");
    expect(html).toContain("保留上次状态");
    expect(html).not.toContain("后台采集正常");
  });
  it("keeps backend failures and pre-closure gaps visible during a weekend", () => {
    const failed = { ...live, health: { ...live.health!, collector_status: "ERROR" as const,
      collector_error: "公开源连接失败", stale_before_closure: true, cache_status: "EXPIRED" as const } };
    const html = renderToStaticMarkup(<EnergyHealthPanel live={failed} timezone="UTC" />);
    expect(html).toContain("后台采集失败");
    expect(html).toContain("公开源连接失败");
    expect(html).toContain("休市前报价已偏旧");
    expect(html).toContain("缓存未按期刷新");
  });
});
