import { useEffect, useState } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent, DataZoomComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { api, post } from "../services/api";

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer]);

const SYMBOLS = ["KORUUSDT", "CLUSDT", "NVDAUSDT", "BTCUSDT"];
const MODELS = ["analysis_dow123", "torch_gru", "pattern_only", "logistic", "random_forest", "extra_trees", "hist_gradient", "mlp"];
const TIMEFRAMES = ["15m", "30m", "1h", "2h", "4h"];
const EXITS = ["swing_percent", "fixed_rr", "atr_trailing", "three_bar_breakout", "three_bar_trailing", "time_exit", "fixed_three_bar_target"];
const MODEL_LABELS: Record<string, string> = {
  analysis_dow123: "道氏 1-2-3", torch_gru: "PyTorch GRU", pattern_only: "反转形态",
  logistic: "逻辑回归", random_forest: "随机森林", extra_trees: "极端随机树",
  hist_gradient: "直方图梯度提升", mlp: "多层感知机",
};
const EXIT_LABELS: Record<string, string> = {
  swing_percent: "波段百分比", fixed_rr: "固定盈亏比", atr_trailing: "ATR 跟踪",
  three_bar_breakout: "三根K线突破", three_bar_trailing: "三根K线跟踪",
  time_exit: "时间退出", fixed_three_bar_target: "三根K线目标",
};

type Progress = { percent: number; phase: string; symbol?: string; timeframe?: string; model?: string; fold?: number };
type Run = { id: string; status: string; kind?: string; parameters: { symbols: string[]; models: string[]; timeframes: string[] }; progress: Progress; error?: string | null; created_at: string };
type Summary = { models: string[]; symbols: string[]; configurations: number; sources: { symbol: string; status: string; configurations: number }[] };
type Ranking = { timeframe: string; model: string; exit_mode: string; net_return: number; max_drawdown: number; entries: number; liquidations: number; validation_score: number; eligible: boolean; rejection_reasons: string[]; holding_hours_mean: number; holding_hours_median: number; holding_hours_max: number };
type ModelOperation = { position_id?: string; direction?: string; entry_time?: string; exit_time?: string; holding_hours?: string; entry_price?: string; exit_price?: string; net_pnl?: string; exit_reason?: string; partial_exit_count?: string };
type ModelReport = { symbol: string; model: string; status: string; dataset_sha256: string; selected: Ranking | null; holdout: Record<string, number> | null; rankings: Ranking[]; curve: [number, number][]; curve_key: string; operations: ModelOperation[]; real_orders_enabled: false };

/** 绘制指定模型验证期权益曲线；输入时间戳为毫秒、权益为初始权益倍数。 */
function ResearchCurve({ points }: { points: [number, number][] }) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!element) return;
    const chart = echarts.init(element, "dark");
    chart.setOption({ backgroundColor: "#111927", tooltip: { trigger: "axis" }, grid: { left: 60, right: 20, top: 20, bottom: 65 },
      xAxis: { type: "time" }, yAxis: { type: "value", scale: true, name: "权益倍数" },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 16 }],
      series: [{ type: "line", name: "验证期权益", showSymbol: false, data: points }],
    });
    /** 跟随面板尺寸变化重绘，并在组件卸载时释放图表资源。 */
    function resize() { chart.resize(); }
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [element, points]);
  return <div ref={setElement} style={{ height: 280 }} data-testid="full-research-equity-chart" />;
}

