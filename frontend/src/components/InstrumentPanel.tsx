import { useEffect, useState } from "react";
import { api, date } from "../services/api";
import { instrumentQuery, specificationValue, type InstrumentHistory, type InstrumentResolution } from "../services/instruments";

/** Inspect versioned rules on demand; old queries are aborted when switching products or cutoffs. */
export default function InstrumentPanel({ assetId, timezone, onClose }: {
  assetId: string; timezone: string; onClose: () => void;
}) {
  const [effective, setEffective] = useState("");
  const [known, setKnown] = useState("");
  const [query, setQuery] = useState(() => instrumentQuery(assetId, "", ""));
  const [history, setHistory] = useState<InstrumentHistory | null>(null);
  const [resolved, setResolved] = useState<InstrumentResolution | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(""); setResolved(null); setHistory(null);
    Promise.all([
      api<InstrumentHistory>(`/instruments/history?${new URLSearchParams({ asset_id: assetId })}`, { signal: controller.signal }),
      api<InstrumentResolution>(`/instruments/specification?${query}`, { signal: controller.signal }),
    ]).then(([versions, result]) => {
      if (!controller.signal.aborted) { setHistory(versions); setResolved(result); }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "规格读取失败");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [assetId, query]);
  const spec = resolved?.specification;
  return <section className="panel" aria-label="产品规格历史">
    <header className="page-heading"><div><h2>{assetId} · 产品规格</h2>
      <p>按生效时间和系统已知时间查询。登记及规则核验不代表已有行情或可以交易。</p></div>
      <button onClick={onClose}>关闭规格</button></header>
    <form className="filters" onSubmit={(event) => {
      event.preventDefault();
      try { setQuery(instrumentQuery(assetId, effective, known)); } catch (reason) { setError(String(reason)); }
    }}>
      <label>规则生效时点（UTC，空白为现在）<input type="datetime-local" step="1" value={effective} onChange={(event) => setEffective(event.target.value)} /></label>
      <label>系统已知截止（UTC，空白为现在）<input type="datetime-local" step="1" value={known} onChange={(event) => setKnown(event.target.value)} /></label>
      <button type="submit">查询历史规格</button>
    </form>
    {loading && <p role="status">正在校验规格版本…</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {!loading && resolved && <>
      <p>{spec ? "该时点存在研究规格" : resolved.status === "NOT_KNOWN" ? "系统在该截止时点尚无规格记录" : "该日期没有已核验规格，不沿用当前规则"}</p>
      {resolved.version_id && <small className="mono">版本：{resolved.version_id} · 实际入库：{date(resolved.recorded_at, timezone)}</small>}
      {spec && <div className="table-panel"><table><thead><tr><th>币种 / 报价 / 结算</th><th>交易单位 / 价位</th><th>交收与卖出限制</th></tr></thead>
        <tbody><tr><td>{spec.currency} / {spec.quote_currency} / {specificationValue(spec.settlement_currency)}</td>
          <td>{specificationValue(spec.lot_size)} / {specificationValue(spec.tick_size)}<small>价位表：{specificationValue(spec.tick_rule)}</small></td>
          <td>现金交收：{specificationValue(spec.cash_settlement)}<small>证券交收：{specificationValue(spec.security_settlement)}</small><small>购入后卖出限制：{specificationValue(spec.resale_restriction)}</small></td></tr></tbody></table><p>{spec.note}</p></div>}
    </>}
    {history?.versions.length === 0 && <p>该产品尚未导入版本化规格。现有图表与历史报告保留。</p>}
    {history?.versions.map((version) => <details key={version.id}>
      <summary>已保存版本 {version.id.slice(0, 12)} · {date(version.recorded_at, timezone)}</summary>
      <p>{version.timeline.mic} · ISIN {version.timeline.isin ?? "未核验"} · {version.timeline.timezone} · 上市日 {version.timeline.listing_date ?? "未知"}</p>
      <p>日历 {version.timeline.calendar_id}：{version.timeline.calendar_status === "PLANNED" ? "聚合尚未实现" : "已核验"}</p>
      <p>{version.timeline.note}</p>
      <table><thead><tr><th>生效范围（结束不含）</th><th>交易单位</th><th>价位规则</th></tr></thead><tbody>
        {version.timeline.periods.map((period) => <tr key={period.effective_from}><td>{date(period.effective_from, timezone)} → {period.effective_to ? date(period.effective_to, timezone) : "未指定结束"}</td><td>{specificationValue(period.lot_size)}</td><td>{specificationValue(period.tick_rule)}</td></tr>)}
      </tbody></table>
      {version.timeline.aliases.map((alias) => <p key={`${alias.provider_id}-${alias.effective_from}`}>来源代码：{alias.provider_id} / {alias.alias} · {date(alias.effective_from, timezone)} → {alias.effective_to ? date(alias.effective_to, timezone) : "未指定结束"}</p>)}
      {version.timeline.evidence.map((evidence) => <p key={evidence.url}><a href={evidence.url} target="_blank" rel="noreferrer">{evidence.title}</a> · 核验 {evidence.checked_on}<small>{evidence.scope}</small></p>)}
    </details>)}
  </section>;
}
