#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT/go-services/taskboard"
if [ ! -f nanocursor-taskboard ]; then
    echo "Building nanocursor-taskboard..."
    go build -o nanocursor-taskboard ./cmd/nanocursor-taskboard
fi
ADDR="${TASKBOARD_GRPC_ADDR:-:50053}"
echo "Starting nanocursor-taskboard on $ADDR..."
./nanocursor-taskboard --addr="$ADDR"
