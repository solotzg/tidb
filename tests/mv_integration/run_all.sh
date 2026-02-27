#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MV_IT_DEPLOY="${MV_IT_DEPLOY:-managed}"

if [[ "${MV_IT_DEPLOY}" == "managed" ]]; then
  "${SCRIPT_DIR}/playground_up.sh"
  trap '"${SCRIPT_DIR}/playground_down.sh"' EXIT
fi

"${SCRIPT_DIR}/run_mv_tests.sh"
