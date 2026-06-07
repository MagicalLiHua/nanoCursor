#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../go-services/eventstore"

ADDR="${EVENTSTORE_GRPC_ADDR:-:50058}"
WORKSPACE="${NANOCURSOR_WORKSPACE_DIR:-}"

if [ ! -f nanocursor-eventstore ]; then
  echo "Building nanocursor-eventstore..."
  go build -o nanocursor-eventstore ./cmd/nanocursor-eventstore
fi

echo "Starting nanocursor-eventstore on $ADDR"
./nanocursor-eventstore --addr="$ADDR" --workspace="$WORKSPACE"
