import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import AppErrorBoundary from "./components/AppErrorBoundary";
import "./style.css";
const rootElement = document.getElementById("root");
if (!rootElement) throw new Error("Application root element is missing");
rootElement.dataset.appMounted = "true";
ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <App />
    </AppErrorBoundary>
  </React.StrictMode>,
);
