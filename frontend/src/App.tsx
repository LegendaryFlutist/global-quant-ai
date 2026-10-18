import { useEffect, useRef, useState } from "react";
import type { Asset, Job, PageId, User } from "./types";
import { post } from "./services/api";
import { useAuth } from "./hooks/useAuth";
import { useWorkspaceData } from "./hooks/useWorkspaceData";
import WorkspaceLayout from "./layout/WorkspaceLayout";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import AssetsPage from "./pages/AssetsPage";
import ChartPage from "./pages/ChartPage";
import JobsPage from "./pages/JobsPage";
import CapabilitiesPage from "./pages/CapabilitiesPage";
import SettingsPage from "./pages/SettingsPage";

/** 工作台路由协调层；身份、轮询与各页面分离，业务能力由后端再次授权。 */
export default function App() {
  const auth = useAuth();
  const data = useWorkspaceData(Boolean(auth.user));
  const [page, setPage] = useState<PageId>("dashboard");
  const [notice, setNotice] = useState("");
  const [chartAsset, setChartAsset] = useState("binance_usdm:KORUUSDT");
  const initializedDefaults = useRef(false);
  useEffect(() => {
    if (data.settings && !initializedDefaults.current) {
      setChartAsset(data.settings.display.default_asset);
      initializedDefaults.current = true;
    }
  }, [data.settings]);
  /** 接受认证结果并回到总览，密码已在登录组件清空。 */
  function loginComplete(user: User) {
    auth.acceptLogin(user);
    setPage("dashboard");
  }
  /** 页面切换时清除上一个任务提示，不改变任何运行中任务。 */
  function navigate(next: PageId) {
    setPage(next);
    setNotice("");
  }
  /** 只选择研究图表的标的，不提交交易。 */
  function openChart(asset: Asset) {
    setChartAsset(asset.id);
    setPage("charts");
  }
  /** 提交受限后台任务并刷新持久状态；由后端完成CSRF、去重和白名单校验。 */
  async function createTask(kind: string, asset_id?: string) {
    try {
      const result = await post<{ id: string }>("/tasks", { kind, asset_id });
      setNotice("任务已创建：" + result.id.slice(0, 8));
      await data.refresh();
    } catch (error) {
      data.setError((error as Error).message);
    }
  }
  /** 使用原任务的已保存参数重新入队；不会把旧失败记录改为成功。 */
  async function retryTask(job: Job) {
    try {
      await post("/tasks", { kind: job.kind, ...job.parameters });
      setNotice("已重新入队");
      await data.refresh();
    } catch (error) {
      data.setError((error as Error).message);
    }
  }
  /** 撤销当前服务端会话并处理网络错误。 */
  async function logout() {
    try {
      await auth.logout();
    } catch (error) {
      data.setError((error as Error).message);
    }
  }
  /** 保存偏好后重读实际服务器状态，页面不自行伪造已保存值。 */
  async function settingsSaved() {
    await data.refresh();
    setNotice("设置已保存");
  }
  if (auth.checking) return <div className="empty">正在连接研究工作台…</div>;
  if (!auth.user) return <LoginPage onLogin={loginComplete} />;
  const timezone = data.settings?.display.timezone ?? "Asia/Shanghai";
  return (
    <WorkspaceLayout
      user={auth.user}
      page={page}
      onNavigate={navigate}
      onLogout={() => void logout()}
      error={data.error}
      notice={notice}
    >
      {page === "dashboard" && (
        <DashboardPage
          overview={data.overview}
          providers={data.providers}
          timezone={timezone}
          onRefresh={data.refresh}
          onChart={openChart}
          onVerify={() => createTask("VERIFY_ASSETS")}
        />
      )}
      {page === "assets" && (
        <AssetsPage
          assets={data.assets}
          timezone={timezone}
          onChart={openChart}
          onVerify={() => createTask("VERIFY_ASSETS")}
        />
      )}
      {page === "charts" && (
        <ChartPage
          key={chartAsset}
          assets={data.assets}
          initialAsset={chartAsset}
          initialInterval={data.settings?.display.default_interval ?? "4h"}
          timezone={timezone}
          onTask={setNotice}
        />
      )}
      {page === "jobs" && (
        <JobsPage
          jobs={data.jobs}
          timezone={timezone}
          onRefresh={data.refresh}
          onRetry={retryTask}
        />
      )}
      {page === "roadmap" && (
        <CapabilitiesPage capabilities={data.capabilities} />
      )}
      {page === "settings" && data.settings && (
        <SettingsPage
          settings={data.settings}
          assets={data.assets}
          onSaved={settingsSaved}
          onLogout={auth.clearUser}
        />
      )}
    </WorkspaceLayout>
  );
}
