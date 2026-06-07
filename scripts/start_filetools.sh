#!/bin/bash
# Start the Go filetools gRPC service
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT/go-services/filetools"

# Build if binary doesn't exist
if [ ! -f nanocursor-filetools ]; then
    echo "Building nanocursor-filetools..."
    go build -o nanocursor-filetools ./cmd/nanocursor-filetools
fi

ADDR="${NANOCURSOR_GO_FILETOOLS_ADDR:-${FILETOOLS_GRPC_ADDR:-127.0.0.1:50054}}"
echo "Starting nanocursor-filetools on $ADDR..."
./nanocursor-filetools -addr "$ADDR"
