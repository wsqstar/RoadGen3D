import "./style.css";

import { mountViewer } from "./app";

const root = document.querySelector<HTMLElement>("#app");

if (!root) {
  throw new Error("Missing #app root element.");
}

void mountViewer(root);
