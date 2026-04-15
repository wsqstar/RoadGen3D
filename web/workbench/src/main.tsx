import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "antd/dist/reset.css";
import App from "./App";

const rootEl = document.getElementById("app");
if (!rootEl) {
  throw new Error("Missing #app root element.");
}

createRoot(rootEl).render(
  <StrictMode>
    <App />
  </StrictMode>
);
