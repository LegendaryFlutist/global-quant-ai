/** Replace the static boot message when same-origin module scripts fail to mount React. */
window.setTimeout(() => {
  const root = document.getElementById("root");
  if (root && !root.dataset.appMounted) {
    root.innerHTML = "<main class=app-failure><h1>前端脚本未能启动</h1><p>页面资源没有完成初始化。请按 Ctrl+Shift+R 强制刷新；如果仍失败，请检查浏览器控制台中的脚本加载错误。</p></main>";
  }
}, 15000);
