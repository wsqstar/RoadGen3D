import "./style.css";

import { mountViewer } from "./app";
import { mountSceneGraphPage } from "./scene-graph";

const appRoot = document.querySelector<HTMLElement>("#app");

if (!appRoot) {
  throw new Error("Missing #app root element.");
}

const root = appRoot;

type Teardown = () => void;

let currentTeardown: Teardown | undefined;
let currentRenderId = 0;

function resolveRoute(): "viewer" | "scene-graph" {
  return window.location.hash === "#scene-graph" ? "scene-graph" : "viewer";
}

async function renderRoute(): Promise<void> {
  const renderId = ++currentRenderId;
  currentTeardown?.();
  currentTeardown = undefined;
  root.innerHTML = "";

  const route = resolveRoute();
  const teardown = route === "scene-graph" ? mountSceneGraphPage(root) : await mountViewer(root);

  if (renderId !== currentRenderId) {
    teardown();
    return;
  }

  currentTeardown = teardown;
}

window.addEventListener("hashchange", () => {
  void renderRoute();
});

void renderRoute();
