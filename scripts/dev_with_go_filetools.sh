#!/usr/bin/env bash
# Start nanoCursor backend with the optional Go filetools sidecar enabled.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
FILETOOLS_ADDR="${NANOCURSOR_GO_FILETOOLS_ADDR:-127.0.0.1:50054}"

if ! command -v go >/dev/null 2>&1; then
  echo "Go is required to start the filetools sidecar." >&2
  exit 1
fi

cleanup() {
  if [ -n "${FILETOOLS_PID:-}" ] && kill -0 "$FILETOOLS_PID" >/dev/null 2>&1; then
    kill "$FILETOOLS_PID" >/dev/null 2>&1 || true
    wait "$FILETOOLS_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[go-filetools] Starting on ${FILETOOLS_ADDR} ..."
(
  cd "$PROJECT_ROOT/go-services/filetools"
  go run ./cmd/nanocursor-filetools -addr "$FILETOOLS_ADDR"
) &
FILETOOLS_PID=$!

export NANOCURSOR_GO_FILETOOLS_ENABLED=true
export NANOCURSOR_GO_FILETOOLS_FALLBACK="${NANOCURSOR_GO_FILETOOLS_FALLBACK:-true}"
export NANOCURSOR_GO_FILETOOLS_ADDR="$FILETOOLS_ADDR"
export FILETOOLS_GRPC_ADDR="$FILETOOLS_ADDR"

sleep 0.8

echo "[backend] Starting with Go filetools enabled ..."
cd "$PROJECT_ROOT"
python scripts/dev_backend.py

