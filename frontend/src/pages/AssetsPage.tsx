import { useState } from "react";
import type { Asset } from "../types";
import { date } from "../services/api";
import { badge } from "../components/StatusBadge";
import InstrumentPanel from "../components/InstrumentPanel";

/** 以底层身份、主题与实际核验状态检索资产；选择标的只打开研究图表。 */
export default function AssetsPage({
  assets,
  timezone,
  onChart,
  onVerify,
}: {
  assets: Asset[];
  timezone: string;
  onChart: (asset: Asset) => void;
  onVerify: () => Promise<void>;
}) {
  const [category, setCategory] = useState("all");
  const [product, setProduct] = useState("all");
  const [theme, setTheme] = useState("全部");
  const [search, setSearch] = useState("");
  const [specAssetId, setSpecAssetId] = useState<string | null>(null);
  const categoryLabels: Record<string, string> = {
    crypto: "虚拟货币", stocks: "股票", etf: "ETF", indices: "指数",
    commodities: "商品", forex: "外汇", macro: "宏观参考", other: "其他",
  };
  const productLabels: Record<string, string> = {
    spot: "现货", perpetual: "永续合约", future: "期货", reference: "指数 / 研究指标", other: "其他",
  };
  const providerLabels: Record<string, string> = {
    massive_stocks: "Massive 美股 / ETF",
    twelve_data: "Twelve Data 韩国市场",
    eia: "EIA 能源现货参考",
    sina_cn_public: "新浪中国指数（公开）",
    tencent_cn_public: "腾讯中国指数（备用）",
  };
  const providerStatusLabels: Record<string, string> = {
    CONNECTED: "已验证连接", CONFIGURED_NOT_VALIDATED: "已配置待验证",
    NEEDS_CONFIGURATION: "需本机配置", UNAVAILABLE: "上游暂不可用",
    PUBLIC_NO_KEY: "免密钥公开源，待验证",
  };
  const themes = [
    "全部",
    ...Array.from(new Set(assets.flatMap((a) => a.themes))).sort(),
  ];
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">ASSET REGISTRY</span>
          <h1>全球资产注册表</h1>
          <p>按资产类别与产品类型筛选；股票、ETF和相关永续保持独立登记，未接入行情不会显示为可交易</p>
        </div>
        <button onClick={() => void onVerify()}>核验 Binance 合约</button>
      </header>
      <div className="filters">
        <input
          placeholder="搜索代码、名称或底层资产"
          aria-label="搜索资产"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select value={category} onChange={(event) => setCategory(event.target.value)} aria-label="资产类别">
          <option value="all">全部类别</option>
          <option value="crypto">虚拟货币</option>
          <option value="stocks">股票</option>
          <option value="etf">ETF</option>
          <option value="indices">指数</option>
          <option value="commodities">商品</option>
          <option value="forex">外汇</option>
          <option value="macro">宏观参考</option>
          <option value="other">其他</option>
        </select>
        <select value={product} onChange={(event) => setProduct(event.target.value)} aria-label="产品类型">
          <option value="all">全部产品</option>
          <option value="spot">现货</option>
          <option value="perpetual">永续合约</option>
          <option value="future">期货</option>
          <option value="reference">指数 / 研究指标</option>
          <option value="other">其他</option>
        </select>
        <select
          value={theme}
          onChange={(e) => setTheme(e.target.value)}
          aria-label="资产主题"
        >
          {themes.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </div>
      {specAssetId && <InstrumentPanel key={specAssetId} assetId={specAssetId} timezone={timezone} onClose={() => setSpecAssetId(null)} />}
      <section className="panel table-panel">
        <table>
          <thead>
            <tr>
              <th>标的 / 底层资产</th>
              <th>市场与类型</th>
              <th>主题</th>
              <th>核验与数据</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {assets
              .filter(
                (a) =>
                  (category === "all" || a.market_category === category) &&
                  (product === "all" || a.product_type === product) &&
                  (theme === "全部" || a.themes.includes(theme)) &&
                  `${a.symbol} ${a.name} ${a.underlying_id}`
                    .toLowerCase()
                    .includes(search.toLowerCase()),
              )
              .map((a) => (
                <tr key={a.id}>
                  <td>
                    <strong>{a.symbol}</strong>
                    <small>{a.name}</small>
                    <small className="mono">{a.underlying_id}</small>
                  </td>
                  <td>
                    <span className="badge neutral">{categoryLabels[a.market_category] ?? "其他"}</span>
                    <small>{productLabels[a.product_type] ?? "其他"} · {a.venue}</small>
                  </td>
                  <td>
                    <div className="tags">
                      {a.themes.map((t) => (
                        <span key={t}>{t}</span>
                      ))}
                    </div>
                  </td>
                  <td>
                    {badge(a.verification)}
                    <small>
                      {a.datasets.length
                        ? `${a.datasets[0].rows} 根历史 K 线`
                        : a.series?.length
                          ? `${a.series[0].rows} 个真实参考值`
                          : "尚无本地行情"}
                    </small>
                    {a.market_source && <small>
                      底层源：{providerLabels[a.market_source.provider_id] ?? a.market_source.provider_id} · {providerStatusLabels[a.market_source.provider_status] ?? a.market_source.provider_status}
                    </small>}
                    {a.market_source?.fallback_provider_ids?.length ? <small>
                      备用：{a.market_source.fallback_provider_ids.map((id) => providerLabels[id] ?? id).join(" / ")}
                    </small> : null}
                    {a.market_source && <small>
                      {a.market_source.credential_required
                        ? a.market_source.credential_configured ? "本机密钥已配置（值不展示）" : "需在本机私有配置中设置数据源密钥"
                        : "无需配置密钥；公开源可能延迟或受访问限制"}
                    </small>}
                    {a.market_source?.freshness && <small>{a.market_source.freshness}</small>}
                    {a.market_source?.request_pacing && <small>
                      {`同一账户的股票类 Key 共用 ${a.market_source.request_pacing.max_requests_per_minute} 次/分钟额度；本系统每 ${a.market_source.request_pacing.safe_spacing_seconds} 秒排队一次，429 后冷却 ${a.market_source.request_pacing.cooldown_on_429_seconds} 秒。`}
                    </small>}
                    <small>{date(a.verified_at, timezone)}</small>
                  </td>
                  <td>
                    <button onClick={() => onChart(a)}>查看图表</button>
                    <button onClick={() => setSpecAssetId(a.id)}>规格历史</button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
