#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../go-services/mcp"

ADDR="${MCP_GRPC_ADDR:-:50056}"

if [ ! -f nanocursor-mcp ]; then
  echo "Building nanocursor-mcp..."
  go build -o nanocursor-mcp ./cmd/nanocursor-mcp
fi

echo "Starting nanocursor-mcp on $ADDR"
./nanocursor-mcp --addr="$ADDR"
