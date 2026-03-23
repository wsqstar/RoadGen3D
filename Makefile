PYTHON := .venv/bin/python
MODEL_DIR := models/clip-vit-base-patch32
MANIFEST := data/real/real_assets_manifest.jsonl
ARTIFACTS := artifacts/real
M4_DIR := artifacts/m4
UI_API_HOST := 127.0.0.1
UI_API_PORT := 8010

.PHONY: dev ui-api ui-web ui-install knowledge-build train collect eval help

help:
	@echo "make dev       - Launch Gradio UI"
	@echo "make ui-api    - Launch the FastAPI design assistant"
	@echo "make ui-web    - Launch the new Vite design workbench"
	@echo "make ui-install - Install ui/web dependencies"
	@echo "make knowledge-build - Build the complete-streets PDF knowledge base"
	@echo "make collect   - Collect M4 policy training data"
	@echo "make train     - Train layout policy (M4)"
	@echo "make eval      - Run M4 engineering evaluation"

dev:
	$(PYTHON) scripts/m1_gradio_app.py --host 127.0.0.1 --port 7860 --inbrowser

ui-api:
	$(PYTHON) -m uvicorn ui.api.main:app --host $(UI_API_HOST) --port $(UI_API_PORT) --reload

ui-web:
	npm --prefix ui/web run dev

ui-install:
	npm --prefix ui/web install

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
