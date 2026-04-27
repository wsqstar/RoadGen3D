/**
 * 场景对比功能 - 在 app.ts 中添加的事件监听代码
 * 
 * 这段代码应该添加到 mountViewerImpl 函数中，在现有的事件监听部分
 */

// ==================== 场景对比状态 ====================

const sceneCompareState: SceneCompareState = {
  mode: "single",
  sceneA: null,
  sceneB: null,
  metricsA: null,
  metricsB: null,
};

// ==================== 场景选择器填充 ====================

/**
 * 填充场景选择器（从当前可用的场景中）
 */
function populateSceneSelectors(scenes: Array<{ key: string; label: string }>) {
  [sceneASelectEl, sceneBSelectEl].forEach((selectEl) => {
    selectEl.innerHTML = "";
    scenes.forEach((scene) => {
      const option = document.createElement("option");
      option.value = scene.key;
      option.textContent = scene.label;
      selectEl.appendChild(option);
    });
  });
}

// ==================== 事件监听器 ====================

/**
 * 启用双场景对比
 */
enableSceneCompareBtn.addEventListener("click", () => {
  sceneCompareState.mode = "dual";
  
  // 显示场景选择器，隐藏默认场景选择
  selectEl.parentElement?.style.setProperty("display", "none");
  sceneCompareControls.hidden = false;
  
  // 启用按钮禁用
  enableSceneCompareBtn.disabled = true;
  
  // 填充场景选择器
  // TODO: 从当前加载的场景列表中获取
  populateSceneSelectors([
    { key: "scene-a", label: "Scene A" },
    { key: "scene-b", label: "Scene B" },
  ]);
  
  // 显示雷达图容器
  sceneRadarContainer.hidden = false;
  
  // 设置默认标签
  sceneALabel.textContent = "Scene A";
  sceneBLabel.textContent = "Scene B";
  
  // 调整画布大小
  setTimeout(() => {
    resizeRadarCanvas(sceneRadarCanvasA);
    resizeRadarCanvas(sceneRadarCanvasB);
  }, 100);
});

/**
 * 重置为单场景模式
 */
resetSceneModeBtn.addEventListener("click", () => {
  sceneCompareState.mode = "single";
  sceneCompareState.sceneA = null;
  sceneCompareState.sceneB = null;
  sceneCompareState.metricsA = null;
  sceneCompareState.metricsB = null;
  
  // 隐藏场景选择器和雷达图
  sceneCompareControls.hidden = true;
  sceneRadarContainer.hidden = true;
  
  // 恢复默认场景选择
  selectEl.parentElement?.style.setProperty("display", "");
  enableSceneCompareBtn.disabled = false;
});

/**
 * 关闭雷达图视图
 */
closeSceneRadarBtn.addEventListener("click", () => {
  sceneRadarContainer.hidden = true;
});

/**
 * Scene A 选择变化
 */
sceneASelectEl.addEventListener("change", async () => {
  const sceneKey = sceneASelectEl.value;
  sceneCompareState.sceneA = sceneKey;
  
  // TODO: 加载场景A的数据
  // const manifest = await loadManifestForScene(sceneKey);
  // sceneCompareState.metricsA = manifest?.summary || null;
  
  // 更新标签
  sceneALabel.textContent = sceneASelectEl.selectedOptions[0]?.label || "Scene A";
  
  // 更新雷达图
  updateRadarCharts();
});

/**
 * Scene B 选择变化
 */
sceneBSelectEl.addEventListener("change", async () => {
  const sceneKey = sceneBSelectEl.value;
  sceneCompareState.sceneB = sceneKey;
  
  // TODO: 加载场景B的数据
  // const manifest = await loadManifestForScene(sceneKey);
  // sceneCompareState.metricsB = manifest?.summary || null;
  
  // 更新标签
  sceneBLabel.textContent = sceneBSelectEl.selectedOptions[0]?.label || "Scene B";
  
  // 更新雷达图
  updateRadarCharts();
});

/**
 * 更新雷达图
 */
function updateRadarCharts() {
  if (!sceneCompareState.metricsA || !sceneCompareState.metricsB) {
    return;
  }
  
  // 确保画布尺寸正确
  resizeRadarCanvas(sceneRadarCanvasA);
  resizeRadarCanvas(sceneRadarCanvasB);
  
  // 绘制雷达图
  createRadarChart(
    sceneRadarCanvasA,
    sceneCompareState.metricsA,
    sceneALabel.textContent || "Scene A",
    "#3b82f6" // 蓝色
  );
  
  createRadarChart(
    sceneRadarCanvasB,
    sceneCompareState.metricsB,
    sceneBLabel.textContent || "Scene B",
    "#ef4444" // 红色
  );
}

/**
 * 窗口大小变化时调整雷达图画布
 */
window.addEventListener("resize", () => {
  if (sceneCompareState.mode === "dual" && !sceneRadarContainer.hidden) {
    resizeRadarCanvas(sceneRadarCanvasA);
    resizeRadarCanvas(sceneRadarCanvasB);
  }
});
