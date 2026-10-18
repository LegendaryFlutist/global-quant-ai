import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Asset,
  Overview,
  Job,
  Provider,
  Capability,
  Settings,
} from "../types";
import { api } from "../services/api";

/** 管理工作台的只读数据与10秒轮询；不会发起采集、训练、邮件或下单。 */
export function useWorkspaceData(enabled: boolean) {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  /** 一次刷新全部已认证页面数据；禁止并发轮询堆积，失败保留最近缓存并显示原因。 */
  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const result = await Promise.all([
        api<Asset[]>("/assets"),
        api<Overview>("/dashboard"),
        api<Job[]>("/tasks"),
        api<Provider[]>("/providers"),
        api<Capability[]>("/capabilities"),
        api<Settings>("/settings"),
      ]);
      setAssets(result[0]);
      setOverview(result[1]);
      setJobs(result[2]);
      setProviders(result[3]);
      setCapabilities(result[4]);
      setSettings(result[5]);
      setError("");
    } catch (error) {
      setError((error as Error).message);
    } finally {
      inFlight.current = false;
    }
  }, []);
  useEffect(() => {
    if (!enabled) return;
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(timer);
  }, [enabled, refresh]);
  return {
    assets,
    overview,
    jobs,
    providers,
    capabilities,
    settings,
    error,
    setError,
    refresh,
  };
}
