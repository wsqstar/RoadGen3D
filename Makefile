PYTHON := .venv/bin/python
MODEL_DIR := models/clip-vit-base-patch32
MANIFEST := data/real/real_assets_manifest.jsonl
ARTIFACTS := artifacts/real
M4_DIR := artifacts/m4
UI_API_HOST := 127.0.0.1
UI_API_PORT := 8010
WORKBENCH_WEB_HOST := 127.0.0.1
WORKBENCH_WEB_PORT := 4174
VIEWER_HOST := 127.0.0.1
VIEWER_PORT := 4173
GRAPH_TEMPLATE := hkust_gz_gate

.PHONY: dev ui-api workbench-api workbench-web workbench-install viewer-web viewer-install ui-web ui-install knowledge-build train collect eval snapshot-diff test test-pipeline test-batch test-single help

help:
	@echo "make dev               - Launch workbench API + workbench web + viewer web"
	@echo "make workbench-api     - Launch the FastAPI design assistant"
	@echo "make workbench-web     - Launch the new Vite generation workbench"
	@echo "make workbench-install - Install web/workbench dependencies"
	@echo "make viewer-web        - Launch the standalone web viewer"
	@echo "make viewer-install    - Install web/viewer dependencies"
	@echo "make ui-api/ui-web/ui-install - Backward-compatible aliases"
	@echo "make knowledge-build   - Build the complete-streets PDF knowledge base"
	@echo "make collect           - Collect M4 policy training data"
	@echo "make train             - Train layout policy (M4)"
	@echo "make eval              - Run M4 engineering evaluation"
	@echo "make snapshot-diff     - Run snapshot diff pipeline (real LLM, single query)"
	@echo ""
	@echo "Test Commands:"
	@echo "  make test             - Run unit tests (pytest) to verify system integrity"
	@echo "  make test-pipeline    - Run full automated test with live progress (starts API + tests)"
	@echo "  make test-pipeline GRAPH_TEMPLATE=hkust_gz_gate_all - Run with alternate graph template"
	@echo "  make test-batch       - Run batch test with all 6 templates in parallel (starts API + tests)"
	@echo "  make test-batch RANDOM_TEMPLATE=1 - Random graph template per preset"
	@echo "  make test-batch USE_LLM=1 - Enable LLM dynamic config generation"
	@echo "  make test-single      - Run single test (requires API already running)"
	@echo "  make test-preset     PRESET=<id> - Run with specific preset"
	@echo "  make test-report      - View latest test report summary"
	@echo ""
	@echo "Test Pipeline Options:"
	@echo "  PRESET=<id>          - Specific preset (pedestrian_friendly, commercial_vitality, etc.)"
	@echo "  PRESETS=<ids>        - Multiple presets for batch test (space-separated)"
	@echo "  RANDOM_TEMPLATE=1     - Random graph template per preset"
	@echo "  USE_LLM=1            - Enable LLM dynamic config generation"
	@echo "  TEST_PYTEST_ARGS=... - Extra pytest arguments (default: -v --tb=short)"

dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) workbench-api & \
	$(MAKE) workbench-web & \
	$(MAKE) viewer-web & \
	wait

gradio-dev:
	@echo "ERROR: gradio-dev 已废弃。请使用 'make dev' 启动新的前后端分离架构"
	@echo "  - make dev: 启动 workbench API + workbench web + viewer web"
	@exit 1

