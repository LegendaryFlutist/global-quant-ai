import { useEffect, useState } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { Asset, SimulationAccount, SimulationBacktestRun, SimulationEquityPoint } from "../types";
import { api, post } from "../services/api";
import FullResearchBacktests from "./FullResearchBacktests";

echarts.use([LineChart, DataZoomComponent, GridComponent, TooltipComponent, CanvasRenderer]);

type WeightKey = keyof SimulationAccount["configuration"]["weights"];
type Draft = Omit<SimulationAccount, "id" | "configuration_sha256" | "revision" | "created_at" | "updated_at" | "research_only" | "backtest_implemented">;
const INTERVAL_CHOICES = ["15m", "30m", "1h", "2h", "4h", "1d"];
const WEIGHTS: { key: WeightKey; label: string }[] = [
  { key: "trend", label: "趋势" }, { key: "dow123", label: "道氏 1-2-3" },
  { key: "wave", label: "波浪候选" }, { key: "divergence", label: "RSI 背离" },
  { key: "reversal_patterns", label: "反转形态" }, { key: "deep_model", label: "深度模型" },
];

/** 生成默认的最近三年回测起始UTC日期。 */
function defaultStartDate() {
  const start = new Date();
  start.setUTCFullYear(start.getUTCFullYear() - 3);
  start.setUTCDate(start.getUTCDate() + 1);
  return start.toISOString().slice(0, 10);
}

/** 展示本地USDT净值和回撤；浏览器只把Decimal文本转换成图表坐标。 */
function EquityChart({ points }: { points: SimulationEquityPoint[] }) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!element) return;
    const chart = echarts.init(element, "dark", { renderer: "canvas" });
    chart.setOption({
      backgroundColor: "#111927",
      tooltip: { trigger: "axis" },
      grid: { left: 64, right: 24, top: 24, bottom: 72 },
      xAxis: { type: "time" },
      yAxis: [
        { type: "value", scale: true, name: "净值 / USDT" },
        { type: "value", min: 0, max: 1, axisLabel: { formatter: (value: number) => `${(100 * value).toFixed(0)}%` }, name: "回撤" },
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18 }],
      series: [
        { type: "line", name: "组合净值", showSymbol: false, data: points.map((point) => [point.close_time, Number(point.equity)]) },
        { type: "line", name: "回撤", yAxisIndex: 1, showSymbol: false, data: points.map((point) => [point.close_time, Number(point.drawdown)]) },
      ],
    });
    /** 跟随容器布局更新图表尺寸并在组件卸载时释放监听器。 */
    function resize() { chart.resize(); }
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [element, points]);
  return <div ref={setElement} style={{ height: 300 }} data-testid="simulation-equity-chart" />;
}

/** 创建默认权重和保守研究风险政策，不隐式启用未登记深度模型。 */
function emptyDraft(): Draft {
  return {
    name: "", initial_equity: "10000", base_currency: "USDT",
    configuration: {
      asset_ids: ["binance_usdm:KORUUSDT", "binance_usdm:CLUSDT", "binance_usdm:NVDAUSDT", "binance_usdm:BTCUSDT"],
      intervals: ["15m", "1h", "4h"],
      timeframe_weights: Object.fromEntries(INTERVAL_CHOICES.map((interval) => [interval, "1"])),
      direction_mode: "trend_following", decision_horizon_hours: 168, horizon_consensus_weight: "0.25",
      weights: { trend: "0.40", dow123: "0.25", wave: "0.10", divergence: "0.15", reversal_patterns: "0.10", deep_model: "0" },
      entry_threshold: "0.70", max_leverage: "3", max_margin_fraction: "0.10",
      min_holding_hours: 1, max_holding_hours: 168, exit_mode: "three_bar_trailing", deep_model_version: null,
    },
  };
}

