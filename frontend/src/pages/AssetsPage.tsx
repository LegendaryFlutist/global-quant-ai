import { useState } from "react";
import type { Asset } from "../types";
import { date } from "../services/api";
import { badge } from "../components/StatusBadge";

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
  const categoryLabels: Record<string, string> = {
    crypto: "虚拟货币", stocks: "股票", etf: "ETF", other: "其他",
  };
  const productLabels: Record<string, string> = {
    spot: "现货", perpetual: "永续合约", future: "期货", reference: "指数 / 研究指标", other: "其他",
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
                        : "未接入行情"}
                    </small>
                    <small>{date(a.verified_at, timezone)}</small>
                  </td>
                  <td>
                    <button onClick={() => onChart(a)}>查看图表</button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
