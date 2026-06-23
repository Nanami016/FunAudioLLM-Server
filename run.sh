#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
# SenseVoice ASR Server - Background startup script
# ──────────────────────────────────────────────────────────────────────

set -euo pipefail

# ─── Paths ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$SCRIPT_DIR/.asr_server.pid"
HOST="0.0.0.0"
PORT=9000
PYTHON="$VENV_DIR/bin/python"

mkdir -p "$LOG_DIR"

# ─── Color helpers ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'  # No Color

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ─── Check virtual environment ──────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    error "Virtual environment not found at $VENV_DIR"
    info "Create it with: python3 -m venv $VENV_DIR && $PYTHON -m pip install -r requirements.txt"
    exit 1
fi

if [ ! -f "$PYTHON" ]; then
    error "Python not found in venv: $PYTHON"
    exit 1
fi

# ─── Check ffmpeg ───────────────────────────────────────────────────
if ! command -v ffmpeg &>/dev/null; then
    error "ffmpeg not found. Install with: brew install ffmpeg"
    exit 1
fi

# ─── Parse arguments ────────────────────────────────────────────────
case "${1:-}" in
    start)
        ;;
    stop)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                info "Stopping ASR server (PID: $PID)..."
                kill "$PID"
                rm -f "$PID_FILE"
                info "Server stopped."
            else
                warn "Process $PID not running. Cleaning up PID file."
                rm -f "$PID_FILE"
            fi
        else
            warn "No PID file found. Server may not be running."
        fi
        exit 0
        ;;
    restart)
        "$0" stop
        sleep 1
        exec "$0" start
        ;;
    status)
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            if kill -0 "$PID" 2>/dev/null; then
                info "ASR server is running (PID: $PID)"
                info "Listening on http://$HOST:$PORT"
                info "Log file: $LOG_DIR/asr_server_$(date +%Y%m%d).log"
            else
                warn "PID file exists but process $PID is not running."
                rm -f "$PID_FILE"
            fi
        else
            warn "ASR server is not running (no PID file)."
        fi
        exit 0
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo ""
        echo "Commands:"
        echo "  start    Start ASR server in background"
        echo "  stop     Stop running ASR server"
        echo "  restart  Restart ASR server"
        echo "  status   Check if server is running"
        exit 1
        ;;
esac

# ─── Check if already running ───────────────────────────────────────
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        warn "ASR server is already running (PID: $PID)"
        info "Use '$0 restart' to restart, or '$0 stop' to stop."
        exit 1
    else
        rm -f "$PID_FILE"
    fi
fi

# ─── Start server ───────────────────────────────────────────────────
LOG_FILE="$LOG_DIR/asr_server_$(date +%Y%m%d).log"
info "Starting SenseVoice ASR Server..."
info "  Host: $HOST"
info "  Port: $PORT"
info "  Log:  $LOG_FILE"

nohup "$PYTHON" "$SCRIPT_DIR/asr_server.py" \
    --host "$HOST" \
    --port "$PORT" \
    >> "$LOG_FILE" 2>&1 &

SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# Wait briefly to verify it started
sleep 2
if kill -0 "$SERVER_PID" 2>/dev/null; then
    info "✅ ASR server started successfully! (PID: $SERVER_PID)"
    info "   API: http://$HOST:$PORT/asr"
    info "   Health: http://$HOST:$PORT/health"
    info "   Docs: http://$HOST:$PORT/docs"
else
    error "Server failed to start. Check log: $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi
