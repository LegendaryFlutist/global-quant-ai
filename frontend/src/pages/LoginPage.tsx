import { useState, type FormEvent } from "react";
import type { User } from "../types";
import { api, post, setCSRF } from "../services/api";

/** 渲染本机登录界面；onLogin仅在服务器验证成功后收到当前身份。 */
export default function LoginPage({ onLogin }: { onLogin: (u: User) => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  /** 校验登录握手后提交身份；口令只在当前组件内存中使用，成功后立即清空。 */
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const status = await api<{ csrf_token: string }>("/auth/status");
      setCSRF(status.csrf_token);
      const user = await post<User>("/auth/login", { username, password });
      setCSRF(user.csrf_token);
      setPassword("");
      onLogin(user);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-shell">
      <div className="login-intro">
        <div className="brand-mark">GQ</div>
        <span className="eyebrow">PERSONAL RESEARCH TERMINAL</span>
        <h1>
          Global
          <br />
          Quant AI<span>.</span>
        </h1>
        <p>
          以真实数据研究全球市场。
          <br />
          从资产结构到多周期价格，建立可追溯的研究工作流。
        </p>
        <div className="login-line">
          <span>01 / 基础平台</span>
          <span>RESEARCH ONLY</span>
        </div>
      </div>
      <form className="login-card" onSubmit={submit}>
        <span className="eyebrow">SECURE WORKSPACE</span>
        <h2>登录研究工作台</h2>
        <p>本机部署 · 服务端会话 · 实盘执行关闭</p>
        <label>
          用户名
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            maxLength={80}
          />
        </label>
        <label>
          密码
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            maxLength={256}
          />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={busy}>
          {busy ? "验证中…" : "进入工作台 →"}
        </button>
        <small>
          初始登录信息保存在本机
          <br />
          <code>.runtime/bootstrap-admin.txt</code>
          <br />
          登录后可在系统设置修改密码。
        </small>
      </form>
    </main>
  );
}
