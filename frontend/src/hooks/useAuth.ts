import { useEffect, useState } from "react";
import type { User } from "../types";
import { api, post, setCSRF } from "../services/api";

/** 恢复服务端身份并监听401；会话/CSRF只留在内存，不写localStorage。 */
export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  useEffect(() => {
    let active = true;
    /** 身份失效时同时清理CSRF，避免旧用户令牌继续用于写请求。 */
    function clearIdentity() {
      setUser(null);
      setCSRF("");
    }
    api<User>("/auth/me")
      .then((user) => {
        if (active) {
          setUser(user);
          setCSRF(user.csrf_token);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (active) setChecking(false);
      });
    window.addEventListener("session-expired", clearIdentity);
    return () => {
      active = false;
      window.removeEventListener("session-expired", clearIdentity);
    };
  }, []);
  /** 登录成功后接受服务器返回的身份及CSRF；调用方不能仅凭用户名绕过后端。 */
  function acceptLogin(value: User) {
    setCSRF(value.csrf_token);
    setUser(value);
  }
  /** 清理本地身份；用于服务器已撤销全部会话的改密流程。 */
  function clearUser() {
    setUser(null);
    setCSRF("");
  }
  /** 请求服务器撤销当前会话；失败交给页面显示，不假装已退出。 */
  async function logout() {
    await post("/auth/logout", {});
    clearUser();
  }
  return { user, checking, acceptLogin, clearUser, logout };
}
