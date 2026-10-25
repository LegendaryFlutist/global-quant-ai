import { useEffect, useRef, useState } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
echarts.use([
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
]);
import { api, post, date } from "../services/api";
import { formatExitReason } from "../utils/labels";
interface Budget {
  allowed: boolean;
  reasons: string[];
  leverage: number;
  margin_fraction: number;
  nominal_exposure: number;
  estimated_stop_loss_fraction: number;
  estimated_daily_account_volatility: number;
  exchange_brackets_verified: boolean;
}
interface Experiment {
  symbol: string;
  timeframe: string;
  higher_minutes: number;
  model_sha256: string;
  model_status: string;
  validation_start: number;
  validation_end: number;
  latest: {
    score: number;
    direction: number;
    as_of_ms: number;
    daily_volatility: number;
    stop_fraction: number;
    blocked_reason: string;
    model_score: number;
    trend_score: number;
    dow_score: number;
    wave_score: number;
    pattern_score: number;
    budget: Budget;
  };
  cases: {
    name: string;
    metrics: {
      net_return: number;
      max_drawdown_stress: number;
      entries: number;
      holding_hours_mean: number;
      partial_exit_count: number;
      actual_max_leverage?: number;
    };
    curve: { close_time: number; equity: number }[];
    trades: {
      entry_time: number;
      exit_time: number;
      direction: number;
      net_pnl: number;
      holding_hours: number;
      exit_reason: string;
      leverage_at_entry?: number;
    }[];
  }[];
}
interface Report {
  created_at_ms: number;
  notes: string[];
  results: Experiment[];
}
const names: Record<string, string> = {
  fixed_5x: "固定5倍 / 10%保证金",
  score_volatility: "评分与波动率仓位",
  score_volatility_derisk: "动态仓位＋风险减仓",
};
/** 显示比例；不把模型质量分或杠杆倍数格式化为收益。 */
function pct(value: number) {
  return (100 * value).toFixed(2) + "%";
}
/** 绘制同区间净值，监听窗口变化并在切换标的时释放实例。 */
function ComparisonChart({ experiment }: { experiment: Experiment }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    chart.setOption({
      tooltip: { trigger: "axis" },
      legend: { bottom: 0 },
      grid: { left: 65, right: 25, top: 25, bottom: 70 },
      xAxis: { type: "time" },
      yAxis: { type: "value", scale: true, name: "净值 / 初始1" },
      series: experiment.cases.map((item) => ({
        name: names[item.name],
        type: "line",
        showSymbol: false,
        data: item.curve.map((row) => [row.close_time, row.equity]),
      })),
    });
    /** 容器布局改变时更新图表尺寸。 */
    function resize() {
      chart.resize();
    }
    const observer = new ResizeObserver(resize);
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [experiment]);
  return (
    <div ref={ref} style={{ height: 360 }} data-testid="risk-equity-chart" />
  );
}
/** 展示真实冻结GRU研究与预算试算，旧数据和未晋升模型始终禁止即时决策。 */
export default function RiskPage() {
  const [report, setReport] = useState<Report | null>(null),
    [error, setError] = useState("");
  const [symbol, setSymbol] = useState("KORUUSDT"),
    [cap, setCap] = useState(10),
    [risk, setRisk] = useState(1),
    [budget, setBudget] = useState<Budget | null>(null),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api<Report>("/risk/research")
      .then((value) => {
        if (active) setReport(value);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, []);
  const experiment = report?.results.find((row) => row.symbol === symbol);
  useEffect(() => {
    setBudget(null);
  }, [symbol, cap, risk]);
  /** 用报告实际评分和波动率试算，服务端重新执行范围与预算校验。 */
  async function calculate() {
    if (!experiment) return;
    setBusy(true);
    setError("");
    try {
      setBudget(
        await post<Budget>("/risk/budget", {
          score: experiment.latest.score,
          daily_volatility: experiment.latest.daily_volatility,
          stop_fraction: experiment.latest.stop_fraction,
          direction: experiment.latest.direction,
          max_leverage: cap,
          risk_percent: risk,
        }),
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const allocation = budget ?? experiment?.latest.budget;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">ENTRY QUALITY / RISK BUDGET</span>
          <h1>入场评分与仓位风控</h1>
          <p>先看方向与质量，再限制敞口；收益与回撤在同一张图比较。</p>
        </div>
        <select
          aria-label="风控标的"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
        >
          {["KORUUSDT", "CLUSDT", "NVDAUSDT", "BTCUSDT"].map((item) => (
            <option key={item}>{item}</option>
          ))}
        </select>
      </div>
      {error && (
        <div role="alert" className="alert error">
          {error}
        </div>
      )}
      <div className="alert">
        历史研究 · 原模型未通过晋升 · 不构成当前开仓信号 · 不发送订单
      </div>
      {!experiment ? (
        <div className="empty">
          {error ? "研究产物不可用" : "正在读取实际研究产物…"}
        </div>
      ) : (
        <>
          <section className="panel">
            <h2>
              {symbol} · {experiment.timeframe} / 上级{" "}
              {experiment.higher_minutes / 60}小时
            </h2>
            <p>
              评分时点：{date(experiment.latest.as_of_ms)} · 模型：
              {experiment.model_status} · 未校准质量分，不是获利概率。
            </p>
            <div className="risk-grid">
              <div>
                <strong className="risk-score">
                  {experiment.latest.score.toFixed(1)}
                  <small> / 100</small>
                </strong>
                <p>
                  候选方向：
                  {experiment.latest.direction === 1
                    ? "做多"
                    : experiment.latest.direction === -1
                      ? "做空"
                      : "观望"}
                </p>
              </div>
              <div>
                {[
                  ["GRU分类支持", experiment.latest.model_score, 50],
                  ["大周期趋势", experiment.latest.trend_score, 25],
                  ["道氏123", experiment.latest.dow_score, 10],
                  ["波浪结构", experiment.latest.wave_score, 10],
                  ["反转形态", experiment.latest.pattern_score, 5],
                ].map(([name, value, max]) => (
                  <div className="score-line" key={name}>
                    <span>{name}</span>
                    <progress max={Number(max)} value={Number(value)} />
                    <b>
                      {Number(value).toFixed(1)} / {max}
                    </b>
                  </div>
                ))}
              </div>
            </div>
            <p>
              {experiment.latest.blocked_reason ||
                "历史结构条件通过；仍受模型未晋升与数据时效限制。"}
            </p>
            <small>权重 SHA256：{experiment.model_sha256}</small>
          </section>
          <section className="panel">
            <h2>仓位预算试算</h2>
            <p>
              日波动率 {pct(experiment.latest.daily_volatility)} · 止损距离{" "}
              {pct(experiment.latest.stop_fraction)}
              。修改参数仅影响下方试算，不改变冻结回测。
            </p>
            <div className="risk-controls">
              <label>
                杠杆上限{" "}
                <input
                  aria-label="杠杆上限"
                  type="number"
                  min={1}
                  max={20}
                  value={cap}
                  onChange={(e) => setCap(Number(e.target.value))}
                />
              </label>
              <label>
                单笔预算（账户%）{" "}
                <input
                  aria-label="单笔预算"
                  type="number"
                  min={0.25}
                  max={2}
                  step={0.25}
                  value={risk}
                  onChange={(e) => setRisk(Number(e.target.value))}
                />
              </label>
              <button
                disabled={
                  busy ||
                  !Number.isInteger(cap) ||
                  cap < 1 ||
                  cap > 20 ||
                  risk < 0.25 ||
                  risk > 2
                }
                onClick={() => void calculate()}
              >
                {busy ? "计算中…" : "计算假设仓位"}
              </button>
            </div>
            {allocation && (
              <>
                <div className="risk-grid">
                  <div>
                    实际杠杆<strong>{allocation.leverage}×</strong>
                  </div>
                  <div>
                    保证金 / 账户
                    <strong>{pct(allocation.margin_fraction)}</strong>
                  </div>
                  <div>
                    名义敞口 / 账户
                    <strong>{pct(allocation.nominal_exposure)}</strong>
                  </div>
                  <div>
                    估计止损成本 / 账户
                    <strong>
                      {pct(allocation.estimated_stop_loss_fraction)}
                    </strong>
                  </div>
                </div>
                <p>
                  {allocation.allowed
                    ? "该历史候选满足研究预算；即时执行仍不可用。"
                    : allocation.reasons.join("；")}
                </p>
              </>
            )}
            <p>
              高波动降低杠杆与敞口；账户回撤8%暂停新仓。维持保证金为1%研究假设，尚未核验实际账户档位；跳空与资金费可能让损失超过估计。
            </p>
          </section>
          <section className="panel">
            <h2>同条件收益与回撤对比</h2>
            <p>
              {date(experiment.validation_start)} —{" "}
              {date(experiment.validation_end)} · 原第3验证窗复用 ·
              未触碰保留留出集 · 初始净值1
            </p>
            <ComparisonChart experiment={experiment} />
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>策略</th>
                    <th>收益率</th>
                    <th>压力回撤</th>
                    <th>开仓次数</th>
                    <th>平均持仓</th>
                    <th>部分减仓次数</th>
                  </tr>
                </thead>
                <tbody>
                  {experiment.cases.map((item) => (
                    <tr key={item.name}>
                      <td>{names[item.name]}</td>
                      <td>{pct(item.metrics.net_return)}</td>
                      <td>{pct(item.metrics.max_drawdown_stress)}</td>
                      <td>{item.metrics.entries}</td>
                      <td>{item.metrics.holding_hours_mean.toFixed(1)}小时</td>
                      <td>{item.metrics.partial_exit_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              三组使用相同评分候选、费用、15分钟人工延迟、24小时～21天持仓与5%价格目标。减仓模式在1R/2R分批兑现，波动升至1.75倍、评分下降20分或峰值回撤1R时减仓；硬止损可提前退出。
            </p>
          </section>
          <section className="panel">
            <h2>分标的实际操作 · 动态仓位＋减仓</h2>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>开仓</th>
                    <th>平仓</th>
                    <th>方向</th>
                    <th>杠杆</th>
                    <th>持仓</th>
                    <th>账户盈亏</th>
                    <th>退出原因</th>
                  </tr>
                </thead>
                <tbody>
                  {experiment.cases[2].trades.map((trade, index) => (
                    <tr key={index}>
                      <td>{date(trade.entry_time)}</td>
                      <td>{date(trade.exit_time)}</td>
                      <td>{trade.direction === 1 ? "多" : "空"}</td>
                      <td>{trade.leverage_at_entry}×</td>
                      <td>{trade.holding_hours.toFixed(1)}小时</td>
                      <td>{pct(trade.net_pnl)}</td>
                      <td>{formatExitReason(trade.exit_reason)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!experiment.cases[2].trades.length && (
                <p>该验证窗没有满足条件的开仓，不生成虚拟交易。</p>
              )}
            </div>
          </section>
          <section className="panel">
            <h2>实验边界</h2>
            {report?.notes.map((note) => (
              <p key={note}>{note}</p>
            ))}
            <small>产物生成：{date(report?.created_at_ms)}</small>
          </section>
        </>
      )}
    </>
  );
}
