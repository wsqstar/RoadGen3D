PYTHON := .venv/bin/python
MODEL_DIR := models/clip-vit-base-patch32
MANIFEST := data/real/real_assets_manifest.jsonl
ARTIFACTS := artifacts/real
M4_DIR := artifacts/m4

.PHONY: dev train collect eval help

help:
	@echo "make dev       - Launch Gradio UI"
	@echo "make collect   - Collect M4 policy training data"
	@echo "make train     - Train layout policy (M4)"
	@echo "make eval      - Run M4 engineering evaluation"

dev:
	$(PYTHON) scripts/m1_gradio_app.py --host 127.0.0.1 --port 7860 --inbrowser

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
