PYTHON := .venv/bin/python
MODEL_DIR := models/clip-vit-base-patch32
MANIFEST := data/real/real_assets_manifest.jsonl
ARTIFACTS := artifacts/real
M4_DIR := artifacts/m4
UI_API_HOST := 127.0.0.1
UI_API_PORT := 8010
WORKBENCH_WEB_HOST := 127.0.0.1
WORKBENCH_WEB_PORT := 4174
VIEWER_HOST ?= 127.0.0.1
VIEWER_PORT ?= 4173
VIEWER_PORT_SCAN_LIMIT := 20
VIEWER_IDENTITY_TEXT := RoadGen3D Viewer
GRAPH_TEMPLATE := hkust_gz_gate
ENABLE_ARCHIVED_WORKBENCH ?= 0

.PHONY: dev ui-api workbench-api workbench-web workbench-install viewer-web viewer-install ui-web ui-install knowledge-build train collect eval snapshot-diff test test-pipeline test-batch test-preset help

help:
	@echo "make dev               - Launch API + Viewer web"
	@echo "make workbench-api     - Launch the FastAPI design assistant API"
	@echo "make workbench-web     - Archived legacy React workbench; set ENABLE_ARCHIVED_WORKBENCH=1 to launch"
	@echo "make workbench-install - Archived legacy React workbench install; opt in with ENABLE_ARCHIVED_WORKBENCH=1"
	@echo "make viewer-web        - Launch the standalone web viewer (auto-selects a free port if 4173 is busy)"
	@echo "make viewer-install    - Install web/viewer dependencies"
	@echo "make ui-api/ui-web/ui-install - Backward-compatible aliases (ui-web/ui-install now target Viewer)"
	@echo "make knowledge-build   - Build the complete-streets PDF knowledge base"
	@echo "make collect           - Collect layout policy training data"
	@echo "make train             - Train layout policy"
	@echo "make eval              - Run layout engineering evaluation"
	@echo "make snapshot-diff     - Run snapshot diff pipeline (real LLM, single query)"
	@echo ""
	@echo "Test Commands:"
	@echo "  make test             - Run unit tests (pytest) to verify system integrity"
	@echo "  make test-pipeline    - Run test with random template (default)"
	@echo "  make test-pipeline GRAPH_TEMPLATE=hkust_gz_gate_all - Run with specific template"
	@echo "  make test-pipeline USE_LLM=1 - Enable LLM dynamic config generation"
	@echo "  make test-pipeline RANDOM_TEMPLATE=1 - Force random template (default)"
	@echo "  make test-batch       - Run batch test with all 6 templates in parallel"
	@echo "  make test-batch RANDOM_TEMPLATE=1 - Random graph template per preset"
	@echo "  make test-batch USE_LLM=1 - Enable LLM dynamic config generation"
	@echo "  make test-preset      - Run single test (PRESET=<id> for specific preset)"
	@echo "  make test-preset PRESET=<id> GRAPH_TEMPLATE=xxx - Run with specific preset and template"
	@echo "  make test-report      - View latest test report summary"
	@echo ""
	@echo "Test Pipeline Options:"
	@echo "  GRAPH_TEMPLATE=<id>   - Use specific graph template (disables random)"
	@echo "  RANDOM_TEMPLATE=1      - Force random template (default for test-pipeline)"
	@echo "  USE_LLM=1             - Enable LLM dynamic config generation"
	@echo "  PRESET=<id>          - Specific preset for test-single/test-preset"
	@echo "  TEST_PYTEST_ARGS=... - Extra pytest arguments (default: -v --tb=short)"

dev:
	@set -e; \
	viewer_port="$(VIEWER_PORT)"; \
	search_end=$$(( $(VIEWER_PORT) + $(VIEWER_PORT_SCAN_LIMIT) )); \
	if curl -fsS --max-time 1 "http://$(VIEWER_HOST):$$viewer_port/" 2>/dev/null | grep -q "$(VIEWER_IDENTITY_TEXT)"; then \
		echo "RoadGen3D Viewer already available at http://$(VIEWER_HOST):$$viewer_port"; \
	elif lsof -nP -iTCP:$$viewer_port -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Port $$viewer_port is occupied by another service; looking for a free Viewer port."; \
		found_port=""; \
		for candidate in $$(seq $$(( $$viewer_port + 1 )) $$search_end); do \
			if ! lsof -nP -iTCP:$$candidate -sTCP:LISTEN >/dev/null 2>&1; then \
				found_port="$$candidate"; \
				break; \
			fi; \
		done; \
		if [ -z "$$found_port" ]; then \
			echo "No free Viewer port found in $(VIEWER_PORT)-$$search_end."; \
			exit 1; \
		fi; \
		viewer_port="$$found_port"; \
		echo "RoadGen3D Viewer will start at http://$(VIEWER_HOST):$$viewer_port"; \
	fi; \
	trap 'kill 0' INT TERM EXIT; \
	ROADGEN_VIEWER_HOST=$(VIEWER_HOST) ROADGEN_VIEWER_PORT=$$viewer_port $(MAKE) workbench-api & \
	ROADGEN_VIEWER_HOST=$(VIEWER_HOST) ROADGEN_VIEWER_PORT=$$viewer_port $(MAKE) viewer-web VIEWER_PORT=$$viewer_port & \
	wait

