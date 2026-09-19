#!/bin/sh
# Worker Zanalyze Engine — cycle complet toutes les N secondes.
set -eu
INTERVAL="${ENGINE_WORKER_INTERVAL:-7200}"
JOURS="${ENGINE_JOURS_SNAPSHOT:-21}"

echo ">>> zanalyze-engine worker (intervalle=${INTERVAL}s)"
run() {
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) refresh ==="
  python -m engine refresh --jours "$JOURS" || echo "WARN refresh échoué"
}

run
while true; do
  echo ">>> pause ${INTERVAL}s"
  sleep "$INTERVAL"
  run
done
