import { useEffect, useState } from "react";
import type { ArchiveResult, Asset, Candle, CandleResult, Interval, LiveMarketResult, StructureResult } from "../types";
import { api, date, post } from "../services/api";
import PriceChart from "../components/PriceChart";
/** 按标的/周期读取已校验历史；切换后忽略旧请求，采集作为独立后台任务。 */
export default function ChartPage({
  assets,
  initialAsset,
  initialInterval,
  timezone,
  onTask,
}: {
  assets: Asset[];
  initialAsset: string;
  initialInterval: string;
  timezone: string;
  onTask: (message: string) => void;
}) {
  const [assetId, setAssetId] = useState(initialAsset);
  const [interval, setInterval] = useState(initialInterval);
  const [intervals, setIntervals] = useState<Interval[]>([]);
  const [data, setData] = useState<CandleResult | null>(null);
  const [archive, setArchive] = useState<ArchiveResult | null>(null);
  const [structure, setStructure] = useState<StructureResult | null>(null);
  const [live, setLive] = useState<LiveMarketResult | null>(null);
  const [showArchive, setShowArchive] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [ma, setMA] = useState(true);
  const [maPeriodsText, setMAPeriodsText] = useState("5,10,20");
  const [showBollinger, setShowBollinger] = useState(true);
  const [showMACD, setShowMACD] = useState(true);
  const [showTD, setShowTD] = useState(true);
  const [reload, setReload] = useState(0);
  const asset = assets.find((a) => a.id === assetId);
  useEffect(() => {
    let active = true;
    api<Interval[]>("/market/intervals?asset_id=" + encodeURIComponent(assetId))
      .then((v) => {
        if (active) setIntervals(v);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, [assetId, reload]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setData(null);
    setArchive(null);
    setStructure(null);
    setLive(null);
    const query = `?asset_id=${encodeURIComponent(assetId)}&interval=${interval}&limit=10000`;
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
  }, [assetId, interval, reload, showArchive]);
  const canRefresh = showArchive ? archive?.available === true : Boolean(data?.candles.length);
  useEffect(() => {
    if (!canRefresh) return;
    let active = true;
    let timer = 0;
    let retryDelay = 5000;
    /** Refresh the selected verified cycle, waiting five seconds after each request settles. */
    async function refreshSelectedCycle() {
      try {
        const result = await post<LiveMarketResult>("/market/live", { asset_id: assetId, interval });
        if (!active) return;
        retryDelay = 5000;
        setLive(result);
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
            ? { ...previous, stale: true, refresh_error: (e as Error).message }
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
  const chartCandles = showArchive ? archive?.candles ?? [] : data?.candles ?? [];
  const displayCandles: Candle[] = live?.live_candle
    ? [...chartCandles.filter((candle) => candle.open_time !== live.live_candle?.open_time), live.live_candle]
    : chartCandles;
  const currentStructure = structure?.timeframes[interval];
  const currentEvents = structure?.events.filter((event) => event.timeframe === interval) ?? [];
  const maPeriods = [...new Set(maPeriodsText.split(",").map((value) => Number(value.trim())).filter((value) => Number.isInteger(value) && value > 0 && value <= 500))].slice(0, 8);
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">MARKET WORKSPACE</span>
          <h1>多周期行情</h1>
          <p>已收盘真实 K 线 · UTC 聚合边界 · {timezone} 显示；每5秒尝试刷新当前周期，结构评分只使用闭合K线</p>
        </div>
        <button onClick={() => setReload((n) => n + 1)}>重新读取缓存</button>
      </header>
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
          <label className="toggle">
            <input
              type="checkbox"
              checked={ma}
              onChange={(e) => setMA(e.target.checked)}
            />
            均线
          </label>
          <label className="toggle">均线(日)
            <input aria-label="均线周期(日)" style={{ width: "110px", padding: "6px 8px" }} value={maPeriodsText} onChange={(event) => setMAPeriodsText(event.target.value)} placeholder="5,10,20" />
          </label>
          <label className="toggle"><input type="checkbox" checked={showBollinger} onChange={(event) => setShowBollinger(event.target.checked)} />布林线</label>
          <label className="toggle"><input type="checkbox" checked={showMACD} onChange={(event) => setShowMACD(event.target.checked)} />MACD</label>
          <label className="toggle"><input type="checkbox" checked={showTD} onChange={(event) => setShowTD(event.target.checked)} />九转</label>
          <button onClick={collect}>采集最近90天</button>
          <button onClick={() => void collectArchive()}>采集三年历史</button>
          <button
            aria-pressed={showArchive}
            onClick={() => setShowArchive((value) => !value)}
          >
            {showArchive ? "查看90天缓存" : "查看三年缓存"}
          </button>
        </div>
        <div className="intervals">
          {(intervals.length
            ? intervals.map((i) => i.interval)
            : ["15m", "30m", "1h", "2h", "4h", "1d"]
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
        {loading ? (
          <div className="empty">正在校验并加载历史行情…</div>
        ) : error ? (
          <div className="empty error">
            {error}
            <small>可选择已有数据的周期，或创建公开行情采集任务。</small>
          </div>
        ) : chartCandles.length ? (
          <PriceChart candles={displayCandles} timezone={timezone} showMA={ma} structure={currentStructure} events={currentEvents} maPeriods={maPeriods} showBollinger={showBollinger} showMACD={showMACD} showTD={showTD} />
        ) : showArchive && archive?.available === false ? (
          <div className="empty">本地尚无该标的/周期的长期缓存；点击“采集三年历史”创建后台任务。</div>
        ) : (
          <div className="empty">没有完整周期 K 线；当前不做预测或插值。</div>
        )}
        <div
          className="chart-footer"
          data-loaded-interval={data?.interval}
          data-loaded-asset={showArchive ? archive?.asset_id : data?.asset_id}
        >
          <span>
            {showArchive
              ? `长期缓存截止：${date(archive?.last_close_ms, timezone)} · 覆盖状态 ${archive?.status ?? "未缓存"} · ${live?.stale ? "实时更新中断，显示旧缓存" : live ? `更新时间 ${date(live.updated_at_ms, timezone)}` : "等待实时更新"}`
              : `数据截止：${date(data?.as_of_ms, timezone)} · ${live?.stale ? "实时连接中断，显示旧缓存" : live ? `刷新于 ${date(live.updated_at_ms, timezone)}` : canRefresh ? "正在刷新" : "等待本地行情"}`} · 当前显示{" "}
            {displayCandles.length} 根 / 可用{" "}
            {showArchive ? archive?.available_count ?? 0 : data?.available_count ?? 0} 根
          </span>
          <span>
            {showArchive
              ? `${live?.live_candle && !live.stale ? `实时K线快照SHA256 ${live.live_snapshot_sha256?.slice(0, 16) ?? "—"}；结构分析只使用已收盘K线 · ` : ""}本地分段SHA256 ${archive?.segment_sha256?.slice(0, 16) ?? "—"}`
              : `原始周期 ${data?.source_interval ?? "—"} · 排除不完整周期 ${data?.excluded_incomplete_buckets ?? 0} 个`}
          </span>
        </div>
      </section>
      <div className="two-col">
        <section className="panel">
          <h3>数据可追溯</h3>
          <p>来源：{showArchive ? archive?.source ?? "暂无归档" : String(data?.source.provider ?? "暂无数据")}</p>
          <p className="mono break">SHA256：{showArchive ? archive?.segment_sha256 ?? "—" : data?.dataset_sha256 ?? "—"}</p>
          <p>
            历史缓存不是实时盘口。新行情只有公开接口实际返回成功后才会进入图表。
          </p>
        </section>
        <section className="panel">
          <h3>分析能力</h3>
          {currentStructure ? (
            <>
              <p>分析截止：{date(currentStructure.last_closed_at, timezone)} · 只读已收盘K线 · {structure?.algorithm ?? "等待缓存数据"}</p>
              <p>当前周期：{interval} · 结构趋势 {currentStructure?.trend === 1 ? "上升" : currentStructure?.trend === -1 ? "下降" : currentStructure?.status === "OK" ? "震荡/分歧" : "数据不足"} · 趋势线状态 {currentStructure?.trendline_break_state ?? "待缓存"}</p>
              <p>各周期缓存状态：{Object.entries(structure?.timeframes ?? {}).map(([tf, value]) => `${tf} ${value.status === "OK" ? value.trendline_break_state === "BROKEN" ? "趋势线破坏" : "可分析" : "数据不足"}`).join(" · ") || "请先采集对应周期"}</p>
              <p>已确认事件：{currentEvents.length ? currentEvents.slice(-4).map((event) => `${event.kind} ${date(event.confirmed_at, timezone)}`).join("；") : "本周期暂无破位、123或RSI背离确认"}</p>
              <p>波浪计数：{currentStructure?.wave?.status ?? "样本不足/待缓存"} · 只作为结构候选，不表示获利概率。</p>
            </>
          ) : (
            <p>均线按UTC日收盘值计算并投影到当前周期；MACD、布林带和九转按当前图表周期计算。缓存可用时显示因果趋势、道氏123、波浪候选与背离；未收盘线只显示临时技术指标，不参与结构评分。</p>
          )}
        </section>
      </div>
    </>
  );
}
