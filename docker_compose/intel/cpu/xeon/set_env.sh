#!/usr/bin/env bash
# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Public IP of the host running the services.
export host_ip=$(hostname -I | awk '{print $1}')

# LLM served by TGI. TGI pulls from Hugging Face, so use the HF repo name here.
# (For a local Ollama run instead of TGI, set LLM_MODEL_ID to the Ollama tag, e.g. "qwen2.5:7b".)
export LLM_MODEL_ID="Qwen/Qwen2.5-7B-Instruct"

# Required to pull gated models from Hugging Face.
export HUGGINGFACEHUB_API_TOKEN=${HUGGINGFACEHUB_API_TOKEN:-""}

# Where TGI caches model weights.
export MODEL_CACHE=${MODEL_CACHE:-"./data"}

# Image registry / tag.
export REGISTRY=${REGISTRY:-"opea"}
export TAG=${TAG:-"latest"}

# Proxy passthrough (leave empty if not behind a proxy).
export no_proxy="${no_proxy},${host_ip},tgi-service,policydriftcheck-backend-server,policydriftcheck-ui-server"
export http_proxy=${http_proxy:-""}
export https_proxy=${https_proxy:-""}

echo "Environment configured. host_ip=${host_ip}, model=${LLM_MODEL_ID}"
