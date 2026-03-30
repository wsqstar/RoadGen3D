import "./style.css";

import { mountViewer } from "./app";
import { mountSceneGraphPage } from "./scene-graph";
import { mountAssetEditor } from "./asset-editor";

const appRoot = document.querySelector<HTMLElement>("#app");

if (!appRoot) {
  throw new Error("Missing #app root element.");
}

const root = appRoot;

type Route = "viewer" | "scene-graph" | "asset-editor";
type Teardown = () => void;

let currentTeardown: Teardown | undefined;
let currentRenderId = 0;

function resolveRoute(): Route {
  const hash = window.location.hash;
  if (hash === "#scene-graph") return "scene-graph";
  if (hash === "#asset-editor") return "asset-editor";
  return "viewer";
}

async function renderRoute(): Promise<void> {
  const renderId = ++currentRenderId;
  currentTeardown?.();
  currentTeardown = undefined;
  root.innerHTML = "";

  const route = resolveRoute();
  let teardown: Teardown;
  switch (route) {
    case "scene-graph":
      teardown = mountSceneGraphPage(root);
      break;
    case "asset-editor":
      teardown = mountAssetEditor(root);
      break;
    default:
      teardown = await mountViewer(root);
      break;
  }

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