/** 提供持久化研究配置、只读归档回测任务、逐笔账本与组合曲线。 */
export default function SimulationPage() {
  const [accounts, setAccounts] = useState<SimulationAccount[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(false);
  const [datasetLoadingId, setDatasetLoadingId] = useState<string | null>(null);
  const [backtestLoadingId, setBacktestLoadingId] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, SimulationBacktestRun[]>>({});
  const [curves, setCurves] = useState<Record<string, SimulationEquityPoint[]>>({});
  const [startDate, setStartDate] = useState(defaultStartDate);
  const [endDate, setEndDate] = useState(() => new Date().toISOString().slice(0, 10));
  /** 从已认证服务读取当前用户的配置账号。 */
  async function refreshAccounts() {
    setLoading(true);
    try {
      const loaded = await api<SimulationAccount[]>("/simulation/accounts");
      setAccounts(loaded);
      const histories = await Promise.all(loaded.map(async (account) => {
        const values = await api<SimulationBacktestRun[]>(`/simulation/accounts/${encodeURIComponent(account.id)}/backtests`).catch(() => []);
        return [account.id, values] as const;
      }));
      setRuns(Object.fromEntries(histories));
      const curvePairs = await Promise.all(histories.flatMap(([accountId, values]) => values
        .filter((run) => run.status === "SUCCEEDED" && run.result.status === "COMPLETED")
        .slice(0, 1)
        .map(async (run) => {
          const response = await api<{ points: SimulationEquityPoint[] }>(
            `/simulation/accounts/${encodeURIComponent(accountId)}/backtests/${encodeURIComponent(run.id)}/equity`,
          ).catch(() => ({ points: [] }));
          return [run.id, response.points] as const;
        })));
      setCurves(Object.fromEntries(curvePairs));
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void refreshAccounts();
    api<Asset[]>("/assets").then(setAssets).catch((cause) => setError((cause as Error).message));
  }, []);
  /** 保存新账号或新配置修订；后端重新验证权重、周期与风险边界。 */
  async function saveAccount() {
    const total = Object.values(draft.configuration.weights).reduce((sum, value) => sum + Number(value), 0);
    if (Math.abs(total - 1) > 0.000001) {
      setError("理论权重总和必须为 1.00");
      return;
    }
    try {
      if (editingId) {
        await api<SimulationAccount>(`/simulation/accounts/${encodeURIComponent(editingId)}`, {
          method: "PUT", body: JSON.stringify(draft),
        });
      } else {
        await post<SimulationAccount>("/simulation/accounts", draft);
      }
      setDraft(emptyDraft());
      setEditingId(null);
      setSaved("模拟配置已保存；请先准备本地归档数据，再运行只读模拟回测。");
      await refreshAccounts();
    } catch (cause) {
      setError((cause as Error).message);
    }
  }
  /** 经账号所有权API按需排队该配置资产/周期的本地标记价与资金费数据。 */
  async function queueDerivativeData(account: SimulationAccount) {
    setDatasetLoadingId(account.id);
    setError("");
    try {
      const end = Date.now();
      const start = new Date(end);
      start.setUTCFullYear(start.getUTCFullYear() - 3);
      const result = await post<{ tasks: { id: string; interval: string }[] }>(
        `/simulation/accounts/${encodeURIComponent(account.id)}/datasets`,
        { start_ms: start.getTime(), end_ms: end },
      );
      setSaved(`已排队 ${result.tasks.length} 个公开数据任务（交易价K线、标记价和资金费率）；进度请在“后台任务”查看。任务完成前不会生成回测收益。`);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setDatasetLoadingId(null);
    }
  }
  /** 使用用户选择的日期和当前账号修订创建本地已归档行情回测任务。 */
  async function queueBacktest(account: SimulationAccount) {
    const start = Date.parse(`${startDate}T00:00:00.000Z`);
    const selectedEnd = Date.parse(`${endDate}T23:59:59.999Z`);
    const end = Math.min(Date.now(), selectedEnd);
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      setError("请设置有效且开始早于结束的UTC日期范围");
      return;
    }
    setBacktestLoadingId(account.id);
    setError("");
    try {
      const queued = await post<{ id: string }>(
        `/simulation/accounts/${encodeURIComponent(account.id)}/backtests`,
        { start_ms: start, end_ms: end },
      );
      setSaved(`回测任务 ${queued.id} 已排队；状态和逐笔结果会出现在此账号下。`);
      await refreshAccounts();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBacktestLoadingId(null);
    }
  }
  /** 将指定修订复制到表单；服务器状态仅在显式保存后更改。 */
  function editAccount(account: SimulationAccount) {
    setEditingId(account.id);
    setDraft({
      name: account.name,
      initial_equity: account.initial_equity,
      base_currency: account.base_currency,
      configuration: structuredClone(account.configuration),
    });
    setError("");
    setSaved("");
  }
  /** 取消编辑并恢复新配置的默认值。 */
  function cancelEdit() {
    setDraft(emptyDraft());
    setEditingId(null);
    setError("");
  }
  const weightTotal = Object.values(draft.configuration.weights).reduce((sum, value) => sum + Number(value), 0);
  const accountComparisons = accounts.flatMap((account) => {
    const latest = (runs[account.id] ?? []).find((run) =>
      run.status === "SUCCEEDED" && run.result.status === "COMPLETED",
    );
    return latest ? [{ account, run: latest }] : [];
  });
  return (
    <>
      <header className="page-heading">
        <div><span className="eyebrow">ISOLATED RESEARCH CONFIGS</span><h1>模拟账号与模型组合</h1><p>账号分别保存策略权重与风险参数；回测只读取本地归档行情，并生成研究账本和净值报告。</p></div>
        <button onClick={() => void refreshAccounts()} disabled={loading}>刷新账号</button>
      </header>
      {error && <div role="alert" className="alert error">{error}</div>}
      {saved && <div role="status" className="alert">{saved}</div>}
      <FullResearchBacktests />
      <section className="panel filters">
        <label>回测起始日期（UTC）<input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
        <label>回测结束日期（UTC）<input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
        <p>范围最多三年；建议先点击每个账号的“按需采集”并等后台任务完成。缺少交易价、标记价或资金费数据的标的会标明跳过。</p>
      </section>
      <section className="panel">
        <h2>各模拟策略最近回测对照</h2>
        {accountComparisons.length ? <div className="table-wrap"><table><thead><tr><th>模拟账号</th><th>策略修订</th><th>净收益</th><th>最大回撤</th><th>成交</th><th>时间范围（UTC）</th></tr></thead>
          <tbody>{accountComparisons.map(({ account, run }) => <tr key={account.id}>
            <td>{account.name}</td><td>{run.parameters.account_revision}</td>
            <td>{run.result.net_return_pct}%</td><td>{run.result.max_drawdown_pct}%</td><td>{run.result.trade_count}</td>
            <td>{new Date(run.parameters.start_ms).toISOString().slice(0, 10)} – {new Date(run.parameters.end_ms).toISOString().slice(0, 10)}</td>
          </tr>)}</tbody></table></div> : <p className="muted">各账号完成首轮有效回测后会显示并列结果。</p>}
        <p className="muted">比较只对照各自完成的本地回测，不按收益自动选择或启用模型；先核对时间范围、配置和数据来源。</p>
      </section>
      <div className="two-col">
        <section className="panel">
          <h2>{editingId ? "编辑配置账号" : "创建配置账号"}</h2>
          <label>账号名称<input value={draft.name} maxLength={80} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></label>
          <label>初始资金（USDT）<input inputMode="decimal" value={draft.initial_equity} onChange={(event) => setDraft({ ...draft, initial_equity: event.target.value })} /></label>
          <label>入场评分门槛（0～1）<input inputMode="decimal" value={draft.configuration.entry_threshold} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, entry_threshold: event.target.value } })} /></label>
          <h3>参与理论权重</h3>
          <div className="filters">
            {WEIGHTS.map(({ key, label }) => <label key={key}>{label}<input inputMode="decimal" value={draft.configuration.weights[key]} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, weights: { ...draft.configuration.weights, [key]: event.target.value } } })} /></label>)}
          </div>
          <p>权重合计：{weightTotal.toFixed(2)}。深度模型权重大于0时需指定版本；实际回测仍待模型注册接入。</p>
          {Number(draft.configuration.weights.deep_model) > 0 && <label>深度模型版本ID<input value={draft.configuration.deep_model_version ?? ""} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, deep_model_version: event.target.value || null } })} /></label>}
          <fieldset><legend>回测标的（仅精确核验永续）</legend>
            {assets.filter((asset) => asset.venue === "BINANCE_USDM" && ["VERIFIED", "VERIFIED_SNAPSHOT"].includes(asset.verification)).map((asset) => <label key={asset.id} className="toggle">
              <input type="checkbox" checked={draft.configuration.asset_ids.includes(asset.id)} onChange={(event) => {
                const current = draft.configuration.asset_ids;
                const asset_ids = event.target.checked ? [...current, asset.id] : current.filter((value) => value !== asset.id);
                setDraft({ ...draft, configuration: { ...draft.configuration, asset_ids } });
              }} />{asset.symbol}
            </label>)}
          </fieldset>
          <fieldset><legend>观察周期</legend>
            {INTERVAL_CHOICES.map((interval) => <label key={interval} className="toggle"><input type="checkbox" checked={draft.configuration.intervals.includes(interval)} onChange={(event) => {
              const current = draft.configuration.intervals;
              const intervals = event.target.checked ? [...current, interval] : current.filter((value) => value !== interval);
              setDraft({ ...draft, configuration: { ...draft.configuration, intervals } });
            }} />{interval}</label>)}
          </fieldset>
          <fieldset><legend>多周期相对权重（仅已选周期参与）</legend>
            <div className="filters">{draft.configuration.intervals.map((interval) => <label key={interval}>{interval}<input type="number" min={0} max={10} step={0.1} value={draft.configuration.timeframe_weights[interval] ?? "1"} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, timeframe_weights: { ...draft.configuration.timeframe_weights, [interval]: event.target.value } } })} /></label>)}</div>
          </fieldset>
          <div className="filters">
            <label>入场方向规则<select value={draft.configuration.direction_mode} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, direction_mode: event.target.value as Draft["configuration"]["direction_mode"] } })}>
              <option value="trend_following">顺最高周期趋势</option><option value="countertrend_rebound">允许多理论确认的逆势反弹</option><option value="balanced">多周期多数方向</option>
            </select></label>
            <label>主要评估期限<select value={draft.configuration.decision_horizon_hours} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, decision_horizon_hours: Number(event.target.value) as 72 | 168 | 720 } })}>
              <option value={72}>3天</option><option value={168}>7天</option><option value={720}>30天</option>
            </select></label>
            <label>其他期限共识占比<input type="number" min={0} max={1} step={0.05} value={draft.configuration.horizon_consensus_weight} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, horizon_consensus_weight: event.target.value } })} /></label>
          </div>
          <div className="filters">
            <label>模拟最大杠杆（1～20）<input inputMode="decimal" value={draft.configuration.max_leverage} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, max_leverage: event.target.value } })} /></label>
            <label>单次保证金比例（≤25%）<input inputMode="decimal" value={draft.configuration.max_margin_fraction} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, max_margin_fraction: event.target.value } })} /></label>
            <label>最短持仓小时（1～上限，止损可提前）<input type="number" min={1} max={draft.configuration.max_holding_hours} value={draft.configuration.min_holding_hours} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, min_holding_hours: Number(event.target.value) } })} /></label>
            <label>持仓上限小时（1～2160）<input type="number" min={1} max={2160} value={draft.configuration.max_holding_hours} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, max_holding_hours: Number(event.target.value) } })} /></label>
            <label>止盈/退出模式<select value={draft.configuration.exit_mode} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, exit_mode: event.target.value as Draft["configuration"]["exit_mode"] } })}>
              <option value="three_bar_trailing">最近三根K线跟踪退出</option><option value="atr">ATR退出</option><option value="fixed_rr">固定盈亏比</option><option value="time">时间退出</option>
            </select></label>
          </div>
          <div className="actions"><button onClick={() => void saveAccount()}>保存配置</button>{editingId && <button onClick={cancelEdit}>取消编辑</button>}</div>
        </section>
        <section className="panel">
          <h2>已保存配置账号（{accounts.length}）</h2>
          {accounts.length === 0 && <p>尚未创建。创建多个账号可分别保存理论权重和风险政策。</p>}
          {accounts.map((account) => <article className="panel" key={account.id}>
            <div className="section-title"><div><h3>{account.name}</h3><small>修订 {account.revision} · USDT {account.initial_equity}</small></div><button onClick={() => editAccount(account)}>编辑</button></div>
            <p>标的：{account.configuration.asset_ids.map((id) => assets.find((asset) => asset.id === id)?.symbol ?? id).join(" / ")}</p>
            <p>周期：{account.configuration.intervals.join(" / ")} · 期限 {account.configuration.decision_horizon_hours / 24} 天 · 方向规则 {account.configuration.direction_mode} · 杠杆 {account.configuration.max_leverage}× · 持仓 {account.configuration.min_holding_hours}～{account.configuration.max_holding_hours} 小时（止损可提前）</p>
            <p>权重：{WEIGHTS.map(({ key, label }) => `${label} ${account.configuration.weights[key]}`).join(" · ")}</p>
            <small className="mono break">配置 SHA256：{account.configuration_sha256}</small>
            <div className="actions">
              <button onClick={() => void queueDerivativeData(account)} disabled={datasetLoadingId !== null}>
                {datasetLoadingId === account.id ? "提交中…" : "按需采集三年交易价、标记价与资金费率"}
              </button>
              <button onClick={() => void queueBacktest(account)} disabled={backtestLoadingId !== null}>
                {backtestLoadingId === account.id ? "排队中…" : "运行本地合约回测"}
              </button>
            </div>
            <p className="muted">隔离研究账号 · 手续费按单边0.05%、滑点按单边0.02%假设；结果绑定本地数据SHA256。深度模型暂不支持Web版本加载。</p>
            <h4>历史回测比较（{runs[account.id]?.length ?? 0}）</h4>
            {(runs[account.id] ?? []).map((run) => <article key={run.id} className="panel">
              <div className="section-title"><strong>{run.result.status ?? run.status}</strong><small>{new Date(run.created_at).toLocaleString()}</small></div>
              {run.result.status === "COMPLETED" ? <>
                <p>USDT {run.result.initial_equity} → {run.result.final_equity} · 净收益 {run.result.net_return_pct}% · 最大回撤 {run.result.max_drawdown_pct}% · 成交 {run.result.trade_count}</p>
                {run.result.algorithm_sha256 && <small className="mono break">算法 SHA256：{run.result.algorithm_sha256}</small>}
                {run.result.forecast_definition && <p className="muted">期限情景：{run.result.forecast_definition.horizons_hours.map((hours) => `${hours / 24}天`).join(" / ")}。价格区间尚未做历史校准，不作为概率或保证目标价。</p>}
                {(run.result.per_asset ?? []).map((assetRun) => <p key={assetRun.asset_id} className="mono break">
                  {assets.find((asset) => asset.id === assetRun.asset_id)?.symbol ?? assetRun.asset_id}：净收益 {assetRun.metrics.net_return_pct}% · 成交 {assetRun.metrics.trades} · 平均持仓 {assetRun.metrics.holding_hours_mean} 小时 · 期限方向命中 {Object.entries(assetRun.forecast_evaluation ?? {}).map(([horizon, score]) => `${Number(horizon.replace("h", "")) / 24}天 ${score.directional_accuracy_pct ?? "—"}%（n=${score.observations}）`).join(" / ")}
                </p>)}
                {(run.result.skipped_assets ?? []).map((asset) => <p className="muted" key={asset.asset_id}>{asset.asset_id}已跳过：{asset.reason}</p>)}
                {curves[run.id]?.length ? <EquityChart points={curves[run.id]} /> : <p className="muted">净值文件暂不可用</p>}
                <details><summary>逐笔模拟操作</summary>
                  <RunTrades accountId={account.id} runId={run.id} />
                </details>
              </> : run.result.status === "NO_DATA" ? <p className="muted">没有可用的本地归档数据；请先完成按需采集任务。</p> : <p className="muted">{run.status === "FAILED" ? "回测失败；请检查后台任务错误信息。" : "后台处理中。"}</p>}
              <small className="mono break">策略配置SHA256：{run.parameters.configuration_sha256}</small>
            </article>)}
          </article>)}
        </section>
      </div>
    </>
  );
}

