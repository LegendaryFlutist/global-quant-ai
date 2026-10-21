import { useEffect, useState } from "react";
import type { Job } from "../types";
import { api, date, post } from "../services/api";
import { datasetDateError, datasetProviderLabel, retainDatasetSelection, type DatasetBars, type DatasetCatalogItem, type DatasetVersion } from "../services/datasets";
import { badge } from "../components/StatusBadge";
import DatasetCloseChart from "../components/DatasetCloseChart";

/** Manage source-native immutable versions; requests collect public history or freeze a local snapshot only. */
export default function DatasetsPage({ timezone, jobs, onTask, onJobs }: {
  timezone: string;
  jobs: Job[];
  onTask: (message: string) => void;
  onJobs: () => void;
}) {
  const today = new Date().toISOString().slice(0, 10);
  const [catalog, setCatalog] = useState<DatasetCatalogItem[]>([]);
  const [selection, setSelection] = useState("");
  const [startDate, setStartDate] = useState(new Date(Date.now() - 365 * 86_400_000).toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(today);
  const [versions, setVersions] = useState<DatasetVersion[]>([]);
  const [versionId, setVersionId] = useState("");
  const [bars, setBars] = useState<DatasetBars | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [loadingVersions, setLoadingVersions] = useState(false);
  const [loadingBars, setLoadingBars] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const item = catalog.find((entry) => `${entry.asset_id}|${entry.provider_id}` === selection);
  const selectedJobs = jobs.filter((job) => job.kind === "INGEST_DATASET" && job.parameters.asset_id === item?.asset_id && job.parameters.provider_id === item?.provider_id);
  const completedTasks = selectedJobs.filter((job) => job.status === "SUCCEEDED").map((job) => job.id).join(",");
  const dateError = datasetDateError(startDate, endDate, today);
  const visibleBars = bars?.version.id === versionId && bars.version.asset_id === item?.asset_id && bars.version.provider_id === item?.provider_id ? bars : null;
  const selectedVersion = versions.find((version) => version.id === versionId && version.asset_id === item?.asset_id && version.provider_id === item?.provider_id);

  useEffect(() => {
    let active = true;
    api<{ items: DatasetCatalogItem[]; live_enabled: boolean }>("/datasets/catalog")
      .then((value) => {
        if (!active) return;
        setCatalog(value.items);
        setSelection((current) => value.items.some((entry) => `${entry.asset_id}|${entry.provider_id}` === current)
          ? current : value.items[0] ? `${value.items[0].asset_id}|${value.items[0].provider_id}` : "");
      })
      .catch((cause) => { if (active) setError(`数据目录读取失败：${(cause as Error).message}`); });
    return () => { active = false; };
  }, [refresh]);

  useEffect(() => {
    setVersions([]);
    setVersionId("");
    setBars(null);
  }, [selection]);

  useEffect(() => {
    if (!item) return;
    let active = true;
    setLoadingVersions(true);
    setError("");
    api<{ versions: DatasetVersion[] }>(`/datasets?asset_id=${encodeURIComponent(item.asset_id)}`)
      .then((value) => {
        if (!active) return;
        const matching = value.versions.filter((version) => version.provider_id === item.provider_id);
        setVersions(matching);
        setVersionId((current) => retainDatasetSelection(matching, current));
      })
      .catch((cause) => { if (active) setError(`数据版本读取失败：${(cause as Error).message}`); })
      .finally(() => { if (active) setLoadingVersions(false); });
    return () => { active = false; };
  }, [item?.asset_id, item?.provider_id, refresh, completedTasks]);

  useEffect(() => {
    if (!versionId) {
      setLoadingBars(false);
      setBars(null);
      return;
    }
    let active = true;
    setLoadingBars(true);
    setBars(null);
    api<DatasetBars>(`/datasets/${encodeURIComponent(versionId)}/bars`)
      .then((value) => { if (active) setBars(value); })
      .catch((cause) => { if (active) setError(`版本行情读取失败：${(cause as Error).message}`); })
      .finally(() => { if (active) setLoadingBars(false); });
    return () => { active = false; };
  }, [versionId]);

  /** Queue a server-validated source/date request and retain the old versions while the task runs. */
  async function collect() {
    if (!item || dateError || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const task = await post<{ id: string; status: string }>("/datasets/ingest", {
        asset_id: item.asset_id, provider_id: item.provider_id, start_date: startDate, end_date: endDate,
      });
      onTask(`数据任务 ${task.id.slice(0, 8)} 已登记；请查看后台任务的实际状态，入队不代表采集成功。`);
    } catch (cause) {
      setError(`数据任务提交失败：${(cause as Error).message}`);
    } finally {
      setSubmitting(false);
    }
  }

  return <>
    <header className="page-heading">
      <div><span className="eyebrow">VERSIONED MARKET DATA</span><h1>数据版本</h1><p>原生周期历史行情 · 精确产品身份 · 不可变版本与来源记录</p></div>
      <button onClick={() => setRefresh((value) => value + 1)}>刷新本地版本</button>
    </header>
    {error && <div role="alert" className="alert error">{error}</div>}
    <section className="panel">
      <div className="dataset-controls">
        <label>产品与来源<select aria-label="产品与来源" value={selection} onChange={(event) => setSelection(event.target.value)}>
          {!catalog.length && <option value="">等待数据目录</option>}
          {catalog.map((entry) => <option key={`${entry.asset_id}|${entry.provider_id}`} value={`${entry.asset_id}|${entry.provider_id}`}>{entry.name} · {datasetProviderLabel(entry.provider_id)} · {entry.interval}</option>)}
        </select></label>
        <label>开始交易日<input aria-label="开始交易日" type="date" value={startDate} max={endDate || today} onChange={(event) => setStartDate(event.target.value)} /></label>
        <label>结束交易日<input aria-label="结束交易日" type="date" value={endDate} min={startDate} max={today} onChange={(event) => setEndDate(event.target.value)} /></label>
        <button className="primary" disabled={!item || Boolean(dateError) || submitting} onClick={() => void collect()}>{submitting ? "正在提交…" : item?.provider_id === "legacy_binance_snapshot" ? "固定已有快照版本" : "采集并保存版本"}</button>
      </div>
      {dateError && <p className="error">{dateError}</p>}
      {item && <>
        <p className="mono break">{item.asset_id} · {item.product_type === "perpetual" ? "永续合约" : item.product_type === "spot" ? "现货" : item.product_type} · {item.currency} · {item.interval} · 交易日历 {item.calendar_id}</p>
        <p>更新口径：{item.freshness}</p>
        <ul className="dataset-limitations">{(Array.isArray(item.limitations) ? item.limitations : [item.limitations]).map((limit, index) => <li key={index}>{limit}</li>)}</ul>
        <small>日线日期按来源交易日解释，永续快照按UTC；增量版本保留同源已有历史，实际覆盖以版本为准。不从其他产品补造K线。本页为历史研究数据，未启动实时交易或模型训练。</small>
      </>}
    </section>
    <section className="panel">
      <div className="section-title"><h2>采集任务</h2><button onClick={onJobs}>查看全部后台任务</button></div>
      {selectedJobs.length ? <ul className="plain-list">{selectedJobs.slice(0, 5).map((job) => <li key={job.id}><span>{job.id.slice(0, 8)} · {date(job.created_at, timezone)}{typeof job.result.error === "string" && <small className="error" style={{ display: "block" }}>{job.result.error}</small>}</span>{badge(job.status)}</li>)}</ul>
        : <p>当前产品与来源暂无任务。提交后由后台执行，状态约每10秒更新。</p>}
    </section>
    <section className="panel">
      <h2>已保存版本</h2>
      {loadingVersions && <p role="status">正在读取本地版本…</p>}
      {!loadingVersions && !versions.length && <p>尚无本地版本。真实数据源可能受网络、覆盖范围或使用权限限制；失败会保留任务原因。</p>}
      {versions.length > 0 && <label className="dataset-version-select">选择固定版本<select aria-label="选择固定版本" value={versionId} onChange={(event) => setVersionId(event.target.value)}>{versions.map((version) => <option key={version.id} value={version.id}>{version.id.slice(0, 12)} · {version.row_count} 根 · 观测 {date(version.observed_at_ms, timezone)}</option>)}</select></label>}
      {selectedVersion && <>
        <p>实际覆盖：{date(selectedVersion.first_open_ms, timezone)} 至 {date(selectedVersion.last_close_ms, timezone)} · {selectedVersion.row_count} 根 · {selectedVersion.interval}</p>
        <small className="mono break">SHA256：{selectedVersion.sha256}</small>
        <div className="two-col dataset-evidence">
          <div><h3>质量记录</h3><pre>{JSON.stringify(selectedVersion.quality, null, 2)}</pre></div>
          <details><summary>查看来源与已知时间依据</summary><pre>{JSON.stringify(selectedVersion.provenance, null, 2)}</pre></details>
        </div>
      </>}
    </section>
    <section className="panel chart-panel">
      <div className="chart-toolbar"><h2>版本收盘价</h2><small>日线按源交易日显示；盘中按UTC时间显示；不跨交易时段聚合。</small></div>
      {loadingBars ? <div role="status" className="empty">正在读取固定版本的K线…</div>
        : visibleBars?.candles.length && item ? <><DatasetCloseChart bars={visibleBars.candles} interval={visibleBars.version.interval} currency={item.currency} /><div className="chart-footer"><span>{visibleBars.candles.length} 根原生K线 · {item.currency}</span><span>版本 {visibleBars.version.id.slice(0, 12)} · 曲线为历史价格</span></div></>
          : <div className="empty">选择一个已保存版本后显示真实收盘价。</div>}
    </section>
  </>;
}
