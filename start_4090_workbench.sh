#!/usr/bin/env bash
# One-click launcher for the RTX 4090 host (COBOT MAGIC Workbench).
# Starts:
#   1. Legacy lab_stack backend (127.0.0.1:8873) when its local config is present
#   2. Control Center & One-Click Connect service (127.0.0.1:8910)
#   3. Default desktop browser at http://127.0.0.1:8910 (skipped on headless SSH)
#
# Usage:
#   bash start_4090_workbench.sh               # Full startup (backend + control center + browser)
#   bash start_4090_workbench.sh --ui-only     # Start control center UI only
#   bash start_4090_workbench.sh --no-browser  # Start services without opening browser
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Works whether placed in repo root or inside tools/
if [[ -f "${SCRIPT_DIR}/server/main.py" ]]; then
  REPO_ROOT="${SCRIPT_DIR}"
elif [[ -f "${SCRIPT_DIR}/../server/main.py" ]]; then
  REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
else
  echo "Error: cannot locate server/main.py. Place this script in the repository root or tools/." >&2
  exit 1
fi

LEGACY_PORT="${LEGACY_PORT:-8873}"
FACADE_PORT="${FACADE_PORT:-8910}"
UI_ONLY=0
NO_BROWSER=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --ui-only) UI_ONLY=1; shift ;;
    --no-browser) NO_BROWSER=1; shift ;;
    --legacy-port) LEGACY_PORT="$2"; shift 2 ;;
    --port) FACADE_PORT="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash $0 [--ui-only] [--no-browser] [--port 8910] [--legacy-port 8873]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "Error: python3 not found in PATH." >&2
  exit 1
fi

PIDS=()
cleanup() {
  echo ""
  echo "[STOP] Shutting down COBOT MAGIC Workbench services..."
  for pid in "${PIDS[@]:-}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
  echo "[STOP] All services stopped cleanly."
}
trap cleanup INT TERM EXIT

port_listening() {
  "${PYTHON_BIN}" - "$1" << 'PY'
import socket, sys
s = socket.socket()
s.settimeout(0.3)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
}

cd "${REPO_ROOT}"
echo "=== COBOT MAGIC 4090 Workbench Launcher ==="
echo "Repo root: ${REPO_ROOT}"

# 1. Start legacy lab_stack backend (unless --ui-only or already running)
LEGACY_SCRIPT="${REPO_ROOT}/lab_stack/2026-10-06-15-57-web.py"
LEGACY_CFG="${REPO_ROOT}/lab_stack/2026-10-06-14-05-config.json"

if [[ "${UI_ONLY}" -eq 0 ]]; then
  if port_listening "${LEGACY_PORT}"; then
    echo "[1/3] Legacy backend already listening on 127.0.0.1:${LEGACY_PORT} (reusing)."
  elif [[ -f "${LEGACY_SCRIPT}" && -f "${LEGACY_CFG}" ]]; then
    echo "[1/3] Starting legacy lab_stack backend on 127.0.0.1:${LEGACY_PORT}..."
    "${PYTHON_BIN}" -B "${LEGACY_SCRIPT}" --port "${LEGACY_PORT}" --no-browser &
    PIDS+=("$!")
    sleep 1
  else
    echo "[1/3] Note: ${LEGACY_CFG} not found; starting Control Center in standalone/offline mode."
  fi
else
  echo "[1/3] --ui-only set; skipping legacy lab_stack backend."
fi

# 2. Start Control Center facade (server.main)
if port_listening "${FACADE_PORT}"; then
  echo "[2/3] Control Center already listening on 127.0.0.1:${FACADE_PORT}."
else
  echo "[2/3] Starting Control Center on 127.0.0.1:${FACADE_PORT} -> legacy 127.0.0.1:${LEGACY_PORT}..."
  "${PYTHON_BIN}" -B -m server.main --port "${FACADE_PORT}" --legacy-origin "http://127.0.0.1:${LEGACY_PORT}" &
  PIDS+=("$!")
fi

# Wait up to 5 seconds for Control Center health port
for _ in $(seq 1 20); do
  if port_listening "${FACADE_PORT}"; then
    break
  fi
  sleep 0.25
done

URL="http://127.0.0.1:${FACADE_PORT}"
echo "[3/3] Control Center is ready at: ${URL}"

# 3. Auto-open browser if desktop display is available
if [[ "${NO_BROWSER}" -eq 0 && ( -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ) ]]; then
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "${URL}" >/dev/null 2>&1 &
  fi
fi

echo "Press Ctrl + C to stop all started services."
if [[ "${#PIDS[@]}" -gt 0 ]]; then
  wait
fi
