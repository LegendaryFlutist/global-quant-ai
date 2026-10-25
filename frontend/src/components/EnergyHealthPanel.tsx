import type { LiveMarketResult } from "../types";
import { date } from "../services/api";

/** Show independent collector and quote status; closures never certify a live price. */
export default function EnergyHealthPanel({ live, timezone }: { live: LiveMarketResult; timezone: string }) {
  const health = live.health;
  if (!health) return null;
  const collectors = { WAITING: "等待首次后台采集", OK: "后台采集正常", ERROR: "后台采集失败", OVERDUE: "后台采集超时未更新" };
  const sources = { MISSING: "暂无源报价", INVALID_TIME: "源时间异常", STALE: "源报价偏旧", DELAYED_REFERENCE: "延迟参考报价" };
  const sessions = { WEEKEND: "常规周末休市", DAILY_BREAK: "常规每日维护时段", REGULAR_WINDOW: "常规交易时段（实际开市未核验）" };
  return <section className="energy-health" aria-label="能源数据状态">
    <div className="chart-meta">
      <span className={`badge ${live.poll_error ? "negative" : health.collector_status === "OK" ? "positive" : "negative"}`}>
        {live.poll_error ? "页面状态读取失败" : collectors[health.collector_status]}
      </span>
      <span className={`badge ${health.source_status === "DELAYED_REFERENCE" ? "neutral" : "negative"}`}>{sources[health.source_status]}</span>
      <span className="badge neutral">{sessions[health.session.status]}</span>
    </div>
    {live.poll_error && <p className="error">{live.poll_error}；以下保留上次状态，不能确认后台当前是否正常。</p>}
    <div className="chart-live-status">
      <span>源报价：{live.source_event_at_ms ? date(live.source_event_at_ms, timezone) : "暂无"}</span>
      <span>缓存接收：{live.updated_at_ms ? date(live.updated_at_ms, timezone) : "暂无"}</span>
      {health.last_success_at_ms && <span>后台上次成功：{date(health.last_success_at_ms, timezone)}</span>}
      {health.next_attempt_at_ms && <span>下次采集 / 重试计划：{date(health.next_attempt_at_ms, timezone)}</span>}
      <span>状态检查：{date(health.checked_at_ms, timezone)}</span>
    </div>
    {health.collector_error && <p className="error">{health.collector_error}</p>}
    {health.cache_status !== "PRESENT" && <p className="error">{health.cache_status === "MISSING" ? "暂无页面快照；磁盘历史仍保留。" : "缓存未按期刷新；当前显示历史快照。"}</p>}
    {health.stale_before_closure && <p className="error">休市前报价已偏旧，不能仅用休市解释数据缺口。</p>}
    {health.session.nominal_reopen_at_ms && <p>常规恢复时间：{date(health.session.nominal_reopen_at_ms, timezone)}（节假日或临时安排可能不同）</p>}
    <small>页面每5秒读取缓存，上游按采集计划更新。{live.source_freshness}。常规时段按芝加哥时间及夏令时计算；节假日、临时停市及连续代码换月未核验。休市不代表旧报价可用于即时交易。</small>
  </section>;
}
