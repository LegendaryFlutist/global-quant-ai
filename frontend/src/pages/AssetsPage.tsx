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
  const [theme, setTheme] = useState("全部");
  const [search, setSearch] = useState("");
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
          <p>同一底层资产可归属多个主题；股票、ETF 与相关永续独立登记</p>
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
                    {a.venue}
                    <small>{a.asset_type}</small>
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
