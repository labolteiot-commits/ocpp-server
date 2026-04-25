#!/usr/bin/env bash
# Orchestre l'exécution des tests OCPP.
#
# Usage :
#   ./run_all.sh                  # couche 1 uniquement (sim_only, safe)
#   ./run_all.sh "sim_only"       # idem
#   ./run_all.sh "not requires_vehicle"   # sim + live sans EV
#   ./run_all.sh "requires_vehicle"       # EV physique requis
set -euo pipefail

cd "$(dirname "$0")/.."
SERVER_ROOT="$(pwd)"
TESTS_ROOT="${SERVER_ROOT}/tests"
REPORTS_DIR="${SERVER_ROOT}/logs/test_reports"
mkdir -p "${REPORTS_DIR}"

MARKER="${1:-sim_only}"
shift || true   # retirer le marker pour que "$@" ne contienne que les extra args
STAMP=$(date +%F_%H%M%S)
JUNIT="${REPORTS_DIR}/ocpp-${STAMP}.xml"
HTML="${REPORTS_DIR}/ocpp-${STAMP}.html"

echo "▶ Tests OCPP — marker='${MARKER}'"
echo "▶ Reports → ${REPORTS_DIR}/ocpp-${STAMP}.*"

# shellcheck source=/dev/null
source "${SERVER_ROOT}/.venv/bin/activate"

cd "${SERVER_ROOT}"
python -m pytest "${TESTS_ROOT}" \
  -v --tb=short \
  -m "${MARKER}" \
  --junit-xml="${JUNIT}" \
  --html="${HTML}" --self-contained-html \
  "$@"

echo "✓ Terminé. Rapports : ${REPORTS_DIR}"
