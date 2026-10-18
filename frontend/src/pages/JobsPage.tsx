import type { Job } from "../types";
import { date } from "../services/api";
import { badge } from "../components/StatusBadge";

/** 展示持久任务的真实终态与有限重试；重新入队由上层统一错误处理。 */
export default function JobsPage({
  jobs,
  timezone,
  onRefresh,
  onRetry,
}: {
  jobs: Job[];
  timezone: string;
  onRefresh: () => Promise<void>;
  onRetry: (job: Job) => Promise<void>;
}) {
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">BACKGROUND WORKERS</span>
          <h1>后台任务</h1>
          <p>独立工作进程 · 持久记录 · 自动重试 · 10 秒刷新</p>
        </div>
        <button onClick={() => void onRefresh()}>立即刷新</button>
      </header>
      <section className="panel table-panel">
        <table>
          <thead>
            <tr>
              <th>任务</th>
              <th>标的 / 周期</th>
              <th>状态</th>
              <th>尝试</th>
              <th>结果</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id}>
                <td>
                  {j.kind}
                  <small className="mono">{j.id.slice(0, 8)}</small>
                </td>
                <td>
                  {j.parameters.asset_id ?? "资产核验"}
                  <small>{j.parameters.interval ?? ""}</small>
                </td>
                <td>{badge(j.status)}</td>
                <td>{j.attempts} / 3</td>
                <td className="job-result">
                  {j.result.error
                    ? String(j.result.error)
                    : j.result.rows
                      ? `${j.result.rows} 根真实 K 线${j.result.status ? ` · ${String(j.result.status)}` : ""}`
                      : j.kind === "FETCH_ARCHIVE"
                        ? "三年历史归档 · 仅追加本地分段"
                        : "—"}
                </td>
                <td>{date(j.created_at, timezone)}</td>
                <td>
                  {["FAILED", "QUEUE_ERROR"].includes(j.status) && (
                    <button onClick={() => void onRetry(j)}>重试</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
