import type { Asset, Overview, Provider } from "../types";
import { date } from "../services/api";
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
          ["全球风险状态", "UNKNOWN", "Phase 3 待接入情绪数据"],
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