/** 展示选定回测的逐笔方向、持仓时长和各期限入场情景及事后价格。 */
function RunTrades({ accountId, runId }: { accountId: string; runId: string }) {
  const [trades, setTrades] = useState<Record<string, unknown>[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ trades: Record<string, unknown>[] }>(
      `/simulation/accounts/${encodeURIComponent(accountId)}/backtests/${encodeURIComponent(runId)}/trades`,
    ).then((result) => setTrades(result.trades)).catch((cause) => setError((cause as Error).message));
  }, [accountId, runId]);
  if (error) return <p className="muted">{error}</p>;
  return <div className="table-wrap"><table><thead><tr><th>标的</th><th>方向</th><th>开仓时间</th><th>平仓时间</th><th>持仓小时</th><th>净损益（USDT）</th><th>退出</th><th>入场3天情景价</th><th>入场7天情景价</th><th>入场30天情景价</th></tr></thead>
    <tbody>{trades.map((trade, index) => <tr key={`${String(trade.position_id)}-${index}`}>
      <td>{String(trade.asset_id ?? "—")}</td><td>{Number(trade.direction) > 0 ? "多" : "空"}</td>
      <td>{new Date(Number(trade.entry_time)).toLocaleString()}</td><td>{new Date(Number(trade.exit_time)).toLocaleString()}</td>
      <td>{String(trade.holding_hours ?? "—")}</td><td>{String(trade.net_pnl ?? "—")}</td><td>{String(trade.exit_reason ?? "—")}</td>
      {["72h", "168h", "720h"].map((horizon) => <td key={horizon}>{String(trade[`scenario_${horizon}_center`] ?? "—")}<small className="muted"> [{String(trade[`scenario_${horizon}_lower`] ?? "—")} – {String(trade[`scenario_${horizon}_upper`] ?? "—")} ]<br />事后实际 {String(trade[`actual_${horizon}_close`] ?? "—")}（{String(trade[`actual_${horizon}_return_pct`] ?? "—")}%）</small></td>)}
    </tr>)}</tbody></table>{trades.length === 0 && <p>该次回测没有成交。</p>}</div>;
}
