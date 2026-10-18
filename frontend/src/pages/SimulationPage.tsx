import { useEffect, useState } from "react";
import type { Asset, SimulationAccount } from "../types";
import { api, post } from "../services/api";

type WeightKey = keyof SimulationAccount["configuration"]["weights"];
type Draft = Omit<SimulationAccount, "id" | "configuration_sha256" | "revision" | "created_at" | "updated_at" | "research_only" | "backtest_implemented">;
const INTERVAL_CHOICES = ["15m", "30m", "1h", "2h", "4h", "1d"];
const WEIGHTS: { key: WeightKey; label: string }[] = [
  { key: "trend", label: "趋势" }, { key: "dow123", label: "道氏 1-2-3" },
  { key: "wave", label: "波浪候选" }, { key: "divergence", label: "RSI 背离" },
  { key: "reversal_patterns", label: "反转形态" }, { key: "deep_model", label: "深度模型" },
];

/** 创建默认权重和保守研究风险政策，不隐式启用未登记深度模型。 */
function emptyDraft(): Draft {
  return {
    name: "", initial_equity: "10000", base_currency: "USDT",
    configuration: {
      asset_ids: ["binance_usdm:KORUUSDT", "binance_usdm:CLUSDT", "binance_usdm:NVDAUSDT", "binance_usdm:BTCUSDT"],
      intervals: ["15m", "1h", "4h"],
      weights: { trend: "0.40", dow123: "0.25", wave: "0.10", divergence: "0.15", reversal_patterns: "0.10", deep_model: "0" },
      entry_threshold: "0.70", max_leverage: "3", max_margin_fraction: "0.10",
      max_holding_hours: 168, exit_mode: "three_bar_trailing", deep_model_version: null,
    },
  };
}

/** 提供持久化的研究配置账号创建/编辑；当前阶段不创建成交或收益曲线。 */
export default function SimulationPage() {
  const [accounts, setAccounts] = useState<SimulationAccount[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(false);
  const [datasetLoadingId, setDatasetLoadingId] = useState<string | null>(null);
  /** 从已认证服务读取当前用户的配置账号。 */
  async function refreshAccounts() {
    setLoading(true);
    try {
      setAccounts(await api<SimulationAccount[]>("/simulation/accounts"));
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
      setSaved("模拟配置已保存；收益回测功能尚未接入。");
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
      setSaved(`已排队 ${result.tasks.length} 个公开数据任务；进度请在“后台任务”查看。任务完成前不会生成回测收益。`);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setDatasetLoadingId(null);
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
  return (
    <>
      <header className="page-heading">
        <div><span className="eyebrow">ISOLATED RESEARCH CONFIGS</span><h1>模拟账号与模型组合</h1><p>资金基数和理论权重分别保存；此处是回测配置账号，不生成成交、实时收益或交易订单。</p></div>
        <button onClick={() => void refreshAccounts()} disabled={loading}>刷新账号</button>
      </header>
      {error && <div role="alert" className="alert error">{error}</div>}
      {saved && <div role="status" className="alert">{saved}</div>}
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
          <div className="filters">
            <label>模拟最大杠杆（1～20）<input inputMode="decimal" value={draft.configuration.max_leverage} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, max_leverage: event.target.value } })} /></label>
            <label>单次保证金比例（≤25%）<input inputMode="decimal" value={draft.configuration.max_margin_fraction} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, max_margin_fraction: event.target.value } })} /></label>
            <label>持仓上限小时（24～504）<input inputMode="numeric" value={draft.configuration.max_holding_hours} onChange={(event) => setDraft({ ...draft, configuration: { ...draft.configuration, max_holding_hours: Number(event.target.value) } })} /></label>
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
            <p>周期：{account.configuration.intervals.join(" / ")} · 最大杠杆 {account.configuration.max_leverage}× · 持仓上限 {account.configuration.max_holding_hours} 小时</p>
            <p>权重：{WEIGHTS.map(({ key, label }) => `${label} ${account.configuration.weights[key]}`).join(" · ")}</p>
            <small className="mono break">配置 SHA256：{account.configuration_sha256}</small>
            <div className="actions">
              <button onClick={() => void queueDerivativeData(account)} disabled={datasetLoadingId !== null}>
                {datasetLoadingId === account.id ? "提交中…" : "按需采集三年标记价与资金费率"}
              </button>
            </div>
            <p className="muted">隔离研究配置 · 行情需由你点击后才采集；当前尚未实现回测收益/成交账本。</p>
          </article>)}
        </section>
      </div>
    </>
  );
}
