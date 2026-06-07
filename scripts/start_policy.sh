#!/bin/bash
# Start the Go policy engine gRPC service
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT/go-services/policy"

if [ ! -f nanocursor-policy ]; then
    echo "Building nanocursor-policy..."
    go build -o nanocursor-policy ./cmd/nanocursor-policy
fi

ADDR="${POLICY_GRPC_ADDR:-:50052}"
echo "Starting nanocursor-policy on $ADDR..."
./nanocursor-policy --addr="$ADDR"