/** 提供整套 GPU 多模型回测的任务启动、真实进度轮询和分模型分标的报告浏览。 */
export default function FullResearchBacktests() {
  const [symbols, setSymbols] = useState(SYMBOLS);
  const [models, setModels] = useState(MODELS);
  const [timeframes, setTimeframes] = useState(TIMEFRAMES);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [symbol, setSymbol] = useState(SYMBOLS[0]);
  const [model, setModel] = useState(MODELS[0]);
  const [timeframe, setTimeframe] = useState("15m");
  const [exitMode, setExitMode] = useState("swing_percent");
  const [report, setReport] = useState<ModelReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  /** 拉取用户私有任务列表，并载入最新已完成报告的目录摘要。 */
  async function refreshRuns() {
    const rows = await api<Run[]>("/research/backtests");
    setRuns(rows);
    const current = rows.find((row) => row.id === selectedRun) ?? rows.find((row) => row.status === "SUCCEEDED");
    if (current && current.id !== selectedRun) setSelectedRun(current.id);
  }
  useEffect(() => {
    void refreshRuns().catch((cause) => setError((cause as Error).message));
  }, []);
  useEffect(() => {
    if (!runs.some((run) => ["QUEUED", "RUNNING", "RETRYING"].includes(run.status))) return;
    const timer = window.setInterval(() => { void refreshRuns().catch((cause) => setError((cause as Error).message)); }, 2000);
    return () => window.clearInterval(timer);
  }, [runs, selectedRun]);
  useEffect(() => {
    const active = runs.find((run) => run.id === selectedRun);
    if (!active || active.status !== "SUCCEEDED") { setSummary(null); setReport(null); return; }
    const summaryPath = active.id.startsWith("archive:")
      ? `/research/archives/${encodeURIComponent(active.id.slice(8))}/summary`
      : `/research/backtests/${encodeURIComponent(active.id)}/summary`;
    api<Summary>(summaryPath)
      .then((value) => { setSummary(value); setSymbol(value.symbols[0] ?? SYMBOLS[0]); setModel(value.models[0] ?? MODELS[0]); })
      .catch((cause) => setError((cause as Error).message));
  }, [runs, selectedRun]);
  useEffect(() => {
    const active = runs.find((run) => run.id === selectedRun);
    if (!active || active.status !== "SUCCEEDED" || !summary || !summary.symbols.includes(symbol) || !summary.models.includes(model)) return;
    const query = new URLSearchParams({ timeframe, exit_mode: exitMode });
    const base = active.id.startsWith("archive:")
      ? `/research/archives/${encodeURIComponent(active.id.slice(8))}`
      : `/research/backtests/${encodeURIComponent(active.id)}`;
    api<ModelReport>(`${base}/assets/${encodeURIComponent(symbol)}/models/${encodeURIComponent(model)}?${query}`)
      .then(setReport).catch((cause) => setError((cause as Error).message));
  }, [runs, selectedRun, summary, symbol, model, timeframe, exitMode]);

  /** 在服务端固定的GPU执行器中排队用户选定的本地历史回测范围。 */
  async function startRun() {
    setBusy(true); setError("");
    try {
      const created = await post<{ id: string }>("/research/backtests", { symbols, models, timeframes });
      setSelectedRun(created.id);
      await refreshRuns();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  /** 切换白名单复选项，拒绝空选择以符合API最小任务范围。 */
  function toggle(values: string[], value: string, update: (items: string[]) => void) {
    const next = values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
    if (next.length) update(next);
  }
  const active = runs.find((run) => run.id === selectedRun);
  const progress = active?.progress;
  const filteredRankings = report?.rankings.filter((row) => row.timeframe === timeframe) ?? [];
  return <section className="panel full-research-panel">
    <div className="section-title"><div><span className="eyebrow">GPU MULTI-MODEL RESEARCH</span><h2>全模型 · 全标的回测</h2></div><button onClick={() => void refreshRuns()} disabled={busy}>刷新任务</button></div>
    <p>从系统页面运行 8 种研究模型，按标的分别生成报告、验证期权益曲线和逐周期/止盈模式排名。仅使用已验证本地行情，邮件为模拟记录，不发送委托。</p>
    {error && <div role="alert" className="alert error">{error}</div>}
    <div className="filters">
      <fieldset><legend>回测标的</legend>{SYMBOLS.map((value) => <label className="toggle" key={value}><input type="checkbox" checked={symbols.includes(value)} onChange={() => toggle(symbols, value, setSymbols)} />{value}</label>)}</fieldset>
      <fieldset><legend>模型</legend>{MODELS.map((value) => <label className="toggle" key={value}><input type="checkbox" checked={models.includes(value)} onChange={() => toggle(models, value, setModels)} />{MODEL_LABELS[value]}</label>)}</fieldset>
      <fieldset><legend>入场周期</legend>{TIMEFRAMES.map((value) => <label className="toggle" key={value}><input type="checkbox" checked={timeframes.includes(value)} onChange={() => toggle(timeframes, value, setTimeframes)} />{value}</label>)}</fieldset>
    </div>
    <div className="actions"><button onClick={() => void startRun()} disabled={busy || !!runs.find((run) => ["QUEUED", "RUNNING", "RETRYING"].includes(run.status))}>{busy ? "提交中…" : "开始整套回测"}</button>
      <label>任务与已完成报告<select value={selectedRun} onChange={(event) => setSelectedRun(event.target.value)}><option value="">选择回测任务</option>{runs.map((run) => <option key={run.id} value={run.id}>{new Date(run.created_at).toLocaleString()} · {run.kind === "ARCHIVED_REPORT" ? "已有报告" : run.status} · {run.id.slice(0, 16)}</option>)}</select></label>
    </div>
    {active && <article className="panel">
      <div className="section-title"><strong>{active.status === "SUCCEEDED" ? "回测完成" : active.status === "FAILED" ? "回测失败" : "回测运行中"}</strong><small>{progress?.percent ?? 0}%</small></div>
      <progress aria-label="全模型回测进度" value={progress?.percent ?? 0} max={100} style={{ width: "100%", height: 18 }} />
      <p>{active.status === "FAILED" ? active.error : `${progress?.phase ?? "queued"}${progress?.symbol ? ` · ${progress.symbol}` : ""}${progress?.timeframe ? ` · ${progress.timeframe}` : ""}${progress?.model ? ` · ${MODEL_LABELS[progress.model] ?? progress.model}` : ""}${progress?.fold ? ` · 验证折 ${progress.fold}` : ""}`}</p>
      {summary && <p>总体报告：{summary.symbols.length} 个标的 · {summary.models.length} 个模型 · {summary.configurations} 组周期/退出配置。每个标的都单独保留模型报告和成交明细。</p>}
      {active.status === "FAILED" && <p className="muted">部分已完成文件会保留在本机运行目录；调整范围后可重新提交任务。</p>}
    </article>}
    {summary && active?.status === "SUCCEEDED" && <>
      <div className="filters">
        <label>标的<select value={symbol} onChange={(event) => setSymbol(event.target.value)}>{summary.symbols.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>模型<select value={model} onChange={(event) => setModel(event.target.value)}>{summary.models.map((value) => <option key={value} value={value}>{MODEL_LABELS[value] ?? value}</option>)}</select></label>
        <label>周期<select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>{TIMEFRAMES.map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>退出模式<select value={exitMode} onChange={(event) => setExitMode(event.target.value)}>{EXITS.map((value) => <option key={value} value={value}>{EXIT_LABELS[value]}</option>)}</select></label>
      </div>
      {report && <>
        <div className="metrics-grid">
          <article className="metric"><span>模型状态</span><strong>{report.status}</strong></article>
          <article className="metric"><span>模型选中配置</span><strong>{report.selected ? `${report.selected.timeframe} · ${EXIT_LABELS[report.selected.exit_mode]}` : "无合格配置"}</strong></article>
          <article className="metric"><span>留出期净收益</span><strong>{report.holdout ? `${(100 * Number(report.holdout.net_return ?? 0)).toFixed(2)}%` : "未触碰留出集"}</strong></article>
          <article className="metric"><span>权益曲线配置</span><strong>{report.curve_key}</strong></article>
        </div>
        <ResearchCurve points={report.curve} />
        <div className="table-wrap"><table><thead><tr><th>周期</th><th>止盈模式</th><th>验证净收益</th><th>最大回撤</th><th>开仓数</th><th>平均持仓时长</th><th>合格</th><th>筛选结果</th></tr></thead>
          <tbody>{filteredRankings.map((row) => <tr key={`${row.timeframe}-${row.exit_mode}`}><td>{row.timeframe}</td><td>{EXIT_LABELS[row.exit_mode]}</td><td>{(100 * row.net_return).toFixed(2)}%</td><td>{(100 * row.max_drawdown).toFixed(2)}%</td><td>{row.entries}</td><td>{row.holding_hours_mean.toFixed(1)} 小时</td><td>{row.eligible ? "是" : "否"}</td><td>{row.rejection_reasons.join("；") || "—"}</td></tr>)}</tbody>
        </table></div>
        <h3>所选配置模拟操作（{report.operations.length} 笔）</h3>
        <div className="table-wrap"><table><thead><tr><th>方向</th><th>开仓时间</th><th>平仓时间</th><th>持仓小时</th><th>开仓价</th><th>平仓价</th><th>净损益</th><th>退出原因</th><th>分批退出次数</th></tr></thead>
          <tbody>{report.operations.map((trade, index) => <tr key={`${trade.position_id ?? "position"}-${index}`}>
            <td>{Number(trade.direction) > 0 ? "多" : "空"}</td>
            <td>{trade.entry_time ? new Date(Number(trade.entry_time)).toLocaleString() : "—"}</td>
            <td>{trade.exit_time ? new Date(Number(trade.exit_time)).toLocaleString() : "—"}</td>
            <td>{trade.holding_hours ?? "—"}</td><td>{trade.entry_price ?? "—"}</td><td>{trade.exit_price ?? "—"}</td>
            <td>{trade.net_pnl ?? "—"}</td><td>{trade.exit_reason ?? "—"}</td><td>{trade.partial_exit_count ?? "0"}</td>
          </tr>)}</tbody></table>{report.operations.length === 0 && <p className="muted">所选配置没有完整平仓记录。</p>}</div>
        <small className="mono break">数据 SHA256：{report.dataset_sha256}</small>
      </>}
    </>}
  </section>;
}