gradio-dev:
	@echo "ERROR: gradio-dev 已废弃。请使用 'make dev' 启动新的前后端分离架构"
	@echo "  - make dev: 启动 API + Viewer web"
	@exit 1

workbench-api:
	@if lsof -nP -iTCP:$(UI_API_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Design API already available at http://$(UI_API_HOST):$(UI_API_PORT)"; \
	else \
		MPLCONFIGDIR=/tmp/mpl-roadgen $(PYTHON) -m uvicorn web.api.main:app --host $(UI_API_HOST) --port $(UI_API_PORT); \
	fi

ui-api: workbench-api

workbench-web:
	@if [ "$(ENABLE_ARCHIVED_WORKBENCH)" != "1" ]; then \
		echo "web/workbench is archived and is not started by default."; \
		echo "Use ENABLE_ARCHIVED_WORKBENCH=1 make workbench-web to launch the legacy UI."; \
	elif lsof -nP -iTCP:$(WORKBENCH_WEB_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Workbench web already available at http://$(WORKBENCH_WEB_HOST):$(WORKBENCH_WEB_PORT)"; \
	else \
		npm --prefix web/workbench run dev; \
	fi

ui-web: viewer-web

workbench-install:
	@if [ "$(ENABLE_ARCHIVED_WORKBENCH)" != "1" ]; then \
		echo "web/workbench is archived; skipping install."; \
		echo "Use ENABLE_ARCHIVED_WORKBENCH=1 make workbench-install if you need the legacy UI."; \
	else \
		npm --prefix web/workbench install; \
	fi

ui-install: viewer-install

viewer-web:
	@set -e; \
	viewer_port="$(VIEWER_PORT)"; \
	search_end=$$(( $(VIEWER_PORT) + $(VIEWER_PORT_SCAN_LIMIT) )); \
	if curl -fsS --max-time 1 "http://$(VIEWER_HOST):$$viewer_port/" 2>/dev/null | grep -q "$(VIEWER_IDENTITY_TEXT)"; then \
		echo "RoadGen3D Viewer already available at http://$(VIEWER_HOST):$$viewer_port"; \
		exit 0; \
	elif lsof -nP -iTCP:$$viewer_port -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "Port $$viewer_port is occupied by another service; looking for a free Viewer port."; \
		found_port=""; \
		for candidate in $$(seq $$(( $$viewer_port + 1 )) $$search_end); do \
			if ! lsof -nP -iTCP:$$candidate -sTCP:LISTEN >/dev/null 2>&1; then \
				found_port="$$candidate"; \
				break; \
			fi; \
		done; \
		if [ -z "$$found_port" ]; then \
			echo "No free Viewer port found in $(VIEWER_PORT)-$$search_end."; \
			exit 1; \
		fi; \
		viewer_port="$$found_port"; \
		echo "RoadGen3D Viewer will start at http://$(VIEWER_HOST):$$viewer_port"; \
	else \
		echo "RoadGen3D Viewer will start at http://$(VIEWER_HOST):$$viewer_port"; \
	fi; \
	ROADGEN_VIEWER_HOST=$(VIEWER_HOST) ROADGEN_VIEWER_PORT=$$viewer_port npm --prefix web/viewer run dev

viewer-install:
	npm --prefix web/viewer install

knowledge-build:
	$(PYTHON) scripts/knowledge/build_pdf_knowledge_base.py \
		--pdf-path "knowledge/book/Complete streets design guide.pdf" \
		--out-dir knowledge/complete_streets

collect:
	$(PYTHON) scripts/layout_collect_data.py \
		--manifest $(MANIFEST) --artifacts $(ARTIFACTS) \
		--out $(M4_DIR)/policy_train.jsonl \
		--model-dir $(MODEL_DIR) --local-files-only

train:
	$(PYTHON) scripts/layout_train.py \
		--data $(M4_DIR)/policy_train.jsonl --out-dir $(M4_DIR)

eval:
	$(PYTHON) scripts/layout_eval.py \
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
		--ignore=tests/test_asset_index_pipeline.py \
		--ignore=tests/test_street_compose.py \
		--ignore=tests/test_street_compose_external_tree_import.py \
		--ignore=tests/test_m4_layout_policy.py \
		--ignore=tests/test_osm_compose_constraints.py \
		--ignore=tests/test_program_neuralsymbolic_pipeline.py \
		$(TEST_PYTEST_ARGS)

# ── Test Pipeline ──────────────────────────────────────────────────────────────

TEST_REPORTS_DIR := artifacts/test_reports

# Run full automated test pipeline: start API, run test, generate report
# Default: random template, no LLM
# Options:
#   GRAPH_TEMPLATE=<id>  - Use specific template (disables random)
#   RANDOM_TEMPLATE=1    - Force random template selection
#   USE_LLM=1           - Enable LLM dynamic config generation
test-pipeline:
	@echo "=========================================="
	@echo "Viewer 自动化测试 Pipeline"
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
	if [ "$(RANDOM_TEMPLATE)" = "1" ]; then \
		echo "    [配置] 随机选择 Graph Template"; \
		TEMPLATE_FLAG="--random-template"; \
	elif [ -n "$(GRAPH_TEMPLATE)" ]; then \
		echo "    [配置] 使用 Graph Template: $(GRAPH_TEMPLATE)"; \
		TEMPLATE_FLAG="--graph-template $(GRAPH_TEMPLATE)"; \
	else \
		echo "    [配置] 随机选择 Graph Template"; \
		TEMPLATE_FLAG="--random-template"; \
	fi; \
	if [ "$(USE_LLM)" = "1" ]; then \
		echo "    [配置] 启用 LLM 动态生成"; \
		LLM_FLAG="--use-llm"; \
	else \
		echo "    [配置] 使用预设配置 (LLM 禁用)"; \
		LLM_FLAG=""; \
	fi; \
	uv run python scripts/test_workflow.py $$TEMPLATE_FLAG $$LLM_FLAG --output $(TEST_REPORTS_DIR); \
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
	echo "服务仍在运行，可继续查看 Viewer。"; \
	echo "按 Ctrl+C 关闭 API 服务并退出，或按 Enter 立即退出..."; \
	sleep infinity & \
	WAIT_PID=$$!; \
	trap 'kill $$WAIT_PID 2>/dev/null; kill 0 2>/dev/null; exit' INT TERM; \
	wait $$WAIT_PID 2>/dev/null; \
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
	echo "服务仍在运行，可继续查看 Viewer。"; \
	echo "按 Ctrl+C 关闭 API 服务并退出，或按 Enter 立即退出..."; \
	sleep infinity & \
	WAIT_PID=$$!; \
	trap 'kill $$WAIT_PID 2>/dev/null; kill 0 2>/dev/null; exit' INT TERM; \
	wait $$WAIT_PID 2>/dev/null; \
	echo "正在关闭服务..."; \
	kill 0 2>/dev/null || true; \
	wait 2>/dev/null || true; \
	exit $$TEST_EXIT

# Run single test (requires API to be running)
# Options:
#   PRESET=<id>         - Specific preset (default: random)
#   GRAPH_TEMPLATE=<id> - Graph template (default: random)
#   USE_LLM=1          - Enable LLM dynamic generation
test-preset:
	@mkdir -p $(TEST_REPORTS_DIR)
	@echo "=========================================="
	@echo "单次测试 (随机 preset)"
	@echo "=========================================="
	@if [ "$(RANDOM_TEMPLATE)" = "1" ]; then \
		TEMPLATE_FLAG="--random-template"; \
	else \
		TEMPLATE_FLAG="--graph-template $(GRAPH_TEMPLATE)"; \
	fi; \
	if [ "$(USE_LLM)" = "1" ]; then \
		LLM_FLAG="--use-llm"; \
	else \
		LLM_FLAG=""; \
	fi; \
	uv run python scripts/test_workflow.py $$TEMPLATE_FLAG $$LLM_FLAG --output $(TEST_REPORTS_DIR); \
	EXIT=$$?; \
	uv run python scripts/test_pipeline.py; \
	exit $$EXIT

# View latest test report
test-report:
	@if [ -f $(TEST_REPORTS_DIR)/SUMMARY.md ]; then \
		cat $(TEST_REPORTS_DIR)/SUMMARY.md; \
	else \
		echo "未找到汇总报告，请先运行 'make test-pipeline'"; \
	fi
