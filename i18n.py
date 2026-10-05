"""
Internationalization (i18n) module for Gemini-OpenAI Proxy.
Supports Portuguese (pt-BR) and English (en) across both:
1. Terminal server logs.
2. Web UI frontend translations.
"""

from config import load_config
from datetime import datetime

# ANSI Color codes for aesthetic terminal logs
BLUE = "\033[94m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

TERMINAL_STRINGS = {
    "pt-BR": {
        "server_started": f"{GREEN}{BOLD}[Proxy]{RESET} Servidor iniciado com sucesso em {CYAN}http://{{host}}:{{port}}{RESET}",
        "tier_info": f"{BLUE}[Info]{RESET} Plano Ativo: {BOLD}{{tier}}{RESET} (Projeto: {{project}})",
        "auth_ok": f"{GREEN}[Auth]{RESET} Autenticado com sucesso via Keyring Linux: {BOLD}{{email}}{RESET}",
        "auth_cleared": f"{YELLOW}[Auth]{RESET} Sessão local e cache de tokens foram limpos.",
        "request_recv": f"{CYAN}[Req]{RESET} Requisição recebida | Modelo: {BOLD}{{model}}{RESET} | Stream: {{stream}}",
        "tool_call": f"{YELLOW}[Tool]{RESET} Chamada de função detectada do modelo: {BOLD}{{name}}{RESET}",
        "tool_result": f"{BLUE}[Tool]{RESET} Resposta da ferramenta {{name}} recebida do agente ({{bytes_len}} bytes)",
        "completed": f"{GREEN}[Concluído]{RESET} Resposta finalizada em {{duration:.2f}}s",
        "upstream_err": f"{RED}[Erro Upstream]{RESET} Falha ao contatar Google Cloud Code: {{err}}",
        "config_updated": f"{GREEN}[Config]{RESET} Configurações salvas: Idioma={{lang}} | Host={{host}} | Porta={{port}}",
        "auth_error": f"{RED}[Auth Erro]{RESET} Falha ao autenticar no chaveiro: {{error}}",
        "server_stopped": f"{YELLOW}[Servidor]{RESET} Servidor proxy foi pausado/parado pelo usuário.",
        "server_started_toggle": f"{GREEN}[Servidor]{RESET} Servidor proxy foi reativado com sucesso.",
        "account_switched": f"{CYAN}[Conta]{RESET} Conta ativa alterada para: {BOLD}{{email}}{RESET}",
        "account_added": f"{GREEN}[Conta]{RESET} Nova conta adicionada via OAuth: {BOLD}{{email}}{RESET}",
        "account_removed": f"{YELLOW}[Conta]{RESET} Conta removida: {BOLD}{{email}}{RESET}",
        "oauth_started": f"{BLUE}[OAuth]{RESET} Servidor de autorização iniciado na porta {{port}}. Aguardando consentimento...",
        "shutdown": f"{YELLOW}[Proxy]{RESET} Encerrando servidor de forma segura..."
    },
    "en": {
        "server_started": f"{GREEN}{BOLD}[Proxy]{RESET} Server successfully started on {CYAN}http://{{host}}:{{port}}{RESET}",
        "tier_info": f"{BLUE}[Info]{RESET} Active Tier: {BOLD}{{tier}}{RESET} (Project: {{project}})",
        "auth_ok": f"{GREEN}[Auth]{RESET} Authenticated via Linux Keyring: {BOLD}{{email}}{RESET}",
        "auth_cleared": f"{YELLOW}[Auth]{RESET} Local session and token cache have been cleared.",
        "request_recv": f"{CYAN}[Req]{RESET} Request received | Model: {BOLD}{{model}}{RESET} | Stream: {{stream}}",
        "tool_call": f"{YELLOW}[Tool]{RESET} Tool call detected from model: {BOLD}{{name}}{RESET}",
        "tool_result": f"{BLUE}[Tool]{RESET} Tool result for {{name}} received from agent ({{bytes_len}} bytes)",
        "completed": f"{GREEN}[Completed]{RESET} Finished response in {{duration:.2f}}s",
        "upstream_err": f"{RED}[Upstream Error]{RESET} Failed to reach Google Cloud Code: {{err}}",
        "config_updated": f"{GREEN}[Config]{RESET} Settings updated: Lang={{lang}} | Host={{host}} | Port={{port}}",
        "auth_error": f"{RED}[Auth Error]{RESET} Failed to authenticate via keyring: {{error}}",
        "server_stopped": f"{YELLOW}[Server]{RESET} Proxy server paused/stopped by user.",
        "server_started_toggle": f"{GREEN}[Server]{RESET} Proxy server resumed successfully.",
        "account_switched": f"{CYAN}[Account]{RESET} Active account switched to: {BOLD}{{email}}{RESET}",
        "account_added": f"{GREEN}[Account]{RESET} New account added via OAuth: {BOLD}{{email}}{RESET}",
        "account_removed": f"{YELLOW}[Account]{RESET} Account removed: {BOLD}{{email}}{RESET}",
        "oauth_started": f"{BLUE}[OAuth]{RESET} Authorization listener started on port {{port}}. Waiting for consent...",
        "shutdown": f"{YELLOW}[Proxy]{RESET} Shutting down server safely..."
    }
}

