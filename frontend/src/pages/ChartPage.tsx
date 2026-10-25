import { useEffect, useState } from "react";
import { DEFAULT_CHART_INDICATORS, type ArchiveResult, type Asset, type Candle, type CandleResult, type ChartIndicators, type Interval, type LiveMarketResult, type MarketObservationSeries, type StructureResult } from "../types";
import { api, date, post } from "../services/api";
import PriceChart from "../components/PriceChart";
import ReferenceSeriesChart from "../components/ReferenceSeriesChart";
import EnergyHealthPanel from "../components/EnergyHealthPanel";
/** 按标的/周期读取已校验历史；切换后忽略旧请求，采集作为独立后台任务。 */
export default function ChartPage({
  assets,
  initialAsset,
  initialInterval,
  timezone,
  onTask,
  indicatorPreferences,
}: {
  assets: Asset[];
  initialAsset: string;
  initialInterval: string;
  timezone: string;
  onTask: (message: string) => void;
  indicatorPreferences: ChartIndicators;
}) {
  const [assetId, setAssetId] = useState(initialAsset);
  const [interval, setInterval] = useState(initialInterval);
  const [intervals, setIntervals] = useState<Interval[]>([]);
  const [data, setData] = useState<CandleResult | null>(null);
  const [archive, setArchive] = useState<ArchiveResult | null>(null);
  const [structure, setStructure] = useState<StructureResult | null>(null);
  const [series, setSeries] = useState<MarketObservationSeries | null>(null);
  const [live, setLive] = useState<LiveMarketResult | null>(null);
  const [showArchive, setShowArchive] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [indicators, setIndicators] = useState<ChartIndicators>(indicatorPreferences);
  const [savedIndicators, setSavedIndicators] = useState<ChartIndicators>(indicatorPreferences);
  const [maPeriodsText, setMAPeriodsText] = useState(indicatorPreferences.ma_periods.join(","));
  const [indicatorPanelOpen, setIndicatorPanelOpen] = useState(false);
  const [reload, setReload] = useState(0);
  const asset = assets.find((a) => a.id === assetId);
  const sourceBinding = asset?.specification.market_data_source as {
    provider_id?: string; kind?: "candles" | "observations"; intervals?: string[]; history_days?: Record<string, number>;
  } | undefined;
  const isEnergyReference = sourceBinding?.provider_id === "yahoo_energy";
  const relatedReferences = assets.filter((item) => item.id !== assetId && item.underlying_id === asset?.underlying_id
    && ["YAHOO_REFERENCE", "EIA"].includes(item.venue));
  const isObservationSeries = sourceBinding?.kind === "observations";
  const isUnderlyingCandle = sourceBinding?.kind === "candles";
  useEffect(() => {
    setIndicators(indicatorPreferences);
    setSavedIndicators(indicatorPreferences);
    setMAPeriodsText(indicatorPreferences.ma_periods.join(","));
  }, [indicatorPreferences]);
  const verifiedBinanceCount = assets.filter((item) =>
    item.venue === "BINANCE_USDM" && ["VERIFIED", "VERIFIED_SNAPSHOT"].includes(item.verification),
  ).length;
  useEffect(() => {
    let active = true;
    if (isObservationSeries) {
      setIntervals([{ interval: "1d", available: true, reason: "仅为EIA发布的日度标量参考值" }]);
      setInterval("1d");
      return () => { active = false; };
    }
    api<Interval[]>("/market/intervals?asset_id=" + encodeURIComponent(assetId))
      .then((v) => {
        if (active) {
          setIntervals(v);
          const allowed = sourceBinding?.intervals ?? [];
          if (allowed.length && !allowed.includes(interval)) setInterval(allowed[0]);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [assetId, reload, isObservationSeries, sourceBinding?.intervals?.join(","), interval]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setData(null);
    setArchive(null);
    setStructure(null);
    setLive(null);
    setSeries(null);
    const query = `?asset_id=${encodeURIComponent(assetId)}&interval=${interval}&limit=10000`;
    if (isObservationSeries) {
      api<MarketObservationSeries>(`/market/series?asset_id=${encodeURIComponent(assetId)}&limit=5000`)
        .then((value) => { if (active) setSeries(value); })
        .catch((e) => { if (active) setError(e.message); })
        .finally(() => { if (active) setLoading(false); });
      return () => { active = false; };
    }
    if (showArchive) {
      const relatedIntervals = [...new Set([interval, "15m", "1h", "4h", "1d"])]
        .slice(0, 10).join(",");
      Promise.all([
        api<ArchiveResult>(`/market/archive${query}`),
        api<StructureResult>(`/market/structure?asset_id=${encodeURIComponent(assetId)}&intervals=${encodeURIComponent(relatedIntervals)}&limit=10000`),
      ])
        .then(([archiveValue, structureValue]) => {
          if (!active) return;
          setArchive(archiveValue);
          setStructure(structureValue);
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    } else {
      const relatedIntervals = [...new Set([interval, "15m", "1h", "4h", "1d"])].slice(0, 4).join(",");
      Promise.all([
        api<CandleResult>(`/market/candles${query}`),
        api<StructureResult>(`/market/structure?asset_id=${encodeURIComponent(assetId)}&intervals=${encodeURIComponent(relatedIntervals)}&limit=10000`),
      ])
        .then(([value, structureValue]) => {
          if (!active) return;
          setData(value);
          setStructure(structureValue);
        })
        .catch((e) => {
          if (active) setError(e.message);
        })
        .finally(() => {
          if (active) setLoading(false);
        });
    }
    return () => {
      active = false;
    };
  }, [assetId, interval, reload, showArchive, isObservationSeries]);
  const canRefresh = !isObservationSeries && (isEnergyReference || (showArchive ? archive?.available === true : Boolean(data?.candles.length)));
  useEffect(() => {
    if (!canRefresh) return;
    let active = true;
    let timer = 0;
    let retryDelay = 5000;
    let waitingForHistory = isEnergyReference;
    /** Refresh the selected verified cycle, waiting five seconds after each request settles. */
    async function refreshSelectedCycle() {
      try {
        const result = await post<LiveMarketResult>("/market/live", { asset_id: assetId, interval });
        if (!active) return;
        retryDelay = 5000;
        setLive(result);
        if (waitingForHistory && result.closed_candles.length) {
          const history = await api<CandleResult>(`/market/candles?asset_id=${encodeURIComponent(assetId)}&interval=${interval}&limit=10000`);
          if (!active) return;
          setData(history);
          setError("");
          waitingForHistory = false;
        }
        if (result.closed_candles.length) {
          if (showArchive) setArchive((previous) => {
            if (!previous?.candles) return previous;
            const byOpen = new Map<number, Candle>(previous.candles.map((candle) => [candle.open_time, candle]));
            result.closed_candles.forEach((candle) => byOpen.set(candle.open_time, candle));
            const candles = [...byOpen.values()].sort((left, right) => left.open_time - right.open_time).slice(-10000);
            return { ...previous, candles, available_count: Math.max(previous.available_count ?? 0, candles.length),
              last_close_ms: result.last_closed_at_ms ?? previous.last_close_ms,
              updated_at_ms: result.updated_at_ms ?? previous.updated_at_ms,
              segment_sha256: result.new_segments?.length
                ? `${previous.segment_sha256 ?? ""}${result.new_segments.map((segment) => segment.sha256).join("")}`
                : previous.segment_sha256 };
          });
          else setData((previous) => {
            if (!previous) return previous;
            const byOpen = new Map<number, Candle>(previous.candles.map((candle) => [candle.open_time, candle]));
            result.closed_candles.forEach((candle) => byOpen.set(candle.open_time, candle));
            const candles = [...byOpen.values()].sort((left, right) => left.open_time - right.open_time).slice(-10000);
            return { ...previous, candles, available_count: candles.length,
              as_of_ms: result.last_closed_at_ms ?? previous.as_of_ms,
              dataset_sha256: result.dataset_sha256 ?? previous.dataset_sha256 };
          });
          const relatedIntervals = [...new Set([interval, "15m", "1h", "4h", "1d"])].slice(0, 4).join(",");
          void api<StructureResult>(`/market/structure?asset_id=${encodeURIComponent(assetId)}&intervals=${encodeURIComponent(relatedIntervals)}&limit=10000`)
            .then((value) => { if (active) setStructure(value); })
            .catch(() => { /* Keep the last confirmed analysis visible during an outage. */ });
        }
      } catch (e) {
        retryDelay = Math.min(retryDelay * 2, 60000);
        if (active) {
          setLive((previous) => previous
            ? { ...previous, stale: true, poll_error: (e as Error).message, refresh_error: (e as Error).message }
            : { asset_id: assetId, interval, closed_candles: [], live_candle: null, research_only: true, stale: true, refresh_error: (e as Error).message });
        }
      } finally {
        if (active) timer = window.setTimeout(() => void refreshSelectedCycle(), retryDelay);
      }
    }
    void refreshSelectedCycle();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [canRefresh, assetId, interval, showArchive]);
  /** 创建最近90天公开行情任务；不把入队成功当作采集成功。 */
  async function collect() {
    try {
      if (isUnderlyingCandle || isObservationSeries) {
        const providerInterval = isObservationSeries ? "1d" : interval;
        const result = await post<{ id: string }>("/tasks", {
          kind: "FETCH_UNDERLYING_HISTORY", asset_id: assetId,
          interval: providerInterval, days: sourceBinding?.history_days?.[providerInterval] ?? 1095,
        });
        onTask(`已创建${asset?.symbol}源头行情任务 ${result.id.slice(0, 8)}；结果和数据覆盖会显示在后台任务`);
        return;
      }
      const result = await post<{ id: string }>("/tasks", {
        kind: "FETCH_HISTORY",
        asset_id: assetId,
        interval,
        days: 90,
      });
      onTask(
        "行情采集任务已创建：" +
          result.id.slice(0, 8) +
          "，请在后台任务查看结果",
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  /** 为所选已核验永续合约创建三年公开历史归档任务，数据由后台分页写入本地磁盘。 */
  async function collectArchive() {
    try {
      const result = await post<{ id: string }>("/tasks", {
        kind: "FETCH_ARCHIVE",
        asset_id: assetId,
        interval,
      });
      onTask(
        "三年历史归档任务已创建：" +
          result.id.slice(0, 8) +
          "；可在后台任务查看进度和实际覆盖范围",
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  /** 为注册表中所有精确核验的 Binance USDT 永续创建可续传的三年 15 分钟归档任务。 */
  async function collectAllBinanceArchives() {
    try {
      const result = await post<{ id: string; asset_count: number; symbols: string[]; deduplicated?: boolean }>(
        "/tasks/binance-archives",
        {},
      );
      onTask(
        `${result.deduplicated ? "已有" : "已创建"}批量归档任务 ${result.id.slice(0, 8)}：${result.asset_count} 个已核验 Binance 合约，基础周期 15m；高周期按真实K线聚合`,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  const chartCandles = showArchive ? archive?.candles ?? [] : data?.candles ?? [];
  const displayCandles: Candle[] = live?.live_candle
    ? [...chartCandles.filter((candle) => candle.open_time !== live.live_candle?.open_time), live.live_candle]
    : chartCandles;
  const currentStructure = structure?.timeframes[interval];
  const currentEvents = structure?.events.filter((event) => event.timeframe === interval) ?? [];
  const parsedMAPeriods = maPeriodsText.split(",").map((value) => Number(value.trim()));
  const validMAPeriods = parsedMAPeriods.length >= 1 && parsedMAPeriods.length <= 8
    && parsedMAPeriods.every((value) => Number.isInteger(value) && value >= 2 && value <= 500)
    && new Set(parsedMAPeriods).size === parsedMAPeriods.length;
  const maPeriods = validMAPeriods ? parsedMAPeriods : indicators.ma_periods;
  const indicatorDraft = { ...indicators, ma_periods: validMAPeriods ? parsedMAPeriods : indicators.ma_periods };
  const hasUnsavedIndicators = JSON.stringify(indicatorDraft) !== JSON.stringify(savedIndicators);
  /** Update one display-only indicator toggle while preserving the other saved preferences. */
  function updateIndicator(key: Exclude<keyof ChartIndicators, "ma_periods">, checked: boolean) {
    setIndicators((previous) => ({ ...previous, [key]: checked }));
  }
  /** Validate and persist chart overlays in PostgreSQL; indicator choices do not affect backtests. */
  async function saveIndicatorSettings() {
    if (!validMAPeriods) return;
    try {
      const saved = await api<ChartIndicators>("/settings/chart-indicators", {
        method: "PUT",
        body: JSON.stringify(indicatorDraft),
      });
      setIndicators(saved);
      setSavedIndicators(saved);
      setMAPeriodsText(saved.ma_periods.join(","));
      setError("");
      onTask("图表指标设置已保存");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">MARKET WORKSPACE</span>
          <h1>多周期行情</h1>
          <p>{isObservationSeries ? "供应商发布的日度能源现货参考值 · 保留真实观测频率" : isUnderlyingCandle ? "原始标的参考行情 · 来源、产品身份与更新频率独立显示" : "Binance公开永续行情 · 本地真实K线 · 只显示已收盘结构信号"}</p>
        </div>
        <button onClick={() => setReload((n) => n + 1)}>重新读取缓存</button>
      </header>
      {relatedReferences.length > 0 && <section className="panel" aria-label="相关能源参考">
        <strong>相关原始标的参考</strong>
        <p>合约历史保持原来源。以下为独立参考产品，不能替代当前合约的成交价。</p>
        {relatedReferences.map((item) => <button key={item.id} onClick={() => { setAssetId(item.id); setShowArchive(false); setInterval(item.venue === "EIA" ? "1d" : "15m"); }}>{item.name}</button>)}
      </section>}
      {isEnergyReference && <p className="panel">后台自动更新，无需保持本页面打开。15分钟历史最多请求59天；日线最多三年，以实际覆盖为准。连续代码包含换月，未核验换月规则；仅供研究。</p>}
      <section className="panel chart-panel">
        <div className="chart-toolbar">
          <select
            aria-label="选择标的"
            value={assetId}
            onChange={(e) => setAssetId(e.target.value)}
          >
            {assets.map((a) => (
              <option key={a.id} value={a.id}>
                {a.symbol} · {a.name}
              </option>
            ))}
          </select>
          <button className={indicatorPanelOpen ? "selected" : ""} aria-expanded={indicatorPanelOpen} onClick={() => setIndicatorPanelOpen((open) => !open)}>
            指标设置
          </button>
          <details className="chart-actions">
            <summary>数据管理</summary>
            <div className="chart-actions-menu">
              <button onClick={collect}>{isUnderlyingCandle || isObservationSeries ? isEnergyReference ? "补齐来源可用历史" : "采集底层三年数据" : "补齐最近90天"}</button>
              {!isUnderlyingCandle && !isObservationSeries && <button disabled={asset?.venue !== "BINANCE_USDM" || !["VERIFIED", "VERIFIED_SNAPSHOT"].includes(asset?.verification ?? "")} onClick={() => void collectArchive()}>补齐所选合约三年</button>}
              {verifiedBinanceCount > 0 && <button onClick={() => void collectAllBinanceArchives()}>补齐全部 Binance 合约三年（{verifiedBinanceCount}）</button>}
            </div>
          </details>
          {!isUnderlyingCandle && !isObservationSeries && <button
            aria-pressed={showArchive}
            onClick={() => setShowArchive((value) => !value)}
          >
            {showArchive ? "查看90天缓存" : "查看三年缓存"}
          </button>}
        </div>
        {indicatorPanelOpen && !isObservationSeries && (
          <section className="indicator-panel" aria-label="图表指标设置">
            <div className="indicator-panel-heading">
              <div><strong>图表指标</strong><small>只控制显示；计算模型不受影响</small></div>
              {hasUnsavedIndicators && <span>有未保存的修改</span>}
            </div>
            <div className="indicator-groups">
              <div>
                <strong>主图</strong>
                <label className="toggle"><input type="checkbox" checked={indicators.show_ma} onChange={(event) => updateIndicator("show_ma", event.target.checked)} />均线</label>
                <label className="toggle">周期<input aria-label="均线周期" className="indicator-periods" value={maPeriodsText} onChange={(event) => setMAPeriodsText(event.target.value)} placeholder="5,10,20" /></label>
                <label className="toggle"><input type="checkbox" checked={indicators.show_bollinger} onChange={(event) => updateIndicator("show_bollinger", event.target.checked)} />布林带</label>
                <label className="toggle"><input type="checkbox" checked={indicators.show_trendlines} onChange={(event) => updateIndicator("show_trendlines", event.target.checked)} />趋势线与破位</label>
              </div>
              <div>
                <strong>副图与结构</strong>
                <label className="toggle"><input type="checkbox" checked={indicators.show_macd} onChange={(event) => updateIndicator("show_macd", event.target.checked)} />MACD</label>
                <label className="toggle"><input type="checkbox" checked={indicators.show_td} onChange={(event) => updateIndicator("show_td", event.target.checked)} />九转序列</label>
                <label className="toggle"><input type="checkbox" checked={indicators.show_dow123} onChange={(event) => updateIndicator("show_dow123", event.target.checked)} />道氏 1-2-3</label>
                <label className="toggle"><input type="checkbox" checked={indicators.show_divergence} onChange={(event) => updateIndicator("show_divergence", event.target.checked)} />价格 / MACD 背离</label>
                <label className="toggle"><input type="checkbox" checked={indicators.show_wave} onChange={(event) => updateIndicator("show_wave", event.target.checked)} />波浪候选</label>
              </div>
            </div>
            {!validMAPeriods && <small className="error">均线周期填写1到8个互不重复的整数，范围为2–500。</small>}
            <div className="indicator-actions">
              <button onClick={() => { setIndicators({ ...DEFAULT_CHART_INDICATORS, ma_periods: [...DEFAULT_CHART_INDICATORS.ma_periods] }); setMAPeriodsText(DEFAULT_CHART_INDICATORS.ma_periods.join(",")); }}>恢复简洁默认</button>
              <button className="primary" disabled={!validMAPeriods || !hasUnsavedIndicators} onClick={() => void saveIndicatorSettings()}>保存指标设置</button>
            </div>
          </section>
        )}
        <div className="intervals">
          {(isObservationSeries ? ["1d"] : isUnderlyingCandle ? sourceBinding?.intervals ?? [] :
            (intervals.length ? intervals.map((i) => i.interval) : ["15m", "30m", "1h", "2h", "4h", "1d"])
          ).map((value) => {
            const capability = intervals.find((i) => i.interval === value);
            return (
              <button
                key={value}
                title={capability?.reason ?? "已支持"}
                className={
                  (interval === value ? "selected " : "") +
                  (!capability?.available ? "unavailable" : "")
                }
                onClick={() => setInterval(value)}
              >
                {value}
                {capability && !capability.available ? " · 待采集" : ""}
              </button>
            );
          })}
        </div>
        <div className="chart-meta">
          <strong>{asset?.symbol}</strong>
          <span>{asset?.name}</span>
          <span className="badge neutral">仅研究</span>
        </div>
        {!isObservationSeries && live?.health ? <EnergyHealthPanel live={live} timezone={timezone} /> : !isObservationSeries && <div className="chart-live-status">
          <span className={`badge ${live?.stale ? "negative" : live ? "positive" : "neutral"}`}>
            {!canRefresh ? "等待本地K线" : live?.stale ? "数据偏旧 / 休市 / 更新异常" : live ? isUnderlyingCandle ? "底层源已连接" : "Binance行情已连接" : "正在连接行情"}
          </span>
          <span>{live?.updated_at_ms ? `${live.source ?? "Binance"} · 页面5秒刷新；${live.source_freshness ?? "本地公开行情刷新"} · 最近拉取 ${date(live.updated_at_ms, timezone)}` : "当前周期每5秒检查本地更新；数据源按其授权/频率限制请求"}</span>
          {live?.source_event_at_ms && <span>源行情时间 {date(live.source_event_at_ms, timezone)}</span>}
          {live?.request_queue_wait_seconds ? <span>{`数据源配额队列等待 ${Math.ceil(live.request_queue_wait_seconds)} 秒`}</span> : null}
          {live?.refresh_error && <span className="error">{live.refresh_error}</span>}
        </div>}
        {loading ? (
          <div className="empty">正在校验并加载历史行情…</div>
        ) : error ? (
          <div className="empty error">
            {error}
            <small>可选择已有数据的周期，或创建公开行情采集任务。</small>
          </div>
        ) : isObservationSeries && series?.observations.length ? (
          <ReferenceSeriesChart observations={series.observations} unit={series.unit} />
        ) : chartCandles.length ? (
          <PriceChart
            candles={displayCandles}
            timezone={timezone}
            showMA={indicators.show_ma}
            structure={currentStructure}
            events={currentEvents}
            maPeriods={maPeriods}
            showBollinger={indicators.show_bollinger}
            showMACD={indicators.show_macd}
            showTD={indicators.show_td}
            showTrendlines={indicators.show_trendlines}
            showDow123={indicators.show_dow123}
            showDivergence={indicators.show_divergence}
            showWave={indicators.show_wave}
          />
        ) : isObservationSeries ? (
          <div className="empty">本地还没有该能源现货参考序列；请点击“数据管理 → 采集底层三年数据”排入后台任务。此源只发布日度价格点，不生成15分钟K线。</div>
        ) : showArchive && archive?.available === false ? (
          <div className="empty">本地尚无该标的/周期的长期缓存；点击“采集三年历史”创建后台任务。</div>
        ) : (
          <div className="empty">没有完整周期 K 线；当前不做预测或插值。</div>
        )}
        <div className="chart-footer" data-loaded-interval={data?.interval} data-loaded-asset={showArchive ? archive?.asset_id : data?.asset_id}>
          {isObservationSeries ? <><span>{series?.observations.length ?? 0} 个真实日度观测 · {series?.unit ?? "—"}</span><span>数据截止 {date(series?.last_observed_at_ms, timezone)} · {String(series?.source?.provider ?? "等待供应商")}</span></> : <><span>{displayCandles.length} 根K线 · 数据截止 {date(showArchive ? archive?.last_close_ms : data?.as_of_ms, timezone)}</span><span>刷新间隔 5 秒 · 结构判断仅使用收盘K线</span></>}
        </div>
      </section>
      <details className="chart-details">
        <summary>数据与多周期分析详情</summary>
        <div className="two-col">
          <section className="panel">
            <h3>数据可追溯</h3>
            <p>来源：{isObservationSeries ? String(series?.source?.provider ?? "等待采集") : showArchive ? archive?.source ?? "暂无归档" : String(data?.source.provider ?? "暂无数据")}</p>
            <p className="mono break">SHA256：{isObservationSeries ? series?.sha256 ?? "—" : showArchive ? archive?.segment_sha256 ?? "—" : data?.dataset_sha256 ?? "—"}</p>
            <p>长期归档状态：{archive?.status ?? "未载入"} · 基础周期：{archive?.source_interval ?? "15m"} · 当前周期原生数据：{data?.source_interval ?? "—"}</p>
          </section>
          <section className="panel">
            <h3>分析能力</h3>
            {isObservationSeries ? (
              <p>该序列为{series?.unit ?? "供应商单位"}的日度标量观测，用折线展示；不会补造OHLC，不提供盘中信号。来源的发布延迟取决于EIA数据更新。</p>
            ) : currentStructure ? (
              <>
                <p>分析截止：{date(currentStructure.last_closed_at, timezone)} · 已收盘K线 · {structure?.algorithm ?? "等待缓存数据"}</p>
                <p>当前周期：{interval} · 结构趋势 {currentStructure.trend === 1 ? "上升" : currentStructure.trend === -1 ? "下降" : currentStructure.status === "OK" ? "震荡/分歧" : "数据不足"} · 趋势线 {currentStructure.trendline_break_state ?? "待缓存"}</p>
                <p>可用周期：{Object.entries(structure?.timeframes ?? {}).map(([tf, value]) => `${tf} ${value.status === "OK" ? value.trendline_break_state === "BROKEN" ? "破位" : "可分析" : "缺数据"}`).join(" · ") || "请先采集对应周期"}</p>
                <p>近期确认：{currentEvents.length ? currentEvents.slice(-4).map((event) => `${event.kind} ${date(event.confirmed_at, timezone)}`).join("；") : "暂无破位、123或RSI背离"}</p>
                <p>波浪候选：{currentStructure.wave?.status ?? "样本不足"} · 仅作结构参考</p>
              </>
            ) : (
              <p>均线按UTC日收盘计算；MACD、布林带和九转按当前周期计算。结构与预测只读取本地已收盘数据，不代表获利概率。</p>
            )}
          </section>
        </div>
      </details>
    </>
  );
}
