(function () {
  const root = document.querySelector("[data-control-shell]");
  if (!root) return;

  function toggleClass(buttonSelector, className, pressedLabel, releasedLabel) {
    const button = document.querySelector(buttonSelector);
    if (!button) return;
    button.addEventListener("click", () => {
      const isActive = root.classList.toggle(className);
      button.setAttribute("aria-pressed", String(isActive));
      if (pressedLabel && releasedLabel) {
        button.setAttribute("aria-label", isActive ? pressedLabel : releasedLabel);
        button.title = isActive ? pressedLabel : releasedLabel;
      }
    });
  }

  toggleClass("[data-toggle-left]", "left-collapsed", "展开左侧浏览栏", "收起左侧浏览栏");
  toggleClass("[data-toggle-right]", "right-collapsed", "展开右侧分析栏", "收起右侧分析栏");

  document.querySelectorAll("[data-open-right]").forEach((button) => {
    button.addEventListener("click", () => {
      root.classList.remove("right-collapsed");
      const isActive = root.classList.toggle("right-open");
      button.setAttribute("aria-pressed", String(isActive));
      button.setAttribute("aria-label", isActive ? "关闭右侧分析栏" : "打开右侧分析栏");
    });
  });

  const tray = document.querySelector("[data-bottom-tray]");
  const trayButton = document.querySelector("[data-toggle-tray]");
  if (tray && trayButton) {
    trayButton.addEventListener("click", () => {
      const collapsed = tray.classList.toggle("collapsed");
      trayButton.setAttribute("aria-expanded", String(!collapsed));
      trayButton.textContent = collapsed ? "展开" : "收起";
    });
  }

  const generationDialog = document.querySelector("[data-generation-dialog]");
  const openGenerationButtons = Array.from(document.querySelectorAll("[data-open-generate]"));
  const closeGenerationButtons = Array.from(document.querySelectorAll("[data-close-generate]"));
  let generationReturnTarget = null;

  function setGenerationDialogState(isOpen) {
    openGenerationButtons.forEach((button) => {
      button.setAttribute("aria-haspopup", "dialog");
      button.setAttribute("aria-controls", generationDialog?.id || "generation-dialog");
      button.setAttribute("aria-expanded", String(isOpen));
    });
  }

  function closeGenerationDialog() {
    if (!generationDialog) return;
    if (typeof generationDialog.close === "function" && generationDialog.open) generationDialog.close();
    else {
      generationDialog.removeAttribute("open");
      setGenerationDialogState(false);
      generationReturnTarget?.focus?.();
    }
  }

  if (generationDialog) {
    setGenerationDialogState(false);
    openGenerationButtons.forEach((button) => {
      button.addEventListener("click", () => {
        generationReturnTarget = button;
        if (typeof generationDialog.showModal === "function" && !generationDialog.open) generationDialog.showModal();
        else generationDialog.setAttribute("open", "");
        setGenerationDialogState(true);
        generationDialog.querySelector("select, textarea, input, button:not([data-close-generate])")?.focus();
      });
    });
    closeGenerationButtons.forEach((button) => {
      button.addEventListener("click", closeGenerationDialog);
    });
    generationDialog.addEventListener("click", (event) => {
      if (event.target === generationDialog) closeGenerationDialog();
    });
    generationDialog.addEventListener("close", () => {
      setGenerationDialogState(false);
      generationReturnTarget?.focus?.();
    });
  }

  document.querySelectorAll("[data-tabs]").forEach((group) => {
    const tabs = Array.from(group.querySelectorAll("[role='tab']"));
    const targetRoot = document.getElementById(group.getAttribute("data-tabs") || "");
    if (!targetRoot) return;
    const panels = Array.from(targetRoot.querySelectorAll("[role='tabpanel']"));
    const activateTab = (tab, shouldFocus = false) => {
      const id = tab.getAttribute("aria-controls");
      tabs.forEach((item) => {
        const selected = item === tab;
        item.setAttribute("aria-selected", String(selected));
        item.setAttribute("tabindex", selected ? "0" : "-1");
      });
      panels.forEach((panel) => {
        panel.hidden = panel.id !== id;
      });
      if (shouldFocus) tab.focus();
    };
    tabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        activateTab(tab);
      });
      tab.addEventListener("keydown", (event) => {
        const currentIndex = tabs.indexOf(tab);
        let nextIndex = currentIndex;
        if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % tabs.length;
        else if (event.key === "ArrowLeft") nextIndex = (currentIndex - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") nextIndex = 0;
        else if (event.key === "End") nextIndex = tabs.length - 1;
        else return;
        event.preventDefault();
        activateTab(tabs[nextIndex], true);
      });
    });
  });

  const schemeButtons = Array.from(document.querySelectorAll("[data-scheme-filter]"));
  const schemeCards = Array.from(document.querySelectorAll("[data-scheme]"));
  schemeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const filter = button.getAttribute("data-scheme-filter");
      schemeButtons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      schemeCards.forEach((card) => {
        const shouldShow = filter === "all" || card.getAttribute("data-scheme") === filter;
        card.style.display = shouldShow ? "" : "none";
      });
    });
  });

  const sceneItems = Array.from(document.querySelectorAll(".scene-item"));
  const status = document.querySelector("[data-status-line]");
  const statusLine = status?.closest(".status-line");
  const stageRegion = document.querySelector("[data-stage-region]");
  const stateCards = Array.from(document.querySelectorAll("[data-state-card]"));
  const runButtons = Array.from(document.querySelectorAll("[data-run]"));

  function setStateCard(state) {
    stateCards.forEach((card) => {
      card.classList.toggle("is-active", card.getAttribute("data-state-card") === state);
    });
  }

  function setRunState(state, message) {
    if (status) status.textContent = message;
    if (statusLine) statusLine.setAttribute("data-state", state);
    if (stageRegion) stageRegion.setAttribute("aria-busy", String(state === "loading"));
    setStateCard(state);
    runButtons.forEach((button) => {
      if (!button.dataset.defaultLabel) button.dataset.defaultLabel = button.textContent || "";
      button.disabled = state === "loading";
      button.textContent = state === "loading" ? "生成中" : button.dataset.defaultLabel;
    });
  }

  sceneItems.forEach((item) => {
    item.addEventListener("click", () => {
      sceneItems.forEach((entry) => entry.setAttribute("aria-selected", "false"));
      item.setAttribute("aria-selected", "true");
      const label = item.querySelector("strong")?.textContent || "场景";
      if (status) status.textContent = `${label} 已载入，右侧分析栏同步更新。`;
      if (statusLine) statusLine.setAttribute("data-state", "done");
      setStateCard("done");
    });
  });

  if (runButtons.length && status) {
    runButtons.forEach((button) => {
      button.addEventListener("click", () => {
        setRunState("loading", "正在创建场景任务：结构、家具与评估队列已进入追踪。");
        if (button.closest("[data-generation-dialog]")) closeGenerationDialog();
        setTimeout(() => {
          setRunState("done", "生成完成：已加载方案 B，并保留完整任务轨迹。");
        }, 900);
      });
    });
  }

  const copyButton = document.querySelector("[data-copy-summary]");
  if (copyButton) {
    copyButton.addEventListener("click", async () => {
      const text = "RoadGen3D 建议：保持 Viewer 为主入口，左侧浏览、中央控制、右侧分析、底部任务轨迹；保留全部生成、评估、对比、历史、资产能力。";
      try {
        await navigator.clipboard.writeText(text);
        copyButton.textContent = "已复制";
      } catch (_) {
        copyButton.textContent = "可手动复制";
      }
      setTimeout(() => {
        copyButton.textContent = "复制摘要";
      }, 1200);
    });
  }
})();
