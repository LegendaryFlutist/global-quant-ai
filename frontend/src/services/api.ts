let csrf = "";
/** 更新仅存内存的会话CSRF；退出时传空字符串，不持久保存令牌。 */
export function setCSRF(value: string) {
  csrf = value;
}
/** 发起同源已认证JSON请求；携带CSRF，失败抛出脱敏业务错误并在401时清理身份。 */
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    ...options,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      ...options.headers,
    },
  });
  const body = await response.json().catch(() => ({ detail: "服务响应无效" }));
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("session-expired"));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail),
    );
  }
  return body as T;
}
/** 以统一JSON与CSRF契约提交POST；返回经调用方类型约束的服务器结果。 */
export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
/** 按显式显示时区格式化时刻，缺失值用占位符，不改写数据的UTC边界。 */
export function date(
  value: number | string | null | undefined,
  timezone = "Asia/Shanghai",
) {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: timezone,
    hour12: false,
  });
}
