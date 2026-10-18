import { useEffect, useState } from "react";
import type { ArchiveResult, Asset, CandleResult, Interval, StructureResult } from "../types";
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
  const [showArchive, setShowArchive] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [ma, setMA] = useState(true);
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
      setStructure(null);
      api<CandleResult>(`/market/candles${query}`)
        .then((value) => {
        if (!active) return;
        setData(value);
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
  const currentStructure = structure?.timeframes[interval];
  const currentEvents = structure?.events.filter((event) => event.timeframe === interval) ?? [];
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">MARKET WORKSPACE</span>
          <h1>多周期行情</h1>
          <p>已收盘真实 K 线 · UTC 聚合边界 · {timezone} 显示</p>
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
            MA20 / MA60
          </label>
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
          <PriceChart candles={chartCandles} timezone={timezone} showMA={ma} structure={currentStructure} events={currentEvents} />
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
              ? `长期缓存截止：${date(archive?.last_close_ms, timezone)} · 覆盖状态 ${archive?.status ?? "未缓存"}`
              : `数据截止：${date(data?.as_of_ms, timezone)}`} · 当前显示{" "}
            {chartCandles.length} 根 / 可用{" "}
            {showArchive ? archive?.available_count ?? 0 : data?.available_count ?? 0} 根
          </span>
          <span>
            {showArchive
              ? `本地分段SHA256 ${archive?.segment_sha256?.slice(0, 16) ?? "—"}`
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
          {showArchive ? (
            <>
              <p>分析截止：{date(structure?.as_of_ms, timezone)} · 只读已收盘K线 · {structure?.algorithm ?? "等待归档数据"}</p>
              <p>当前周期：{interval} · 结构趋势 {currentStructure?.trend === 1 ? "上升" : currentStructure?.trend === -1 ? "下降" : currentStructure?.status === "OK" ? "震荡/分歧" : "数据不足"} · 趋势线状态 {currentStructure?.trendline_break_state ?? "待缓存"}</p>
              <p>各周期缓存状态：{Object.entries(structure?.timeframes ?? {}).map(([tf, value]) => `${tf} ${value.status === "OK" ? value.trendline_break_state === "BROKEN" ? "趋势线破坏" : "可分析" : "数据不足"}`).join(" · ") || "请先采集对应周期"}</p>
              <p>已确认事件：{currentEvents.length ? currentEvents.slice(-4).map((event) => `${event.kind} ${date(event.confirmed_at, timezone)}`).join("；") : "本周期暂无破位、123或RSI背离确认"}</p>
              <p>波浪计数：{currentStructure?.wave?.status ?? "样本不足/待缓存"} · 只作为结构候选，不表示获利概率。</p>
            </>
          ) : (
            <p>90天缓存用于普通蜡烛图；切换到“三年缓存”后会加载已落盘周期的趋势线、道氏123、波浪候选、RSI背离和跨周期结构破坏时间。</p>
          )}
        </section>
      </div>
    </>
  );
}
