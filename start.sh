#!/usr/bin/env bash
# ==============================================================================
# Gemini OpenAI Proxy - Launcher Script
#
# Modes:
#   ./start.sh            -> Runs in Foreground (bound to this terminal window;
#                            closing the terminal or pressing Ctrl+C stops the server)
#   ./start.sh --silent   -> Runs detached in Background (independent of terminal;
#   ./start.sh silent        also accepts -s / silent)
# ==============================================================================

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$DIR"

PID_FILE="$DIR/proxy.pid"
LOG_FILE="$DIR/server.log"

SILENT_MODE=0
for arg in "$@"; do
    case "$arg" in
        silent|--silent|-s)
            SILENT_MODE=1
            ;;
        -h|--help|help)
            echo "Uso: ./start.sh [opções]"
            echo ""
            echo "Opções:"
            echo "  (sem argumentos)       Executa vinculado ao terminal atual (se fechar a janela, o servidor encerra junto)"
            echo "  silent, --silent, -s   Executa silenciosamente em segundo plano (independente do terminal)"
            echo "  --help, -h             Exibe esta mensagem de ajuda"
            exit 0
            ;;
    esac
done

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

if [ "$SILENT_MODE" -eq 1 ]; then
    echo -e "[*] Iniciando servidor em segundo plano (\033[1;35mmodo silent\033[0m)..."
    export PROXY_SILENT_MODE=1
    nohup setsid python3 -u server.py --silent >> "$LOG_FILE" 2>&1 < /dev/null &
    SERVER_PID=$!
    echo "$SERVER_PID" > "$PID_FILE"

    sleep 1.2
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo -e "\033[1;31m[ERRO] O servidor falhou ao iniciar. Verifique o log:\033[0m"
        tail -n 15 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi

    echo -e "\033[1;32m[✓] Servidor iniciado em background! (PID: $SERVER_PID)\033[0m"
    echo ""
    echo -e "  🌐 \033[1mDashboard Web:\033[0m      \033[1;36mhttp://${HOST}:${PORT}/\033[0m"
    echo -e "  🔌 \033[1mOpenAI Base URL:\033[0m    \033[1;32mhttp://${HOST}:${PORT}/v1\033[0m"
    echo -e "  📊 \033[1mCotas & Limites:\033[0m    \033[1;33mhttp://${HOST}:${PORT}/account-limits\033[0m"
    echo ""
    echo -e "  📋 Comandos úteis:"
    echo -e "     - Para visualizar logs em tempo real: \033[1;35mtail -f server.log\033[0m"
    echo -e "     - Para encerrar o servidor:           \033[1;31m./stop.sh\033[0m"
    echo -e "\033[1;34m========================================================\033[0m"
else
    echo -e "[*] Modo vinculado ao terminal ativo (feche a janela ou pressione \033[1;33mCtrl+C\033[0m para encerrar)."
    echo -e "    💡 Dica: use \033[1;35m./start.sh --silent\033[0m para rodar em segundo plano."
    echo ""
    echo -e "  🌐 \033[1mDashboard Web:\033[0m      \033[1;36mhttp://${HOST}:${PORT}/\033[0m"
    echo -e "  🔌 \033[1mOpenAI Base URL:\033[0m    \033[1;32mhttp://${HOST}:${PORT}/v1\033[0m"
    echo -e "  📊 \033[1mCotas & Limites:\033[0m    \033[1;33mhttp://${HOST}:${PORT}/account-limits\033[0m"
    echo -e "\033[1;34m========================================================\033[0m"

    export PROXY_SILENT_MODE=0
    export PROXY_TERMINAL_PID="$PPID"
    echo "$$" > "$PID_FILE"
    exec python3 -u server.py
fi