workbench-api:
	@if lsof -nP -iTCP:$(UI_API_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Workbench API already available at http://$(UI_API_HOST):$(UI_API_PORT)"; \
	else \
		MPLCONFIGDIR=/tmp/mpl-roadgen $(PYTHON) -m uvicorn web.api.main:app --host $(UI_API_HOST) --port $(UI_API_PORT); \
	fi

ui-api: workbench-api

workbench-web:
	@if lsof -nP -iTCP:$(WORKBENCH_WEB_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Workbench web already available at http://$(WORKBENCH_WEB_HOST):$(WORKBENCH_WEB_PORT)"; \
	else \
		npm --prefix web/workbench run dev; \
	fi

ui-web: workbench-web

workbench-install:
	npm --prefix web/workbench install

ui-install: workbench-install

viewer-web:
	@if lsof -nP -iTCP:$(VIEWER_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Viewer already available at http://$(VIEWER_HOST):$(VIEWER_PORT)"; \
	else \
		npm --prefix web/viewer run dev; \
	fi

viewer-install:
	npm --prefix web/viewer install

knowledge-build:
	$(PYTHON) scripts/knowledge/build_pdf_knowledge_base.py \
		--pdf-path "knowledge/book/Complete streets design guide.pdf" \
		--out-dir knowledge/complete_streets

collect:
	$(PYTHON) scripts/m4_01_collect_policy_data.py \
		--manifest $(MANIFEST) --artifacts $(ARTIFACTS) \
		--out $(M4_DIR)/policy_train.jsonl \
		--model-dir $(MODEL_DIR) --local-files-only

train:
	$(PYTHON) scripts/m4_02_train_layout_policy.py \
		--data $(M4_DIR)/policy_train.jsonl --out-dir $(M4_DIR)

eval:
	$(PYTHON) scripts/m4_10_eval_engineering.py \
		--placement-policy learned --policy-ckpt $(M4_DIR)/layout_policy.pt \
		--compare-rule --manifest $(MANIFEST) --artifacts $(ARTIFACTS) \
		--out-dir $(M4_DIR) --model-dir $(MODEL_DIR) --local-files-only

SNAPSHOT_QUERY ?= "modern pedestrian-friendly street with trees and benches"
SNAPSHOT_ITERS ?= 3

SF_MANIFEST := data/street_furniture/street_furniture_manifest.jsonl

snapshot-diff:
	$(PYTHON) scripts/snapshot_diff.py \
		--query $(SNAPSHOT_QUERY) \
		--max-iterations $(SNAPSHOT_ITERS) \
		--manifest $(SF_MANIFEST) \
		--model-dir $(MODEL_DIR) \
		--local-files-only \
		--device cpu

# ── Unit Tests ─────────────────────────────────────────────────────────────────

TEST_PYTEST_ARGS ?= -v --tb=short

# Run pytest unit tests (skips LLM-dependent tests if API not configured)
test:
	@echo "=========================================="
	@echo "Running Unit Tests (pytest)"
	@echo "=========================================="
	@echo ""
	@echo "Note: Tests requiring LLM API will be auto-skipped"
	@echo "      if llm_base_url and key are not set in .env"
	@echo ""
	uv run pytest tests/ \
		--ignore=tests/test_m2_pipeline.py \
		--ignore=tests/test_m3_street_compose.py \
		--ignore=tests/test_m3_external_tree_import.py \
		--ignore=tests/test_m4_layout_policy.py \
		--ignore=tests/test_m5_compose_constraints.py \
		--ignore=tests/test_m6_neuralsymbolic_pipeline.py \
		$(TEST_PYTEST_ARGS)

# ── Test Pipeline ──────────────────────────────────────────────────────────────

TEST_REPORTS_DIR := artifacts/test_reports

# Run full automated test pipeline: start API, run test, generate report
test-pipeline:
	@echo "=========================================="
	@echo "Workbench 自动化测试 Pipeline"
	@echo "=========================================="
	@echo ""
	@mkdir -p $(TEST_REPORTS_DIR)
	@echo "[1/4] 启动 API 与 Viewer 服务..."
	@trap 'kill 0' INT TERM; \
	$(MAKE) workbench-api & \
	$(MAKE) viewer-web & \
	sleep 3 && \
	echo "[2/4] 等待 API 就绪..." && \
	for i in 1 2 3 4 5; do \
		if curl -s http://$(UI_API_HOST):$(UI_API_PORT)/api/health > /dev/null 2>&1; then \
			echo "[2/4] ✓ API 已就绪"; \
			break; \
		fi; \
		echo "    等待中... ($$i/5)"; \
		sleep 2; \
	done; \
	echo "[3/4] 等待 Viewer 就绪..." && \
	for i in 1 2 3 4 5; do \
		if curl -s http://$(VIEWER_HOST):$(VIEWER_PORT) > /dev/null 2>&1; then \
			echo "[3/4] ✓ Viewer 已就绪"; \
			break; \
		fi; \
		echo "    等待中... ($$i/5)"; \
		sleep 2; \
	done; \
	echo ""; \
	echo "[4/4] 运行测试..."; \
	uv run python scripts/test_workflow.py --graph-template $(GRAPH_TEMPLATE) --output $(TEST_REPORTS_DIR); \
	TEST_EXIT=$$?; \
	echo ""; \
	echo "[汇总] 生成报告汇总..."; \
	uv run python scripts/test_pipeline.py; \
	LATEST_REPORT=$$(ls -t $(TEST_REPORTS_DIR)/test_*.md 2>/dev/null | head -n 1); \
	VIEWER_URL=""; \
	if [ -n "$$LATEST_REPORT" ]; then \
		VIEWER_URL=$$(grep '^- \*\*Viewer URL\*\*:' "$$LATEST_REPORT" 2>/dev/null | sed 's/^- \*\*Viewer URL\*\*: //'); \
	fi; \
	echo ""; \
	echo "=========================================="; \
	echo "Pipeline 完成!"; \
	echo "报告目录: $(TEST_REPORTS_DIR)"; \
	echo "汇总报告: $(TEST_REPORTS_DIR)/SUMMARY.md"; \
	echo "=========================================="; \
	echo ""; \
	if [ -n "$$VIEWER_URL" ] && [ "$$VIEWER_URL" != "N/A" ]; then \
		echo "Viewer 链接: $$VIEWER_URL"; \
		echo ""; \
	fi; \
	echo "服务仍在运行，可继续查看 Viewer。按 Enter 键关闭 API 服务并退出..."; \
	read _ || true; \
	echo "正在关闭服务..."; \
	kill 0 2>/dev/null || true; \
	wait 2>/dev/null || true; \
	exit $$TEST_EXIT

# Run batch test: start API, run all 6 templates in parallel, generate report
test-batch:
	@echo "=========================================="
	@echo "批量测试 Pipeline (并行生成 6 个模板)"
	@echo "=========================================="
	@echo ""
	@mkdir -p $(TEST_REPORTS_DIR)
	@echo "[1/4] 启动 API 与 Viewer 服务..."
	@trap 'kill 0' INT TERM; \
	$(MAKE) workbench-api & \
	$(MAKE) viewer-web & \
	sleep 3 && \
	echo "[2/4] 等待 API 就绪..." && \
	for i in 1 2 3 4 5; do \
		if curl -s http://$(UI_API_HOST):$(UI_API_PORT)/api/health > /dev/null 2>&1; then \
			echo "[2/4] ✓ API 已就绪"; \
			break; \
		fi; \
		echo "    等待中... ($$i/5)"; \
		sleep 2; \
	done; \
	echo "[3/4] 等待 Viewer 就绪..." && \
	for i in 1 2 3 4 5; do \
		if curl -s http://$(VIEWER_HOST):$(VIEWER_PORT) > /dev/null 2>&1; then \
			echo "[3/4] ✓ Viewer 已就绪"; \
			break; \
		fi; \
		echo "    等待中... ($$i/5)"; \
		sleep 2; \
	done; \
	echo ""; \
	echo "[4/4] 运行批量测试..."; \
	if [ "$(RANDOM_TEMPLATE)" = "1" ]; then \
		RANDOM_FLAG="--random-template"; \
	else \
		RANDOM_FLAG="--graph-template $(GRAPH_TEMPLATE)"; \
	fi; \
	if [ "$(USE_LLM)" = "1" ]; then \
		LLM_FLAG="--use-llm"; \
	else \
		LLM_FLAG=""; \
	fi; \
	if [ -n "$(PRESETS)" ]; then \
		uv run python scripts/test_batch.py --all --workers 6 $$RANDOM_FLAG $$LLM_FLAG --output $(TEST_REPORTS_DIR) --presets $(PRESETS); \
	else \
		uv run python scripts/test_batch.py --all --workers 6 $$RANDOM_FLAG $$LLM_FLAG --output $(TEST_REPORTS_DIR); \
	fi; \
	TEST_EXIT=$$?; \
	echo ""; \
	echo "=========================================="; \
	echo "批量测试完成!"; \
	echo "报告目录: $(TEST_REPORTS_DIR)"; \
	echo "=========================================="; \
	echo ""; \
	echo "服务仍在运行，可继续查看 Viewer。按 Enter 键关闭 API 服务并退出..."; \
	read _ || true; \
	echo "正在关闭服务..."; \
	kill 0 2>/dev/null || true; \
	wait 2>/dev/null || true; \
	exit $$TEST_EXIT

# Run single test with random preset (requires API to be running)
test-single:
	@mkdir -p $(TEST_REPORTS_DIR)
	@echo "=========================================="
	@echo "运行单次自动化测试"
	@echo "=========================================="
	@uv run python scripts/test_workflow.py --graph-template $(GRAPH_TEMPLATE) --output $(TEST_REPORTS_DIR); \
	EXIT=$$?; \
	uv run python scripts/test_pipeline.py; \
	exit $$EXIT

# Run test with specific preset
test-preset:
	@mkdir -p $(TEST_REPORTS_DIR)
	@uv run python scripts/test_workflow.py --preset $(PRESET) --graph-template $(GRAPH_TEMPLATE) --output $(TEST_REPORTS_DIR); \
	uv run python scripts/test_pipeline.py

# View latest test report
test-report:
	@if [ -f $(TEST_REPORTS_DIR)/SUMMARY.md ]; then \
		cat $(TEST_REPORTS_DIR)/SUMMARY.md; \
	else \
		echo "未找到汇总报告，请先运行 'make test-pipeline'"; \
	fi
