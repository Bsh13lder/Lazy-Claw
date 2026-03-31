#!/usr/bin/env bash
# start_all.sh — Install and/or start all LazyClaw MCP servers
#
# Usage:
#   ./start_all.sh install    # Install all dependencies
#   ./start_all.sh start      # Start all servers (background, logs to /tmp)
#   ./start_all.sh stop       # Stop all running servers
#   ./start_all.sh status     # Show which servers are running

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# PID file directory
PID_DIR="${SCRIPT_DIR}/.pids"
LOG_DIR="/tmp/lazyclaw-mcps"

# Server definitions: name|runtime|start_command
SERVERS=(
    "mcp-whatsapp|node|node src/index.js"
    "mcp-email|python|python -m mcp_email"
    "mcp-instagram|python|python -m mcp_instagram"
    "mcp-jobspy|python|python -m mcp_jobspy"
    "mcp-lazydoctor|python|python -m mcp_lazydoctor"
)

cmd_install() {
    echo -e "${GREEN}Installing all MCP server dependencies...${NC}"
    echo ""

    for entry in "${SERVERS[@]}"; do
        IFS='|' read -r name runtime _ <<< "$entry"
        dir="${SCRIPT_DIR}/${name}"

        if [[ ! -d "$dir" ]]; then
            echo -e "${YELLOW}  SKIP${NC} ${name} (directory not found)"
            continue
        fi

        echo -e "${GREEN}  Installing${NC} ${name} (${runtime})..."

        if [[ "$runtime" == "node" ]]; then
            (cd "$dir" && npm install --production 2>&1 | tail -1)
        elif [[ "$runtime" == "python" ]]; then
            (cd "$dir" && pip install -e . --quiet 2>&1 | tail -1)
        fi

        echo -e "${GREEN}  Done${NC} ${name}"
    done

    echo ""
    echo -e "${GREEN}All dependencies installed.${NC}"
}

cmd_start() {
    mkdir -p "$PID_DIR" "$LOG_DIR"

    echo -e "${GREEN}Starting all MCP servers...${NC}"
    echo ""

    for entry in "${SERVERS[@]}"; do
        IFS='|' read -r name runtime start_cmd <<< "$entry"
        dir="${SCRIPT_DIR}/${name}"
        pid_file="${PID_DIR}/${name}.pid"
        log_file="${LOG_DIR}/${name}.log"

        if [[ ! -d "$dir" ]]; then
            echo -e "${YELLOW}  SKIP${NC} ${name} (directory not found)"
            continue
        fi

        # Check if already running
        if [[ -f "$pid_file" ]]; then
            old_pid=$(cat "$pid_file")
            if kill -0 "$old_pid" 2>/dev/null; then
                echo -e "${YELLOW}  RUNNING${NC} ${name} (PID ${old_pid})"
                continue
            fi
            rm -f "$pid_file"
        fi

        # Start in background
        (cd "$dir" && $start_cmd > "$log_file" 2>&1) &
        new_pid=$!
        echo "$new_pid" > "$pid_file"
        echo -e "${GREEN}  STARTED${NC} ${name} (PID ${new_pid}, log: ${log_file})"
    done

    echo ""
    echo -e "${GREEN}All servers started. Logs in ${LOG_DIR}/${NC}"
}

cmd_stop() {
    echo -e "${RED}Stopping all MCP servers...${NC}"
    echo ""

    if [[ ! -d "$PID_DIR" ]]; then
        echo "No PID directory found. Nothing to stop."
        return
    fi

    for entry in "${SERVERS[@]}"; do
        IFS='|' read -r name _ _ <<< "$entry"
        pid_file="${PID_DIR}/${name}.pid"

        if [[ ! -f "$pid_file" ]]; then
            echo -e "${YELLOW}  NOT RUNNING${NC} ${name}"
            continue
        fi

        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            echo -e "${RED}  STOPPED${NC} ${name} (PID ${pid})"
        else
            echo -e "${YELLOW}  ALREADY DEAD${NC} ${name} (stale PID ${pid})"
        fi
        rm -f "$pid_file"
    done

    echo ""
    echo -e "${RED}All servers stopped.${NC}"
}

cmd_status() {
    echo -e "MCP Server Status:"
    echo ""

    if [[ ! -d "$PID_DIR" ]]; then
        mkdir -p "$PID_DIR"
    fi

    for entry in "${SERVERS[@]}"; do
        IFS='|' read -r name runtime _ <<< "$entry"
        pid_file="${PID_DIR}/${name}.pid"

        if [[ -f "$pid_file" ]]; then
            pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                echo -e "  ${GREEN}RUNNING${NC}  ${name} (PID ${pid}) [${runtime}]"
            else
                echo -e "  ${RED}DEAD${NC}     ${name} (stale PID ${pid}) [${runtime}]"
                rm -f "$pid_file"
            fi
        else
            echo -e "  ${YELLOW}STOPPED${NC}  ${name} [${runtime}]"
        fi
    done
}

# Main dispatch
case "${1:-help}" in
    install) cmd_install ;;
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    *)
        echo "Usage: $0 {install|start|stop|status}"
        echo ""
        echo "  install  Install all MCP server dependencies"
        echo "  start    Start all MCP servers in background"
        echo "  stop     Stop all running MCP servers"
        echo "  status   Show running/stopped status of each server"
        exit 1
        ;;
esac
