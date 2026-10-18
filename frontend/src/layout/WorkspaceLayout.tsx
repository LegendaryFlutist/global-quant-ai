import type { ReactNode } from "react";
import type { User, PageId } from "../types";
const nav: [PageId, string, string][] = [
  ["dashboard", "◈", "总览"],
  ["assets", "▤", "全球资产"],
  ["charts", "⌁", "多周期 K 线"],
  ["jobs", "◷", "后台任务"],
  ["roadmap", "▦", "模型与平台能力"],
  ["settings", "⚙", "系统设置"],
];

/** 提供导航、身份边界和统一反馈区；不发起模型、行情或交易请求。 */
export default function WorkspaceLayout({
  user,
  page,
  onNavigate,
  onLogout,
  error,
  notice,
  children,
}: {
  user: User;
  page: PageId;
  onNavigate: (page: PageId) => void;
  onLogout: () => void;
  error: string;
  notice: string;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            onNavigate("dashboard");
          }}
        >
          <span className="brand-mark small">GQ</span>
          <div>
            GLOBAL QUANT<span>AI RESEARCH PLATFORM</span>
          </div>
        </a>
        <div className="nav-label">研究工作台</div>
        <nav>
          {nav.map(([id, icon, title]) => (
            <button
              key={id}
              className={page === id ? "active" : ""}
              onClick={() => {
                onNavigate(id);
              }}
            >
              <span>{icon}</span>
              {title}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="status-dot" />
          内部研究环境<p>Phase 1 · V2.0</p>
          <small>
            账户同步、模拟账本和实盘审批
            <br />
            将在后续阶段开发。
          </small>
        </div>
      </aside>
      <div className="workspace">
        <div className="topbar">
          <span>
            个人量化研究 / <b>{nav.find((n) => n[0] === page)?.[2]}</b>
          </span>
          <div>
            <span className="mode-tag">RESEARCH ONLY</span>
            <span>{user.username}</span>
            <button onClick={onLogout}>退出</button>
          </div>
        </div>
        <main className="content">
          {error && (
            <div role="alert" className="alert error">
              {error}
            </div>
          )}
          {notice && (
            <div role="status" className="alert">
              {notice}
            </div>
          )}
          {children}
          <footer className="workspace-footer">
            <span>GLOBAL QUANT AI / PERSONAL RESEARCH</span>
            <span>Phase 1 · 不生成收益承诺 · 实盘执行未实现</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
