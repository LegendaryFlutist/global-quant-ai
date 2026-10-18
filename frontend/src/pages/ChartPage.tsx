import { useEffect, useState } from "react";
import type { Asset, CandleResult, Interval } from "../types";
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
    api<CandleResult>(
      `/market/candles?asset_id=${encodeURIComponent(assetId)}&interval=${interval}&limit=10000`,
    )
      .then((v) => {
        if (active) setData(v);
      })
      .catch((e) => {
        if (active) setError(e.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [assetId, interval, reload]);
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
        ) : data?.candles.length ? (
          <PriceChart candles={data.candles} timezone={timezone} showMA={ma} />
        ) : (
          <div className="empty">没有完整周期 K 线；当前不做预测或插值。</div>
        )}
        <div
          className="chart-footer"
          data-loaded-interval={data?.interval}
          data-loaded-asset={data?.asset_id}
        >
          <span>
            数据截止：{date(data?.as_of_ms, timezone)} · 当前显示{" "}
            {data?.candles.length ?? 0} 根 / 可用 {data?.available_count ?? 0}{" "}
            根
          </span>
          <span>
            原始周期 {data?.source_interval ?? "—"} · 排除不完整周期{" "}
            {data?.excluded_incomplete_buckets ?? 0} 个
          </span>
        </div>
      </section>
      <div className="two-col">
        <section className="panel">
          <h3>数据可追溯</h3>
          <p>来源：{String(data?.source.provider ?? "暂无数据")}</p>
          <p className="mono break">SHA256：{data?.dataset_sha256 ?? "—"}</p>
          <p>
            历史缓存不是实时盘口。新行情只有公开接口实际返回成功后才会进入图表。
          </p>
        </section>
        <section className="panel">
          <h3>分析能力</h3>
          <p>
            本阶段提供蜡烛图、成交量、MA20 /
            MA60、缩放和平移。道氏、波浪候选、手动画线和历史回放将在 Phase 2
            接入；已有离线研究结果继续保留。
          </p>
        </section>
      </div>
    </>
  );
}
