const statuses: Record<string, string> = {
  SUCCEEDED: "已完成",
  QUEUED: "等待执行",
  RUNNING: "执行中",
  RETRYING: "等待重试",
  FAILED: "失败",
  QUEUE_ERROR: "入队失败",
  VERIFIED: "已实时核验",
  VERIFIED_SNAPSHOT: "公开元数据快照",
  RESEARCH_ONLY: "仅研究登记",
  UNVERIFIED: "未核验",
  UNAVAILABLE: "连接不可用",
  UNKNOWN: "未知",
  CONNECTED: "已连接",
  REGISTERED_RESEARCH_ONLY: "仅登记",
};
/** 把后端状态码转换为中文；未知新状态保留原码，不默认视为成功。 */
export const label = (s: string) => statuses[s] ?? s;
/** 按真实成功、失败、未知状态呈现统一徽标，返回展示用JSX。 */
export const badge = (s: string) => (
  <span
    className={
      "badge " +
      (["SUCCEEDED", "VERIFIED", "CONNECTED"].includes(s)
        ? "positive"
        : ["FAILED", "QUEUE_ERROR", "UNAVAILABLE"].includes(s)
          ? "negative"
          : "neutral")
    }
  >
    {label(s)}
  </span>
);
