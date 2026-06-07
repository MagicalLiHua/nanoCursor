#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT/go-services/executor"

ADDR="${EXECUTOR_GRPC_ADDR:-:50055}"

if [ ! -f nanocursor-executor ]; then
  echo "Building nanocursor-executor..."
  go build -o nanocursor-executor ./cmd/nanocursor-executor
fi

echo "Starting nanocursor-executor on $ADDR"
./nanocursor-executor --addr="$ADDR"
