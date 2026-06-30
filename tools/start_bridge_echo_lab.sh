#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON_BIN="${BRIDGE_ECHO_PYTHON:-${REPO_DIR}/venv/bin/python3}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="${BRIDGE_ECHO_PYTHON:-python3}"
fi

SERVER="${BRIDGE_ECHO_SERVER:-bridge.gwnl.info}"
PORT="${BRIDGE_ECHO_PORT:-4200}"
BRIDGE_PASSWORD="${BRIDGE_ECHO_BRIDGE_PASSWORD:-}"
NAME="${BRIDGE_ECHO_NAME:-Echo Lab}"
PASSWORD="${BRIDGE_ECHO_PASSWORD:-password}"
ADMIN_PASSWORD="${BRIDGE_ECHO_ADMIN_PASSWORD:-${PASSWORD}}"
STATE="${BRIDGE_ECHO_STATE:-${REPO_DIR}/bridge_echo_lab_state.json}"
SCOPE="${BRIDGE_ECHO_SCOPE:-}"
ADVERT_INTERVAL="${BRIDGE_ECHO_ADVERT_INTERVAL:-180}"
PATH_HASH_SIZE="${BRIDGE_ECHO_PATH_HASH_SIZE:-1}"
DEFAULT_DELAY_MS="${BRIDGE_ECHO_DEFAULT_DELAY_MS:-1000}"
MAX_DELAY_MS="${BRIDGE_ECHO_MAX_DELAY_MS:-30000}"
LOSS_SPACING_MS="${BRIDGE_ECHO_LOSS_SPACING_MS:-700}"
RECONNECT_DELAY="${BRIDGE_ECHO_RECONNECT_DELAY:-5}"
VERBOSE="${BRIDGE_ECHO_VERBOSE:-1}"

mkdir -p "$(dirname "${STATE}")"

args=(
  "${SCRIPT_DIR}/bridge_echo_lab.py"
  --server "${SERVER}"
  --port "${PORT}"
  --name "${NAME}"
  --password "${PASSWORD}"
  --admin-password "${ADMIN_PASSWORD}"
  --state "${STATE}"
  --advert-interval "${ADVERT_INTERVAL}"
  --path-hash-size "${PATH_HASH_SIZE}"
  --default-delay-ms "${DEFAULT_DELAY_MS}"
  --max-delay-ms "${MAX_DELAY_MS}"
  --loss-spacing-ms "${LOSS_SPACING_MS}"
  --reconnect-delay "${RECONNECT_DELAY}"
)

if [[ -n "${BRIDGE_PASSWORD}" ]]; then
  args+=(--bridge-password "${BRIDGE_PASSWORD}")
fi

if [[ -n "${SCOPE}" ]]; then
  args+=(--scope "${SCOPE}")
fi

if [[ "${VERBOSE}" == "1" || "${VERBOSE}" == "true" || "${VERBOSE}" == "on" ]]; then
  args+=(--verbose)
fi

echo "Starting Bridge Echo Lab"
echo "  server: ${SERVER}:${PORT}"
echo "  name:   ${NAME}"
echo "  state:  ${STATE}"
exec "${PYTHON_BIN}" "${args[@]}"
