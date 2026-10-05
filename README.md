# 🚀 Gemini Google AI Pro -> OpenAI Proxy Server

Um servidor proxy ponte, de alta fidelidade e compatível com a API OpenAI (`/v1/chat/completions` e `/v1/models`), projetado especificamente para conectar sua assinatura do **Google AI Pro** (através da sessão oficial do Antigravity) diretamente a harnesses de agentes autônomos avançados como o **Hermes Agent**, **Claude Code**, **Cursor**, **Cline** e scripts Python.

---

## 🌟 Principais Recursos

- **Compatibilidade Total OpenAI**: Tradução bidirecional transparente entre o formato OpenAI (`chat.completion`, streaming SSE, `tool_calls`) e o protocolo Cloud Code da Google.
- **Ferramentas e Function Calling Preservados**: Suporte completo a múltiplos turnos com chamadas de ferramentas, tratando automaticamente o `thoughtSignature` (`skip_thought_signature_validator`) para que o Hermes execute suas próprias ferramentas locais sem conflito.
- **Autenticação Segura via Keyring**: Lê o token OAuth oficial do Antigravity diretamente do Secret Service do Linux (DBus / GNOME Keyring / KWallet), renovando-o automaticamente sem necessidade de chaves de API estáticas ou exposição de senhas.
- **Monitoramento de Cota em Tempo Real**: Consulta a API interna da Google (`retrieveUserQuota`) para exibir porcentagens de consumo e contagem regressiva para renovação das cotas de cada família de modelo (`gemini-2.5-pro`, `gemini-2.5-flash`, etc.).
- **Dashboard Web Moderno**: Interface em tempo real, escura e responsiva, com controle de status, recarga/limpeza de sessão, catálogo de modelos com cópia rápida e guias interativos de configuração.
- **Bilinguismo Dinâmico (i18n)**: Alterne entre **Português (Brasil)** e **English** diretamente pela UI, alterando instantaneamente tanto o painel web quanto as saídas coloridas no terminal de logs.
- **Scripts de Controle**: Inicie e pare o serviço em segundo plano com apenas `./start.sh` e `./stop.sh`.

---

## 📁 Estrutura do Projeto

```
gemini-openai-proxy/
├── start.sh              # Script de inicialização em background
├── stop.sh               # Script de encerramento seguro
├── server.py             # Servidor HTTP multithread (endpoints OpenAI, API e Dashboard)
├── auth.py               # Integração com DBus Secret Service e consulta de cotas
├── accounts.py           # Gerenciador multi-contas Google OAuth com alternância instantânea
├── api_keys.py           # Gerenciador de API Keys locais (opcionais com toggle)
├── sync_hermes.py        # Sincronizador automático de modelos para o Hermes Agent
├── converter.py          # Conversor de payloads OpenAI <-> Cloud Code Proto
├── config.py             # Gerenciador de persistência de configurações
├── i18n.py               # Dicionário de traduções (PT-BR / EN) e logger colorido
├── static/
│   └── index.html        # Dashboard Web responsivo e estilizado
├── test_client.py        # Script de validação e teste de ponta a ponta
└── README.md             # Esta documentação
```

---

## ⚡ Início Rápido

### 1. Iniciar o Servidor
Certifique-se de que está autenticado no Antigravity e execute:
```bash
./start.sh
```
A saída exibirá o PID do processo e as URLs de acesso:
```text
========================================================
       Gemini Google AI Pro -> OpenAI Proxy Server      
========================================================
[*] Iniciando servidor em background...
[✓] Servidor iniciado com sucesso! (PID: 12345)

  🌐 Dashboard Web:      http://127.0.0.1:8000/
  🔌 OpenAI Base URL:    http://127.0.0.1:8000/v1
  📊 Cotas & Status:     http://127.0.0.1:8000/v1/quota

  📋 Comandos úteis:
     - Para visualizar logs em tempo real: tail -f server.log
     - Para encerrar o servidor:           ./stop.sh
========================================================
```

