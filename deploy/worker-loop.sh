#!/bin/sh
# Worker Zanalyze Engine — sync SofaScore → analyses → snapshot JSON
set -eu
INTERVAL="${ENGINE_WORKER_INTERVAL:-7200}"
PAGES="${ENGINE_SYNC_PAGES:-1}"
PASSES="${ENGINE_SYNC_PASSES:-1}"

echo ">>> zanalyze-engine worker (interval=${INTERVAL}s)"
run() {
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) refresh ==="
  python -m engine refresh --pages "$PAGES" --passes "$PASSES" \
    || echo "WARN refresh échoué"
}

run
while true; do
  echo ">>> sleep ${INTERVAL}s"
  sleep "$INTERVAL"
  run
done
