# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# DriftGuardian -- developer & deployment shortcuts.
# Run `make help` for the full list.

# ---- Single source of truth for ports & model -------------------------------
# Change UI_PORT here (or `make ui UI_PORT=8501`) to move the UI everywhere.
UI_PORT      ?= 5173
BACKEND_PORT ?= 8888

# LLM defaults target a local Ollama server (OpenAI-compatible API).
LLM_MODEL_ID ?= qwen2.5:7b
LLM_ENDPOINT ?= http://localhost:11434/v1/chat/completions
BACKEND_URL  ?= http://localhost:$(BACKEND_PORT)

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.DEFAULT_GOAL := help
.PHONY: help install ollama model backend ui deploy compose-up compose-down test demo clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: $(VENV)/.installed ## Create the venv and install backend + UI deps
$(VENV)/.installed: requirements.txt ui/requirements.txt
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r ui/requirements.txt pytest
	touch $(VENV)/.installed

ollama: ## Check Ollama is installed (LLM server for local runs)
	@command -v ollama >/dev/null 2>&1 || { echo "Ollama not found. Install from https://ollama.com"; exit 1; }
	@echo "Ollama found. Start it with: ollama serve"

model: ollama ## Pull the LLM (default qwen2.5:7b)
	ollama pull $(LLM_MODEL_ID)

backend: install ## Run the FastAPI gateway (default :8888)
	MEGA_SERVICE_PORT=$(BACKEND_PORT) LLM_ENDPOINT=$(LLM_ENDPOINT) LLM_MODEL_ID=$(LLM_MODEL_ID) \
		$(PY) policydriftcheck.py

ui: install ## Run the Flask UI (default :5173; set UI_PORT to change)
	UI_PORT=$(UI_PORT) BACKEND_URL=$(BACKEND_URL) $(PY) ui/app.py

deploy: ## One-click local stack (pull model, start backend + UI)
	./deploy.sh

compose-up: ## Build & start the full stack with Docker Compose
	docker compose up -d --build

compose-down: ## Stop the Docker Compose stack
	docker compose down

test: install ## Run the unit test suite
	$(PY) -m pytest tests/test_drift_logic.py -v

demo: ## Run the regional-override BLOCK demo against a running backend
	@curl -s -X POST $(BACKEND_URL)/v1/drift_check \
		-F "global_doc=@data/demo/global_policy.md" \
		-F "regional_doc=@data/demo/regional_override.md" \
		-F "sop_doc=@data/demo/sop_block_regional.md" | python3 -m json.tool

clean: ## Remove the venv and Python caches
	rm -rf $(VENV)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
