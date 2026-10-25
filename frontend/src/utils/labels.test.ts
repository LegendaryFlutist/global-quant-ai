import { describe, expect, it } from "vitest";
import { formatExitReason } from "./labels";

describe("formatExitReason", () => {
  it("translates known exit codes and combined risk reasons", () => {
    expect(formatExitReason("STOP_LOSS_GAP")).toBe("开盘跳空止损");
    expect(formatExitReason("MAX_HOLDING_TIME;OPPOSITE_SIGNAL")).toBe("达到最长持仓时间；出现反向信号");
    expect(formatExitReason("RISK_VOLATILITY_SPIKE")).toBe("风险管理减仓（VOLATILITY SPIKE）");
  });

  it("keeps unknown values visible and handles missing reasons", () => {
    expect(formatExitReason("NEW_EXIT_CODE")).toBe("其他退出原因（NEW EXIT CODE）");
    expect(formatExitReason(" ")).toBe("—");
  });
});
