import React from "react";

/** Keep a React render failure inside the application shell and show a recoverable message. */
export default class AppErrorBoundary extends React.Component<
  React.PropsWithChildren,
  { failed: boolean }
> {
  state = { failed: false };

  /** Convert a descendant render failure into the fixed, non-sensitive fallback panel. */
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  /** Render children while healthy; let the user reload after a component failure. */
  render() {
    if (this.state.failed) {
      return (
        <main className="app-failure" role="alert">
          <h1>页面组件运行异常</h1>
          <p>工作台未能完成渲染。请先重新加载；若问题再次出现，请查看浏览器控制台的第一条错误。</p>
          <button onClick={() => window.location.reload()}>重新加载页面</button>
        </main>
      );
    }
    return this.props.children;
  }
}
