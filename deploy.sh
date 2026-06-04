#!/usr/bin/env bash
# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# One-click local deploy for DriftGuardian.
# Brings up a local Ollama-backed LLM, the FastAPI gateway, and the Flask UI.
#
# Usage:   ./deploy.sh            # native: python venv + local Ollama
#          ./deploy.sh --docker   # containers: docker compose up --build
#
# Override defaults via env, e.g.:  UI_PORT=8501 ./deploy.sh

set -euo pipefail

# ---- Configuration (single source of truth; override via env) ---------------
UI_PORT="${UI_PORT:-5173}"
BACKEND_PORT="${BACKEND_PORT:-8888}"
LLM_MODEL_ID="${LLM_MODEL_ID:-qwen2.5:14b-instruct}"
LLM_ENDPOINT="${LLM_ENDPOINT:-http://localhost:11434/v1/chat/completions}"
BACKEND_URL="${BACKEND_URL:-http://localhost:${BACKEND_PORT}}"

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

log() { printf '\033[36m[deploy]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[deploy] %s\033[0m\n' "$*" >&2; exit 1; }

# ---- Docker path ------------------------------------------------------------
if [[ "${1:-}" == "--docker" ]]; then
  command -v docker >/dev/null 2>&1 || die "Docker not found."
  log "Starting the full stack with Docker Compose..."
  UI_PORT="$UI_PORT" BACKEND_PORT="$BACKEND_PORT" LLM_MODEL_ID="$LLM_MODEL_ID" \
    docker compose up -d --build
  log "Stack is up. UI: http://localhost:${UI_PORT}  ·  API: http://localhost:${BACKEND_PORT}"
  log "Pull the model into the ollama container (first run only):"
  log "  docker compose exec ollama ollama pull ${LLM_MODEL_ID}"
  exit 0
fi

# ---- Native path ------------------------------------------------------------
command -v python3 >/dev/null 2>&1 || die "python3 not found."

# 1. LLM server (Ollama)
if command -v ollama >/dev/null 2>&1; then
  if ! curl -s "http://localhost:11434/api/tags" >/dev/null 2>&1; then
    log "Starting 'ollama serve' in the background..."
    ollama serve >/tmp/driftguardian-ollama.log 2>&1 &
    sleep 3
  fi
  log "Ensuring model '${LLM_MODEL_ID}' is available (first pull can take a while)..."
  ollama pull "$LLM_MODEL_ID" || die "Failed to pull ${LLM_MODEL_ID}."
else
  log "WARNING: Ollama not found. Install it from https://ollama.com, or point"
  log "         LLM_ENDPOINT at any OpenAI-compatible server before continuing."
fi

# 2. Python environment
if [[ ! -d .venv ]]; then
  log "Creating virtual environment..."
  python3 -m venv .venv
fi
log "Installing dependencies..."
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt -r ui/requirements.txt

# 3. Start backend gateway
log "Starting backend gateway on :${BACKEND_PORT}..."
MEGA_SERVICE_PORT="$BACKEND_PORT" LLM_ENDPOINT="$LLM_ENDPOINT" LLM_MODEL_ID="$LLM_MODEL_ID" \
  ./.venv/bin/python policydriftcheck.py >/tmp/driftguardian-backend.log 2>&1 &
BACKEND_PID=$!

# Wait for health
for _ in $(seq 1 30); do
  if curl -s "${BACKEND_URL}/v1/health" >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -s "${BACKEND_URL}/v1/health" >/dev/null 2>&1 \
  && log "Backend healthy." \
  || die "Backend did not become healthy. See /tmp/driftguardian-backend.log"

# 4. Start UI (foreground)
log "Starting UI on :${UI_PORT}  ->  http://localhost:${UI_PORT}"
log "(backend pid ${BACKEND_PID}; Ctrl-C to stop the UI, then 'kill ${BACKEND_PID}')"
UI_PORT="$UI_PORT" BACKEND_URL="$BACKEND_URL" ./.venv/bin/python ui/app.py
