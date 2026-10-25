/** Translate known simulator exit codes into the Chinese labels used by reports. */
const EXIT_REASON_LABELS: Record<string, string> = {
  TAKE_PROFIT: "止盈目标触发",
  TAKE_PROFIT_GAP: "开盘跳空止盈",
  STOP_LOSS: "止损触发",
  STOP_LOSS_GAP: "开盘跳空止损",
  ATR_TRAILING_STOP: "ATR 跟踪止损",
  THREE_BAR_TRAILING_PROFIT: "最近三根 K 线跟踪止盈",
  TIME_EXIT: "达到持仓时间退出条件",
  MAX_HOLDING_TIME: "达到最长持仓时间",
  HIGHER_TREND_REVERSAL: "大周期趋势反转",
  TREND_REVERSAL: "趋势反转",
  OPPOSITE_SIGNAL: "出现反向信号",
  FINAL_EXIT: "回测区间结束平仓",
  LIQUIDATION_INTRABAR: "K 线内近似强制平仓",
  LIQUIDATION_OPEN_GAP: "开盘跳空触发强制平仓",
  LIQUIDATION_AFTER_FUNDING: "资金费结算后触发强制平仓",
  TIME_SCALE_OUT_COMPLETE_FAVORABLE: "持仓时间与有利价格偏移完成减仓",
  TIME_SCALE_OUT_COMPLETE_ADVERSE: "持仓时间与不利价格偏移完成减仓",
  TIME_DEVIATION_FAVORABLE: "持仓较久且价格有利偏移，逐步减仓",
  TIME_DEVIATION_ADVERSE: "持仓较久且价格不利偏移，逐步减仓",
};

/** Render known exit codes in Chinese while preserving unknown codes for diagnosis.
 * @param reason Raw exit code from a historical or account backtest, if present.
 * @returns A Chinese display label; unknown codes remain visible in parentheses.
 */
export function formatExitReason(reason: string | null | undefined): string {
  const normalized = reason?.trim();
  if (!normalized) return "—";
  return normalized.split(";").map((part) => {
    const code = part.trim();
    const known = EXIT_REASON_LABELS[code];
    if (known) return known;
    if (code.startsWith("RISK_")) return `风险管理减仓（${code.slice(5).replaceAll("_", " ")}）`;
    if (code.startsWith("TIME_DEVIATION_")) {
      const direction = code.endsWith("FAVORABLE") ? "有利" : code.endsWith("ADVERSE") ? "不利" : "";
      return `持仓时间偏移减仓${direction ? `（${direction}价格）` : ""}`;
    }
    return `其他退出原因（${code.replaceAll("_", " ")}）`;
  }).join("；");
}
