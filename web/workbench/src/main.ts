import "./style.css";

import { mountWorkbench } from "./app";

const root = document.querySelector<HTMLDivElement>("#app");

if (!root) {
  throw new Error("Missing #app root element.");
}

mountWorkbench(root);
