#!/bin/bash
# Start the Go indexer gRPC service
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT/go-indexer"

# Build if binary doesn't exist
if [ ! -f nanocursor-indexer ]; then
    echo "Building nanocursor-indexer..."
    go build -o nanocursor-indexer ./cmd/nanocursor-indexer
fi

ADDR="${INDEXER_GRPC_ADDR:-:50051}"
echo "Starting nanocursor-indexer on $ADDR..."
./nanocursor-indexer --addr="$ADDR"
