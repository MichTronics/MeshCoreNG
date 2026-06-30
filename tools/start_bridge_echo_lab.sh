#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

BRIDGE_ECHO_SERVER="${BRIDGE_ECHO_SERVER:-bridge.gwnl.info}"
BRIDGE_ECHO_PORT="${BRIDGE_ECHO_PORT:-4200}"
BRIDGE_ECHO_NAME="${BRIDGE_ECHO_NAME:-Echo Lab}"
BRIDGE_ECHO_PASSWORD="${BRIDGE_ECHO_PASSWORD:-secret}"
BRIDGE_ECHO_ADMIN_PASSWORD="${BRIDGE_ECHO_ADMIN_PASSWORD:-${BRIDGE_ECHO_PASSWORD}}"
BRIDGE_ECHO_STATE="${BRIDGE_ECHO_STATE:-${REPO_DIR}/bridge_echo_lab_state.json}"
BRIDGE_ECHO_VERBOSE="${BRIDGE_ECHO_VERBOSE:-1}"

args=(
  "${REPO_DIR}/tools/bridge_echo_lab.py"
  --server "${BRIDGE_ECHO_SERVER}"
  --port "${BRIDGE_ECHO_PORT}"
  --name "${BRIDGE_ECHO_NAME}"
  --password "${BRIDGE_ECHO_PASSWORD}"
  --admin-password "${BRIDGE_ECHO_ADMIN_PASSWORD}"
  --state "${BRIDGE_ECHO_STATE}"
)

if [[ -n "${BRIDGE_ECHO_BRIDGE_PASSWORD:-}" ]]; then
  args+=(--bridge-password "${BRIDGE_ECHO_BRIDGE_PASSWORD}")
fi

if [[ "${BRIDGE_ECHO_VERBOSE}" != "0" ]]; then
  args+=(--verbose)
fi

cd "${REPO_DIR}"
exec python3 "${args[@]}"