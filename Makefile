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

.PHONY: dev gradio-dev ui-api workbench-api workbench-web workbench-install viewer-web viewer-install ui-web ui-install knowledge-build train collect eval help

help:
	@echo "make dev               - Launch workbench API + workbench web + viewer web"
	@echo "make gradio-dev        - Launch legacy Gradio UI"
	@echo "make workbench-api     - Launch the FastAPI design assistant"
	@echo "make workbench-web     - Launch the new Vite generation workbench"
	@echo "make workbench-install - Install web/workbench dependencies"
	@echo "make viewer-web        - Launch the standalone web viewer"
	@echo "make viewer-install    - Install web/viewer dependencies"
	@echo "make ui-api/ui-web/ui-install - Backward-compatible aliases"
	@echo "make knowledge-build - Build the complete-streets PDF knowledge base"
	@echo "make collect   - Collect M4 policy training data"
	@echo "make train     - Train layout policy (M4)"
	@echo "make eval      - Run M4 engineering evaluation"

dev:
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) workbench-api & \
	$(MAKE) workbench-web & \
	$(MAKE) viewer-web & \
	wait

gradio-dev:
	$(PYTHON) scripts/m1_gradio_app.py --host 127.0.0.1 --port 7860 --inbrowser

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