### 2. Acessar o Dashboard Web
Abra seu navegador em:
👉 **[http://127.0.0.1:8000/](http://127.0.0.1:8000/)**

No painel você poderá:
- Verificar o status da conta conectada e o plano ativo (`Google AI Pro`).
- Observar as barras de cota e o tempo restante para o reset da janela diária.
- Alternar o idioma da interface e dos logs no terminal (PT-BR / EN).
- Copiar os IDs dos modelos compatíveis com um clique.

### 3. Acompanhar os Logs do Terminal
Para acompanhar as requisições, durações e tokens consumidos:
```bash
tail -f server.log
```

### 4. Parar o Servidor
Para encerrar a execução de maneira limpa:
```bash
./stop.sh
```

---

## 🤖 Como Conectar ao Hermes Agent

O [Hermes Agent](https://github.com/NousResearch/Hermes-Agent) utiliza a especificação OpenAI. Configure as variáveis de ambiente ou o arquivo de perfil do Hermes:

### Configuração via Variáveis de Ambiente
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
export OPENAI_API_KEY="google-ai-pro-proxy"  # Qualquer valor não vazio
export MODEL="gemini-2.5-pro"
```

### Executar o Hermes
```bash
hermes run "Analise os arquivos do diretório atual e liste os pontos de refatoração"
```

> **Nota sobre Tool Calling**: O proxy não tenta executar as ferramentas declaradas pelo Hermes. Ele encaminha as assinaturas ao modelo Gemini e devolve a resposta no formato padrão `tool_calls`. O Hermes recebe a chamada, executa o comando localmente em seu harness e devolve o resultado para a próxima rodada normalmente!

---

## 🛠️ Outras Conexões

### Claude Code
```bash
export OPENAI_BASE_URL="http://127.0.0.1:8000/v1"
export OPENAI_API_KEY="google-ai-pro-proxy"
claude --model gemini-2.5-pro
```

### Cursor / Cline
Nas configurações do editor:
- **Provider**: OpenAI Compatible
- **Base URL**: `http://127.0.0.1:8000/v1`
- **API Key**: `google-ai-pro-proxy`
- **Model ID**: `gemini-2.5-pro` (ou `gemini-2.5-flash`)

### Python (OpenAI SDK)
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="google-ai-pro-proxy"
)

response = client.chat.completions.create(
    model="gemini-2.5-pro",
    messages=[
        {"role": "user", "content": "Olá! Explique resumidamente como funciona o proxy."}
    ],
    stream=True
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
print()
```

---

## 📊 Endpoints da API

| Endpoint | Método | Descrição |
| :--- | :--- | :--- |
| `/` | `GET` | Dashboard Web e painel de controle interativo |
| `/v1/models` | `GET` | Lista de modelos Gemini e Claude suportados no formato OpenAI |
| `/v1/chat/completions` | `POST` | Execução de completions e streamings (compatível com OpenAI) |
| `/v1/quota` | `GET` | Consulta de cotas em tempo real com pools aglutinados e resets de 5h e mensal |
| `/api/server/status` | `GET` | Retorna se o proxy está operacional ou pausado |
| `/api/server/toggle` | `POST` | Alterna o estado do proxy entre Ativo e Pausado |
| `/api/accounts` | `GET` | Lista as contas cadastradas e indica a conta ativa |
| `/api/accounts/switch` | `POST` | Altera a conta Google ativa utilizada pelo proxy |
| `/api/accounts/remove` | `POST` | Desloga / remove uma conta do sistema |
| `/api/oauth/start` | `GET` | Inicia o servidor local de callback e gera URL Google OAuth |
| `/api/oauth/status` | `GET` | Retorna o status de conclusão do fluxo OAuth em andamento |
| `/api/config` | `GET/POST` | Lê e altera endereço, porta e idioma ativo |

---

## 🔒 Conformidade e Legitimidade

Este proxy foi construído respeitando os termos de serviço e as diretrizes de uso pessoal da assinatura Google AI Pro:
1. **Sem engenharia reversa maliciosa**: Utiliza o token de autenticação e os endpoints oficiais expostos pelo Antigravity na máquina local do próprio usuário.
2. **Uso estritamente pessoal**: Não atua como SaaS, não compartilha tokens com terceiros e não comercializa acessos.
3. **Respeito às cotas e limites**: Não burla limites nem efetua abusos de carga; as requisições estão sujeitas aos mesmos limites contratuais do plano contratado.
