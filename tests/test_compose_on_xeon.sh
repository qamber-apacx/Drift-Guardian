#!/usr/bin/env bash
# Copyright (C) 2025 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# End-to-end test: builds the images, starts the stack on Xeon (CPU),
# waits for the LLM to load, then submits a sample drift check and
# asserts a verdict is returned.

set -xe

ROOT=$(cd "$(dirname "$0")/.." && pwd)
COMPOSE_DIR="$ROOT/docker_compose/intel/cpu/xeon"
ip_address=$(hostname -I | awk '{print $1}')

function build_docker_images() {
    cd "$ROOT/docker_image_build"
    docker compose -f build.yaml build --no-cache
}

function start_services() {
    cd "$COMPOSE_DIR"
    source set_env.sh
    docker compose up -d
    n=0
    until docker logs policydriftcheck-tgi-service 2>&1 | grep -q "Connected"; do
        n=$((n+1))
        if [[ $n -ge 100 ]]; then
            echo "TGI did not start in time"; docker logs policydriftcheck-tgi-service; exit 1
        fi
        sleep 10s
    done
}

function validate_backend() {
    echo "Records must be retained for 7 years. Access requires two-factor authentication." > /tmp/global.txt
    echo "Records are retained for 3 years. Access requires a password." > /tmp/sop.txt

    response=$(curl -s -X POST "http://${ip_address}:8888/v1/drift_check" \
        -F "global_doc=@/tmp/global.txt" \
        -F "sop_doc=@/tmp/sop.txt")
    echo "Response: $response"

    if echo "$response" | grep -q '"verdict"'; then
        echo "PASS: backend returned a verdict."
    else
        echo "FAIL: no verdict in response."
        docker logs policydriftcheck-backend-server
        exit 1
    fi
}

function stop_services() {
    cd "$COMPOSE_DIR"
    docker compose down
}

function main() {
    stop_services || true
    build_docker_images
    start_services
    validate_backend
    stop_services
    echo "All tests passed."
}

main
