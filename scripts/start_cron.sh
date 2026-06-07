#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../go-services/cron"

ADDR="${CRON_GRPC_ADDR:-:50057}"

if [ ! -f nanocursor-cron ]; then
  echo "Building nanocursor-cron..."
  go build -o nanocursor-cron ./cmd/nanocursor-cron
fi

echo "Starting nanocursor-cron on $ADDR"
./nanocursor-cron --addr="$ADDR"
