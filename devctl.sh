#!/usr/bin/env zsh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_DIR="$ROOT_DIR/.run"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
BACKEND_LOG_FILE="$RUN_DIR/backend.log"
FRONTEND_LOG_FILE="$RUN_DIR/frontend.log"

mkdir -p "$RUN_DIR"

wait_for_pid() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"

  for _ in {1..25}; do
    if is_running "$pid_file"; then
      return 0
    fi
    sleep 0.2
  done

  echo "$name failed to start"
  if [[ -f "$log_file" ]]; then
    echo "--- $name log (tail) ---"
    tail -n 60 "$log_file" || true
    echo "-------------------------"
  fi
  return 1
}

wait_for_frontend_ready() {
  local url="http://127.0.0.1:5173"
  if ! command -v curl >/dev/null 2>&1; then
    return 0
  fi

  for _ in {1..30}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.2
  done

  echo "frontend process is alive but not ready at $url"
  if [[ -f "$FRONTEND_LOG_FILE" ]]; then
    echo "--- frontend log (tail) ---"
    tail -n 60 "$FRONTEND_LOG_FILE" || true
    echo "---------------------------"
  fi
  return 1
}

is_running() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi

  local pid
  pid="$(cat "$pid_file")"
  if [[ -z "$pid" ]]; then
    return 1
  fi

  if kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi

  rm -f "$pid_file"
  return 1
}

start_backend() {
  if is_running "$BACKEND_PID_FILE"; then
    echo "backend already running (pid=$(cat "$BACKEND_PID_FILE"))"
    return
  fi

  echo "starting backend..."
  local python_bin="python3"
  if [[ -x "$BACKEND_DIR/venv/bin/python" ]]; then
    python_bin="$BACKEND_DIR/venv/bin/python"
  fi

  (
    cd "$BACKEND_DIR"
    nohup "$python_bin" -m uvicorn main:app --host 0.0.0.0 --port 8000 >"$BACKEND_LOG_FILE" 2>&1 &
    echo $! >"$BACKEND_PID_FILE"
  )
  wait_for_pid "backend" "$BACKEND_PID_FILE" "$BACKEND_LOG_FILE"
  echo "backend started (pid=$(cat "$BACKEND_PID_FILE"))"
}

start_frontend() {
  if is_running "$FRONTEND_PID_FILE"; then
    echo "frontend already running (pid=$(cat "$FRONTEND_PID_FILE"))"
    return
  fi

  echo "starting frontend..."
  (
    cd "$FRONTEND_DIR"
    nohup npm run dev -- --port 5173 >"$FRONTEND_LOG_FILE" 2>&1 &
    echo $! >"$FRONTEND_PID_FILE"
  )
  wait_for_pid "frontend" "$FRONTEND_PID_FILE" "$FRONTEND_LOG_FILE"
  wait_for_frontend_ready
  echo "frontend started (pid=$(cat "$FRONTEND_PID_FILE"))"
}

stop_one() {
  local name="$1"
  local pid_file="$2"

  if ! is_running "$pid_file"; then
    echo "$name is not running"
    return
  fi

  local pid
  pid="$(cat "$pid_file")"
  echo "stopping $name (pid=$pid)..."
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      sleep 0.2
    else
      break
    fi
  done

  if kill -0 "$pid" >/dev/null 2>&1; then
    echo "force killing $name (pid=$pid)..."
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi

  rm -f "$pid_file"
  echo "$name stopped"
}

start_all() {
  start_backend
  start_frontend
  echo "all services started"
  echo "backend log: $BACKEND_LOG_FILE"
  echo "frontend log: $FRONTEND_LOG_FILE"
}

stop_all() {
  stop_one "frontend" "$FRONTEND_PID_FILE"
  stop_one "backend" "$BACKEND_PID_FILE"
  echo "all services stopped"
}

status_all() {
  if is_running "$BACKEND_PID_FILE"; then
    echo "backend: running (pid=$(cat "$BACKEND_PID_FILE"))"
  else
    echo "backend: stopped"
  fi

  if is_running "$FRONTEND_PID_FILE"; then
    echo "frontend: running (pid=$(cat "$FRONTEND_PID_FILE"))"
  else
    echo "frontend: stopped"
  fi
}

restart_all() {
  stop_all
  start_all
}

usage() {
  echo "Usage: ./devctl.sh {start|stop|restart|status|logs|build|release}"
  echo "  build           Build frontend into backend/static"
  echo "  release TAG     Tag, push, and trigger GitHub release"
}

show_logs() {
  local target="${1:-all}"
  local follow="${2:-}"
  local tail_args=("-n" "120")

  if [[ "$follow" == "-f" ]]; then
    tail_args=("-f")
  fi

  case "$target" in
    backend)
      if [[ ! -f "$BACKEND_LOG_FILE" ]]; then
        echo "backend log not found: $BACKEND_LOG_FILE"
        return 1
      fi
      tail "${tail_args[@]}" "$BACKEND_LOG_FILE"
      ;;
    frontend)
      if [[ ! -f "$FRONTEND_LOG_FILE" ]]; then
        echo "frontend log not found: $FRONTEND_LOG_FILE"
        return 1
      fi
      tail "${tail_args[@]}" "$FRONTEND_LOG_FILE"
      ;;
    all)
      if [[ ! -f "$BACKEND_LOG_FILE" && ! -f "$FRONTEND_LOG_FILE" ]]; then
        echo "no logs found under $RUN_DIR"
        return 1
      fi

      if [[ -f "$BACKEND_LOG_FILE" ]]; then
        echo "===== backend ($BACKEND_LOG_FILE) ====="
        tail -n 60 "$BACKEND_LOG_FILE" || true
      fi
      if [[ -f "$FRONTEND_LOG_FILE" ]]; then
        echo "===== frontend ($FRONTEND_LOG_FILE) ====="
        tail -n 60 "$FRONTEND_LOG_FILE" || true
      fi

      if [[ "$follow" == "-f" ]]; then
        local files=()
        [[ -f "$BACKEND_LOG_FILE" ]] && files+=("$BACKEND_LOG_FILE")
        [[ -f "$FRONTEND_LOG_FILE" ]] && files+=("$FRONTEND_LOG_FILE")
        tail -f "${files[@]}"
      fi
      ;;
    *)
      echo "unknown logs target: $target"
      usage
      return 1
      ;;
  esac
}

command="${1:-status}"

case "$command" in
  start)
    start_all
    ;;
  stop)
    stop_all
    ;;
  restart)
    restart_all
    ;;
  status)
    status_all
    ;;
  logs)
    show_logs "${2:-all}" "${3:-}"
    ;;
  build)
    echo "── Building frontend into backend/static ──"
    cd "$FRONTEND_DIR" && npm run build
    echo "✅ Frontend built → backend/static/"
    ;;
  release)
    tag="${2:-}"
    if [[ -z "$tag" ]]; then
      echo "Usage: ./devctl.sh release vX.Y.Z"
      exit 1
    fi
    if [[ -n $(git status --porcelain) ]]; then
      echo "❌ Working tree is dirty. Commit or stash changes before releasing."
      exit 1
    fi
    if git tag -l "$tag" | grep -q .; then
      echo "❌ Tag '$tag' already exists locally. Delete it first: git tag -d $tag"
      exit 1
    fi
    echo "── Tagging $tag and pushing ──"
    git tag "$tag"
    git push origin main
    git push origin "$tag"
    echo "✅ Tag $tag pushed — GitHub Actions will create the release."
    ;;
  *)
    usage
    exit 1
    ;;
esac
