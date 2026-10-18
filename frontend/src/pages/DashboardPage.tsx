import { useEffect, useState } from "react";
import type { Asset, Overview, Provider } from "../types";
import type { MarketSentiment } from "../types";
import { api, date } from "../services/api";
import { badge } from "../components/StatusBadge";

/** 展示真实数据覆盖、优先标的及连接状态；没有模型数据时不制造风险结论。 */
export default function DashboardPage({
  overview,
  providers,
  timezone,
  onRefresh,
  onChart,
  onVerify,
}: {
  overview: Overview | null;
  providers: Provider[];
  timezone: string;
  onRefresh: () => Promise<void>;
  onChart: (asset: Asset) => void;
  onVerify: () => Promise<void>;
}) {
  const [sentiment, setSentiment] = useState<MarketSentiment | null>(null);
  const [sentimentError, setSentimentError] = useState("");
  useEffect(() => {
    let active = true;
    /** Refresh the local-only structural summary once per minute without using live private data. */
    async function refreshSentiment() {
      try {
        const value = await api<MarketSentiment>("/market/sentiment");
        if (active) {
          setSentiment(value);
          setSentimentError("");
        }
      } catch (error) {
        if (active) setSentimentError((error as Error).message);
      }
    }
    void refreshSentiment();
    const timer = window.setInterval(() => void refreshSentiment(), 60000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">RESEARCH OVERVIEW</span>
          <h1>市场研究总览</h1>
          <p>优先资产、数据覆盖与系统运行状态</p>
        </div>
        <button onClick={() => void onRefresh()}>刷新数据</button>
      </header>
      <div className="stat-grid">
        {[
          ["登记资产", overview?.asset_count ?? "—", "全球资产与相关合约"],
          [
            "历史 K 线",
            overview?.candle_count.toLocaleString() ?? "—",
            `${overview?.datasets ?? 0} 份原生行情集`,
          ],
          ["数据窗口", "90 天", "保留真实来源与文件校验"],
          ["本地结构周期", sentiment?.regime ?? "UNKNOWN", sentimentError ? "情绪摘要暂不可用" : `${sentiment?.known_asset_count ?? 0}/4 个优先标的有本地结构缓存`],
        ].map(([title, value, detail]) => (
          <section className="stat" key={title}>
            <span>{title}</span>
            <strong>{value}</strong>
            <small>{detail}</small>
          </section>
        ))}
      </div>
      <div className="section-title">
        <h2>优先观察</h2>
        <span>韩国 · 能源 · AI · 加密货币</span>
      </div>
      <div className="asset-cards">
        {overview?.priority_assets.map((a, i) => (
          <button key={a.id} className="asset-card" onClick={() => onChart(a)}>
            <span className="asset-number">0{i + 1}</span>
            <div className="asset-title">
              <strong>{a.symbol}</strong>
              <span>↗</span>
            </div>
            <p>{a.name}</p>
            <div className="tags">
              {a.themes.map((t) => (
                <span key={t}>{t}</span>
              ))}
            </div>
            <div className="asset-card-footer">
              {a.datasets.length ? (
                <span>
                  {a.datasets[0].rows.toLocaleString()} 根 ·{" "}
                  {a.datasets[0].interval}
                </span>
              ) : (
                <span>暂无历史数据</span>
              )}
              <span>打开图表 →</span>
            </div>
          </button>
        ))}
      </div>
      <section className="panel">
        <div className="section-title">
          <div>
            <h2>总体情绪面 · 结构周期</h2>
            <small>{sentiment?.score_meaning ?? "只汇总本地已收盘趋势、道氏状态和近期确认背离，不构成交易信号。"}</small>
          </div>
          <span>方向分 {sentiment?.signed_score ?? "—"} · 更新时间 {date(sentiment?.as_of_ms, timezone)}</span>
        </div>
        <p>
          当前状态：<strong>{sentiment?.regime ?? "UNKNOWN"}</strong> · 已知 {sentiment?.known_asset_count ?? 0} / 4 · 缺行情 {sentiment?.unknown_asset_count ?? 4}。
          缺失周期保持 UNKNOWN，不按其他资产补值。
        </p>
        {sentiment?.assets.map((item) => (
          <div className="provider-row" key={item.asset_id}>
            <div>
              <strong>{item.symbol} · {item.regime}{item.signed_score === null ? "" : ` (${item.signed_score})`}</strong>
              <small>
                {item.timeframes.length
                  ? item.timeframes.map((frame) => {
                    const support = frame.support_resistance.support?.price ?? "—";
                    const resistance = frame.support_resistance.resistance?.price ?? "—";
                    const divergence = frame.recent_events.map((event) => event.kind.replace("_RSI_DIVERGENCE", "背离")).join("/") || "无近期背离";
                    const volatility = frame.volatility_20_bar_pct === null ? "—" : `${frame.volatility_20_bar_pct.toFixed(2)}%`;
                    return `${frame.timeframe} ${frame.trend_component > 0 ? "上行" : frame.trend_component < 0 ? "下行" : "中性"} / 道氏${frame.dow123_component > 0 ? "+" : ""}${frame.dow123_component} / 趋势线${frame.trendline_break_state} / ${divergence} / 波浪${frame.wave_candidate} / 20根波幅${volatility} / 支撑${support}压力${resistance}`;
                  }).join(" · ")
                  : `本地结构缓存不足：${item.missing_archives.join(", ") || "未核验或尚未采集"}`}
              </small>
            </div>
            <span className="badge neutral">{item.status === "OK" ? `${item.timeframes.length} 周期` : "UNKNOWN"}</span>
          </div>
        ))}
      </section>
      <div className="two-col">
        <section className="panel">
          <div className="section-title">
            <h3>数据连接</h3>
            <button onClick={() => void onVerify()}>重新核验</button>
          </div>
          {providers.map((p) => (
            <div className="provider-row" key={p.id}>
              <div>
                <strong>{p.name}</strong>
                <small>
                  {String(
                    p.detail.error ??
                      p.detail.last_success_at ??
                      "已注册来源，状态见标记",
                  )}
                </small>
              </div>
              {badge(p.status)}
            </div>
          ))}
          <p className="muted">
            快照核验保留来源时间；连接不可用时显示缓存，不视为实时行情。
          </p>
        </section>
        <section className="panel">
          <h3>运行边界</h3>
          <ul className="plain-list">
            <li>
              <span>当前运行模式</span>
              <b>行情研究</b>
            </li>
            <li>
              <span>实盘执行</span>
              <b className="green">服务端禁用</b>
            </li>
            <li>
              <span>行情覆盖截止</span>
              <b>{date(overview?.as_of_ms, timezone)}</b>
            </li>
            <li>
              <span>完成的后台任务</span>
              <b>{overview?.successful_tasks ?? 0}</b>
            </li>
          </ul>
          <p>
            历史研究报告与 NGC GPU 容器保留。Web
            模型训练、账户、邮件与审批能力按阶段接入。
          </p>
        </section>
      </div>
    </>
  );
}
