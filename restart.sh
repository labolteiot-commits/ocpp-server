#!/bin/bash
TMUX_SESSION="ocpp"
WORK_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${WORK_DIR}/.venv/bin/python"
PID_FILE="/tmp/ocpp-server.pid"
LOG_FILE="${WORK_DIR}/logs/server.log"

ok()   { echo "[OK]  $*"; }
warn() { echo "[WARN] $*"; }
err()  { echo "[ERR]  $*"; }

HAS_TMUX=false
command -v tmux >/dev/null 2>&1 && HAS_TMUX=true

is_tmux_alive() { $HAS_TMUX && tmux has-session -t "$TMUX_SESSION" 2>/dev/null; }

server_pid() { [ -f "$PID_FILE" ] && cat "$PID_FILE" 2>/dev/null; }

is_server_running() {
    pid=$(server_pid)
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_server() {
    if is_server_running; then
        echo "Arret du serveur OCPP..."
        "$PYTHON" "$WORK_DIR/main.py" --stop
    else
        warn "Aucune instance en cours."
        rm -f "$PID_FILE"
    fi
    if is_tmux_alive; then
        tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
        ok "Session tmux fermee."
    fi
}

start_server() {
    if is_server_running; then
        warn "Le serveur tourne deja (PID $(server_pid))."
        return 1
    fi
    mkdir -p "$(dirname "$LOG_FILE")"
    if $HAS_TMUX; then
        if ! is_tmux_alive; then
            tmux new-session -d -s "$TMUX_SESSION" -c "$WORK_DIR"                 "source .venv/bin/activate && python main.py; echo Serveur arrete; read"
        fi
        ok "Serveur demarre dans tmux."
        echo "  Logs     : ./restart.sh logs"
    else
        nohup "$PYTHON" "$WORK_DIR/main.py" >> "$LOG_FILE" 2>&1 &
        for i in 1 2 3 4 5 6 7 8 9 10; do
            sleep 0.5
            if is_server_running; then
                ok "Serveur demarre (PID $(server_pid))."
                echo "  Logs     : tail -f $LOG_FILE"
                echo "  Dashboard: http://$(hostname -I | awk "{print \$1}"):8000"
                return 0
            fi
        done
        err "Le serveur na pas demarre. Voir logs:"
        tail -20 "$LOG_FILE"
        return 1
    fi
    echo "  Dashboard: http://$(hostname -I | awk "{print \$1}"):8000"
}

show_status() {
    echo ""
    echo "-- Serveur OCPP --"
    if is_server_running; then
        ok "En cours - PID $(server_pid)"
    else
        err "Arrete"
    fi
    if $HAS_TMUX; then
        echo "-- Session tmux --"
        if is_tmux_alive; then ok "Session active"; else warn "Aucune session"; fi
    else
        echo "-- Logs nohup --"
        [ -f "$LOG_FILE" ] && tail -3 "$LOG_FILE" || warn "Pas de fichier log"
    fi
    echo ""
}

case "${1:-restart}" in
    stop)    stop_server ;;
    start)   start_server ;;
    restart) stop_server; sleep 1; start_server ;;
    status)  show_status ;;
    logs)
        if $HAS_TMUX && is_tmux_alive; then
            echo "Ctrl+B D pour detacher."
            sleep 1; tmux attach -t "$TMUX_SESSION"
        elif [ -f "$LOG_FILE" ]; then
            tail -f "$LOG_FILE"
        else
            err "Aucune session ni log trouve."; fi ;;
    *)       echo "Usage: $0 {restart|start|stop|status|logs}"; exit 1 ;;
esac
