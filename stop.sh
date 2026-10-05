#!/usr/bin/env bash
# ==============================================================================
# Gemini OpenAI Proxy - Shutdown Script
# Gracefully terminates the running proxy server
# ==============================================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PID_FILE="$DIR/proxy.pid"

if [ ! -f "$PID_FILE" ]; then
    echo -e "\033[1;33m[!] Nenhum arquivo proxy.pid encontrado. Verificando processos...\033[0m"
    PIDS=$(pgrep -f "python3 server.py" || true)
    if [ -n "$PIDS" ]; then
        echo -e "[*] Finalizando processos encontrados: $PIDS"
        kill -15 $PIDS 2>/dev/null || true
        echo -e "\033[1;32m[✓] Processos encerrados.\033[0m"
    else
        echo -e "\033[1;32m[✓] Nenhum servidor proxy em execução.\033[0m"
    fi
    exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || echo "")

if [ -z "$PID" ]; then
    rm -f "$PID_FILE"
    echo -e "\033[1;32m[✓] Arquivo PID vazio removido. Nenhum servidor ativo.\033[0m"
    exit 0
fi

if kill -0 "$PID" 2>/dev/null; then
    echo -e "[*] Parando servidor proxy (PID: $PID)..."
    kill -15 "$PID" 2>/dev/null || true
    
    # Wait up to 5 seconds for clean exit
    for i in {1..5}; do
        if ! kill -0 "$PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    # Force kill if still active
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "[!] Forçando encerramento (SIGKILL)..."
        kill -9 "$PID" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    echo -e "\033[1;32m[✓] Servidor parado com sucesso.\033[0m"
else
    rm -f "$PID_FILE"
    echo -e "\033[1;33m[!] O processo com PID $PID já não estava em execução. PID file limpo.\033[0m"
fi
