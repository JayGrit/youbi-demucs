#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$ROOT/.venv/bin/python"
SERVICE_PID=""
UPDATE_INTERVAL_SECONDS="${UPDATE_INTERVAL_SECONDS:-60}"

cd "$ROOT" || exit 1

export DEVICE="${DEVICE:-Macmini M2}"

find_python() {
  if command -v python3.12 >/dev/null 2>&1; then
    command -v python3.12
    return 0
  fi

  if command -v python3 >/dev/null 2>&1; then
    python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
    if [ "$?" -eq 0 ]; then
      command -v python3
      return 0
    fi
  fi

  return 1
}

install_dependencies() {
  echo "Installing Python dependencies..."
  "$PYTHON" -m pip install -U pip || return 1
  "$PYTHON" -m pip install -e . || return 1
}

ensure_venv() {
  if [ -x "$PYTHON" ]; then
    return 0
  fi

  local python_cmd
  python_cmd="$(find_python)" || {
    echo "Python 3.12+ was not found. Install Python, then rerun start.sh." >&2
    return 1
  }

  echo "Creating Python virtual environment with $python_cmd..."
  "$python_cmd" -m venv "$ROOT/.venv"
}

start_service() {
  echo "Starting ydbi-demucs..."
  "$PYTHON" -m ydbi_demucs.main &
  SERVICE_PID="$!"
  echo "Started ydbi-demucs process $SERVICE_PID"
}

stop_service() {
  if [ -z "${SERVICE_PID:-}" ] || ! kill -0 "$SERVICE_PID" >/dev/null 2>&1; then
    return 0
  fi

  echo "Stopping ydbi-demucs process $SERVICE_PID..."
  kill "$SERVICE_PID" >/dev/null 2>&1 || true

  for _ in $(seq 1 30); do
    if ! kill -0 "$SERVICE_PID" >/dev/null 2>&1; then
      wait "$SERVICE_PID" >/dev/null 2>&1 || true
      return 0
    fi
    sleep 1
  done

  echo "Process $SERVICE_PID did not stop in time; killing it."
  kill -9 "$SERVICE_PID" >/dev/null 2>&1 || true
  wait "$SERVICE_PID" >/dev/null 2>&1 || true
}

git_head() {
  git -C "$ROOT" rev-parse HEAD 2>/dev/null || true
}

update_repository() {
  local before
  local after

  before="$(git_head)"
  if [ -z "$before" ]; then
    echo "Not a git repository or git is unavailable; skipping auto update." >&2
    return 1
  fi

  echo "Checking for updates..."
  if ! git -C "$ROOT" pull --ff-only; then
    echo "git pull failed; keeping current process running." >&2
    return 1
  fi

  after="$(git_head)"
  [ -n "$after" ] && [ "$after" != "$before" ]
}

cleanup() {
  stop_service
}
trap cleanup EXIT INT TERM

ensure_venv || exit 1
install_dependencies || exit 1

echo "Operator: $DEVICE"
echo "Root: $ROOT"
echo "Auto update: git pull every $UPDATE_INTERVAL_SECONDS seconds"
echo

start_service

while true; do
  sleep "$UPDATE_INTERVAL_SECONDS"

  if ! kill -0 "$SERVICE_PID" >/dev/null 2>&1; then
    wait "$SERVICE_PID" >/dev/null 2>&1 || true
    echo "ydbi-demucs exited; restarting."
    start_service
    continue
  fi

  if update_repository; then
    echo "Repository updated; restarting ydbi-demucs."
    stop_service
    install_dependencies || exit 1
    start_service
  fi
done
