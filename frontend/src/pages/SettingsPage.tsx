import { useState, type FormEvent } from "react";
import type { Asset, Settings, Display } from "../types";
import { api, post } from "../services/api";

/** 管理显示偏好及改密；凭据不回显，改密成功撤销当前身份。 */
export default function SettingsPage({
  settings,
  assets,
  onSaved,
  onLogout,
}: {
  settings: Settings;
  assets: Asset[];
  onSaved: () => Promise<void>;
  onLogout: () => void;
}) {
  const [display, setDisplay] = useState<Display>(settings.display);
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  /** 提交显示偏好并重读服务器状态；错误留在本页。 */
  async function saveDisplay(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/settings/display", {
        method: "PUT",
        body: JSON.stringify(display),
      });
      await onSaved();
      setError("");
    } catch (error) {
      setError((error as Error).message);
    }
  }
  /** 仅向本机受控后端提交改密请求，成功后清空输入并退出；不记录密码。 */
  async function changePassword(e: FormEvent) {
    e.preventDefault();
    try {
      await post("/auth/password", {
        current_password: old,
        new_password: next,
      });
      setOld("");
      setNext("");
      onLogout();
    } catch (error) {
      setError((error as Error).message);
    }
  }
  return (
    <>
      <header className="page-heading">
        <div>
          <span className="eyebrow">WORKSPACE SETTINGS</span>
          <h1>系统设置</h1>
          <p>基础偏好与账户安全</p>
        </div>
      </header>
      {error && <div className="alert error">{error}</div>}
      <div className="two-col">
        <form className="panel settings-form" onSubmit={saveDisplay}>
          <h3>显示偏好</h3>
          <label>
            显示时区
            <select
              value={display.timezone}
              onChange={(e) =>
                setDisplay({
                  ...display,
                  timezone: e.target.value as Display["timezone"],
                })
              }
            >
              <option>Asia/Shanghai</option>
              <option>UTC</option>
            </select>
          </label>
          <label>
            默认周期
            <select
              value={display.default_interval}
              onChange={(e) =>
                setDisplay({ ...display, default_interval: e.target.value })
              }
            >
              {[
                "15m",
                "30m",
                "1h",
                "2h",
                "4h",
                "6h",
                "12h",
                "1d",
                "1w",
                "1M",
              ].map((i) => (
                <option key={i}>{i}</option>
              ))}
            </select>
          </label>
          <label>
            默认标的
            <select
              value={display.default_asset}
              onChange={(e) =>
                setDisplay({ ...display, default_asset: e.target.value })
              }
            >
              {assets.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.symbol} · {a.venue}
                </option>
              ))}
            </select>
          </label>
          <button className="primary">保存偏好</button>
        </form>
        <form className="panel settings-form" onSubmit={changePassword}>
          <h3>修改登录密码</h3>
          <label>
            当前密码
            <input
              type="password"
              autoComplete="current-password"
              value={old}
              onChange={(e) => setOld(e.target.value)}
              required
            />
          </label>
          <label>
            新密码（至少12个字符）
            <input
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
              minLength={12}
              maxLength={256}
              required
            />
          </label>
          <p>
            修改后全部会话立即失效，需要重新登录。本机初始密码文件不会随密码修改而更新。
          </p>
          <button>修改密码并退出</button>
        </form>
        <section className="panel">
          <h3>交易与 API</h3>
          <p>
            当前仅使用公开行情，无 API Key
            输入或保存功能。实盘下单服务尚未实现，不能通过页面、环境变量或模型启用。
          </p>
          <span className="badge neutral">Phase 6 / 7 待开发</span>
        </section>
        <section className="panel">
          <h3>邮件与通知</h3>
          <p>
            既有研究脚本继续记录模拟邮件。Web SMTP 配置、凭据加密与通知投递将在
            Phase 6 实现。
          </p>
          <span className="badge neutral">模拟研究记录</span>
        </section>
      </div>
    </>
  );
}