UI_TRANSLATIONS = {
    "pt-BR": {
        "title": "Gemini OpenAI Proxy",
        "subtitle": "Ponte oficial para o Hermes Agent usando sua assinatura Google AI Pro",
        "nav_dashboard": "Painel & Cotas",
        "nav_models": "Catálogo de Modelos",
        "nav_guide": "Guia de Agentes",
        "nav_settings": "Configurações",
        "status_running": "Servidor Operacional",
        "status_offline": "Servidor Parado",
        "tier_title": "Assinatura Ativa",
        "tier_sub": "Google AI Pro (Cota Dedicada)",
        "project_title": "Projeto Gerenciado",
        "keyring_status": "Chaveiro do Linux",
        "keyring_connected": "Conectado",
        "btn_server_start": "▶️ Iniciar Servidor",
        "btn_server_stop": "⏹️ Parar Servidor",
        "btn_add_account": "+ Adicionar Conta Google",
        "btn_sign_out": "Sair da Conta",
        "accounts_dropdown_title": "Contas Conectadas",
        "badge_active": "Ativa",
        "quota_headline": "Consumo de Cota em Tempo Real",
        "quota_desc": "Extraído diretamente da infraestrutura do Google Cloud Code para o seu plano Google AI Pro.",
        "label_5h_window": "Janela 5 Horas",
        "label_monthly_quota": "Cota Mensal",
        "label_resets_in": "Reseta em",
        "label_days_left": "dias restantes",
        "label_used": "usado",
        "label_available": "disponível",
        "models_headline": "Modelos Disponíveis",
        "models_desc": "Todos os modelos suportados pela sua cota e compatíveis com Function Calling no Hermes.",
        "btn_copy_model": "Copiar ID",
        "guide_headline": "Como Conectar o Hermes Agent",
        "guide_step1": "Passo 1: Variáveis de Ambiente no Terminal",
        "guide_step2": "Passo 2: Iniciar o Hermes Agent",
        "guide_step3": "Passo 3: Como as Ferramentas Operam",
        "guide_step3_desc": "O modelo prevê as ferramentas do Hermes e emite o JSON cru. O Hermes executa localmente no sandbox dele sem nenhuma interferência do proxy.",
        "settings_headline": "Configurações do Servidor",
        "settings_lang": "Idioma da Interface e Logs de Terminal",
        "settings_host": "Endereço de Escuta (Host)",
        "settings_port": "Porta do Servidor",
        "btn_save_settings": "Salvar Configurações",
        "saved_success": "Configurações salvas com sucesso!"
    },
    "en": {
        "title": "Gemini OpenAI Proxy",
        "subtitle": "Official bridge for Hermes Agent using your Google AI Pro subscription",
        "nav_dashboard": "Dashboard & Quotas",
        "nav_models": "Model Catalog",
        "nav_guide": "Agent Guide",
        "nav_settings": "Settings",
        "status_running": "Server Operational",
        "status_offline": "Server Stopped",
        "tier_title": "Active Subscription",
        "tier_sub": "Google AI Pro (Dedicated Quota)",
        "project_title": "Managed Project",
        "keyring_status": "Linux Keyring",
        "keyring_connected": "Connected",
        "btn_server_start": "▶️ Start Server",
        "btn_server_stop": "⏹️ Stop Server",
        "btn_add_account": "+ Add Google Account",
        "btn_sign_out": "Sign Out",
        "accounts_dropdown_title": "Connected Accounts",
        "badge_active": "Active",
        "quota_headline": "Real-time Quota Consumption",
        "quota_desc": "Extracted directly from Google Cloud Code infrastructure for your Google AI Pro tier.",
        "label_5h_window": "5-Hour Window",
        "label_monthly_quota": "Monthly Quota",
        "label_resets_in": "Resets in",
        "label_days_left": "days left",
        "label_used": "used",
        "label_available": "available",
        "models_headline": "Available Models",
        "models_desc": "All models supported by your quota and compatible with Hermes Function Calling.",
        "btn_copy_model": "Copy ID",
        "guide_headline": "How to Connect Hermes Agent",
        "guide_step1": "Step 1: Set Environment Variables",
        "guide_step2": "Step 2: Launch Hermes Agent",
        "guide_step3": "Step 3: How Tool Calling Works",
        "guide_step3_desc": "The model anticipates Hermes tools and emits raw JSON. Hermes executes locally inside its sandbox with zero proxy interference.",
        "settings_headline": "Server Settings",
        "settings_lang": "Interface & Terminal Log Language",
        "settings_host": "Listen Address (Host)",
        "settings_port": "Server Port",
        "btn_save_settings": "Save Settings",
        "saved_success": "Settings saved successfully!"
    }
}


def log_terminal(key, **kwargs):
    """Prints a localized terminal message using current configuration."""
    cfg = load_config()
    lang = cfg.get("language", "pt-BR")
    table = TERMINAL_STRINGS.get(lang, TERMINAL_STRINGS["pt-BR"])
    template = table.get(key, key)
    time_str = datetime.now().strftime("%H:%M:%S")
    formatted = template.format(**kwargs)
    print(f"\033[90m[{time_str}]\033[0m {formatted}")


def get_ui_translations(lang="pt-BR"):
    """Returns UI dictionary for the given language."""
    return UI_TRANSLATIONS.get(lang, UI_TRANSLATIONS["pt-BR"])
