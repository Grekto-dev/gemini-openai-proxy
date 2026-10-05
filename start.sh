#!/usr/bin/env bash
# ==============================================================================
# Gemini OpenAI Proxy - Launcher Script
# Starts the background proxy server and web dashboard
# ==============================================================================

set -e

# Change directory to the script's location
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

PID_FILE="$DIR/proxy.pid"
LOG_FILE="$DIR/server.log"

# Check if already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo -e "\033[1;33m[!] O servidor proxy já está rodando (PID: $PID).\033[0m"
        echo -e "    Dashboard: \033[1;36mhttp://127.0.0.1:8000/\033[0m"
        echo -e "    Para parar: \033[1;31m./stop.sh\033[0m"
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# Verify Python 3
if ! command -v python3 &>/dev/null; then
    echo -e "\033[1;31m[ERRO] Python 3 não foi encontrado no sistema.\033[0m"
    exit 1
fi

# Verify python dependencies
python3 -c "import requests, dbus" 2>/dev/null || {
    echo -e "\033[1;33m[!] Instalando/verificando dependências necessárias (requests, dbus-python)...\033[0m"
    pip3 install requests --quiet 2>/dev/null || true
}

# Read host and port from config.json if available
HOST=$(python3 -c "import config; print(config.load_config().get('host', '127.0.0.1'))" 2>/dev/null || echo "127.0.0.1")
PORT=$(python3 -c "import config; print(config.load_config().get('port', 8000))" 2>/dev/null || echo "8000")

echo -e "\033[1;34m========================================================\033[0m"
echo -e "\033[1;36m       Gemini Google AI Pro -> OpenAI Proxy Server      \033[0m"
echo -e "\033[1;34m========================================================\033[0m"
echo -e "[*] Iniciando servidor em background..."

# Run server in background fully detached with setsid
setsid python3 -u server.py >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# Wait a moment to check if process stays alive
sleep 1.2
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo -e "\033[1;31m[ERRO] O servidor falhou ao iniciar. Verifique o log:\033[0m"
    tail -n 15 "$LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
fi

echo -e "\033[1;32m[✓] Servidor iniciado com sucesso! (PID: $SERVER_PID)\033[0m"
echo ""
echo -e "  🌐 \033[1mDashboard Web:\033[0m      \033[1;36mhttp://${HOST}:${PORT}/\033[0m"
echo -e "  🔌 \033[1mOpenAI Base URL:\033[0m    \033[1;32mhttp://${HOST}:${PORT}/v1\033[0m"
echo -e "  📊 \033[1mCotas & Status:\033[0m     \033[1;33mhttp://${HOST}:${PORT}/v1/quota\033[0m"
echo ""
echo -e "  📋 Comandos úteis:"
echo -e "     - Para visualizar logs em tempo real: \033[1;35mtail -f server.log\033[0m"
echo -e "     - Para encerrar o servidor:           \033[1;31m./stop.sh\033[0m"
echo -e "\033[1;34m========================================================\033[0m"
