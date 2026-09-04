# ClaroUnify Hub — MVP Funcional

MVP 100% funcional do orquestrador conversacional descrito na documentação técnica
do desafio (Sprint 1 e 2). Roda localmente, sem chaves de API pagas, e demonstra o
fluxo completo: canais → autenticação silenciosa → NLU → roteador de agentes →
agentes especializados → backends → handoff → dashboard de KPIs.

📄 Documentos relacionados neste repositório:
- [`CHANGELOG.md`](./CHANGELOG.md) — histórico de decisões e correções, ciclo a ciclo
  (útil para a apresentação: mostra a evolução guiada por revisão crítica).
- Seção **"Checklist de Requisitos"** mais abaixo — rastreabilidade RF/RNF contra o
  Documento de Visão (Sprint 1).

---

## Sumário

1. [Pré-requisitos](#pré-requisitos)
2. [Instalação passo a passo](#instalação-passo-a-passo)
3. [Como rodar](#como-rodar)
4. [Rodando com Docker](#rodando-com-docker)
5. [Testes automatizados (pytest)](#testes-automatizados-pytest)
6. [Como validar que está tudo funcionando](#como-validar-que-está-tudo-funcionando)
7. [Estrutura do projeto](#estrutura-do-projeto)
8. [O que está na tela](#o-que-está-na-tela)
9. [LLM gratuito (Qwen3 4B via Ollama)](#llm-gratuito-qwen3-4b-via-ollama)
10. [Variáveis de ambiente](#variáveis-de-ambiente)
11. [Referência completa da API](#referência-completa-da-api)
12. [O que é real vs. mockado nesta versão](#o-que-é-real-vs-mockado-nesta-versão)
13. [Checklist de Requisitos Funcionais e Não Funcionais](#checklist-de-requisitos-funcionais-e-não-funcionais)
14. [Troubleshooting](#troubleshooting)
15. [Do MVP à produção: integrações reais](#do-mvp-à-produção-como-as-integrações-reais-substituiriam-os-mocks)
16. [Próximos passos para evoluir rumo à arquitetura completa](#próximos-passos-para-evoluir-rumo-à-arquitetura-completa)

---

## Pré-requisitos

- **Python 3.10 ou superior** instalado ([python.org](https://www.python.org/downloads/)).
  Confira com `python --version` (ou `python3 --version` no Mac/Linux).
- **Nenhuma chave de API paga é necessária.** O sistema funciona 100% sem internet
  depois de instalado (motor por palavras-chave); o LLM gratuito local (Qwen3 4B via
  Ollama) é opcional — ver seção própria abaixo.
- ~200MB livres para as dependências Python. Se for usar o LLM local, mais ~2,5GB
  para o modelo Qwen3 4B.

## Instalação passo a passo

### Windows (PowerShell)

```powershell
cd caminho\para\clarounify_mvp
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Mac / Linux

```bash
cd caminho/para/clarounify_mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Se o `pip install` travar tentando compilar algo do zero (erro mencionando `maturin`,
`cargo` ou `link.exe`), veja a seção [Troubleshooting](#troubleshooting) — geralmente
é falta de wheel pronta pra sua versão do Python, e o fix é reinstalar sem versão
fixada (o `requirements.txt` já vem assim por esse motivo).

### Configurar o `.env` (opcional)

O sistema roda com valores padrão sensatos sem nenhuma configuração. Se quiser
ajustar algo (endereço do Ollama, limiares de alerta, caminho do banco etc.),
copie o exemplo e edite:

```bash
cp .env.example .env
```

Veja a lista completa em [Variáveis de ambiente](#variáveis-de-ambiente). O `.env`
nunca deve ser commitado (já está no `.gitignore`).

## Como rodar

Com o ambiente virtual ativado (passo acima):

```bash
uvicorn main:app --reload
```

Você deve ver algo como:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

Abra **http://localhost:8000** no navegador. Pronto — o motor por palavras-chave já
funciona sem nenhuma configuração extra. O LLM gratuito é opcional (seção própria).

Pra parar o servidor: `Ctrl+C` no terminal onde ele está rodando.

## Rodando com Docker

Alternativa ao passo a passo manual acima — sobe o backend **e** o Ollama (LLM
local) já configurados para conversar entre si, cada um em seu próprio container.

### Pré-requisitos

- [Docker](https://docs.docker.com/get-docker/) e Docker Compose (já vem junto no
  Docker Desktop, no Windows/Mac).

### Subir tudo

```bash
docker compose up --build
```

Na primeira vez isso builda a imagem do backend e baixa a imagem do Ollama —
pode demorar alguns minutos. Depois de subir, acesse **http://localhost:8000**
normalmente.

### Baixar o modelo Qwen3 dentro do container

O container do Ollama sobe vazio (sem modelo baixado ainda) — é preciso puxar o
modelo uma vez, de dentro do container:

```bash
docker exec -it clarounify-ollama ollama pull qwen3:4b
```

Isso só precisa ser feito uma vez: o modelo fica salvo no volume `ollama_models`,
que sobrevive a `docker compose down` (só some se você rodar
`docker compose down -v`, removendo os volumes também).

Enquanto o modelo não é baixado, o sistema funciona normalmente no fallback por
palavras-chave — confirme em **http://localhost:8000/api/llm-status**.

### Rodando sem o container do Ollama

Se você já tem o Ollama instalado direto na sua máquina (fora do Docker) e só
quer conteinerizar o backend:

1. Remova o serviço `ollama` do `docker-compose.yml` (e a linha `depends_on`).
2. Troque `OLLAMA_URL=http://ollama:11434` por
   `OLLAMA_URL=http://host.docker.internal:11434` no serviço `app`.

### Parar / limpar

```bash
docker compose down          # para os containers, mantém os dados (volumes)
docker compose down -v       # para e apaga também o banco e o modelo baixado
```

O `docker-compose.yml` já lê um `.env` na mesma pasta, se existir (copie de
`.env.example`). Qualquer variável não definida usa o padrão embutido no compose.

## Testes automatizados (pytest)

Além da suíte `prova_dos_9.py` (que testa o servidor real de ponta a ponta — ver
próxima seção), o projeto tem uma suíte `pytest` mais granular em `tests/`, pensada
para rodar rápido, sem depender do Ollama, e servir de rede de segurança para
mudanças futuras no código.

### Rodando

```bash
pip install -r requirements.txt   # pytest e pytest-cov já estão incluídos
pytest
```

Saída esperada:

```
tests/test_chat.py .........                                             [ 23%]
tests/test_dashboard.py ............                                     [ 55%]
tests/test_lgpd.py .....                                                 [ 68%]
tests/test_nlu_unit.py ............                                      [100%]

======================= 38 passed in 0.96s =======================
```

### Com relatório de cobertura

```bash
pytest --cov=main --cov=db --cov-report=term-missing
```

### O que cada arquivo cobre

| Arquivo | Cobre |
|---|---|
| `tests/test_nlu_unit.py` | Funções puras: limpeza do bloco `<think>` do LLM, classificador de intenção por palavras-chave, detecção de sinal de encerramento (gatilho do NPS), mascaramento de ID/nome — não sobe servidor, roda em milissegundos. |
| `tests/test_chat.py` | Fluxo de `/api/chat` de ponta a ponta: exigência de consentimento, detecção de intenção, handoff automático, persistência de histórico entre requisições, rate limiting. |
| `tests/test_lgpd.py` | Direitos do titular: exportação (portabilidade), exclusão (eliminação), registro de consentimento por cliente. |
| `tests/test_dashboard.py` | KPIs agregados, fila de handoff (e mascaramento LGPD nela), alertas automáticos, paginação de clientes, reset — e o fluxo completo de **pesquisa de satisfação (NPS)**: sinal de encerramento → prompt → nota → sessão `resolved` → KPI refletindo de verdade (o gap que existia antes de o NPS ser exercitado no chat). |

### Decisões de design da suíte

- **Banco isolado por teste**: cada teste usa um arquivo SQLite temporário
  próprio (`tmp_path` do pytest) — um teste nunca vê dado deixado por outro.
- **`USE_LLM=false` sempre nos testes**: a suíte não pode depender de o Ollama
  estar instalado e rodando na máquina de quem roda `pytest` (nem no CI). Ela
  testa o caminho determinístico (fallback por palavras-chave), que é o que
  garante que o sistema nunca quebra em produção. O caminho do LLM é validado
  manualmente e pela suíte `prova_dos_9.py` contra um servidor real.

## Como validar que está tudo funcionando

Depois de rodar o servidor (comando acima, deixe o terminal aberto), abra **outro**
terminal na mesma pasta e rode a suíte de testes automatizados:

```bash
pip install requests --break-system-packages   # só na 1ª vez (Linux/Mac);
                                                 # no Windows, sem a flag: pip install requests
python prova_dos_9.py
```

Isso executa **67 checagens reais** contra o servidor que está no ar — não é
simulado — cobrindo LGPD, roteamento de intenção, handoff, continuidade entre
canais, rate limiting, KPIs, autenticação silenciosa, trace ID, alertas e a
pesquisa de satisfação (NPS) exercitada de ponta a ponta pelo chat. No final
aparece:

```
RESULTADO: 67 passaram, 0 falharam
✅ Tudo passou. MVP validado de ponta a ponta.
```

Se algo falhar, o script aponta exatamente qual checagem e por quê.

## Estrutura do projeto

```
clarounify_mvp/
├── main.py                 # Backend FastAPI: NLU, agentes, LGPD, handoff, KPIs, LLM
├── db.py                   # Camada de dados (SQLite): CRM, faturas, rede, catálogo, sessões
├── clarounify.db           # Banco SQLite — criado e populado automaticamente na 1ª execução
├── requirements.txt        # Dependências Python (sem versão fixada — evita erro de wheel)
├── .env.example            # Modelo de configuração — copie para .env para customizar
├── Dockerfile              # Imagem do backend
├── docker-compose.yml      # Orquestra backend + Ollama, com volumes persistentes
├── prova_dos_9.py          # Suíte de 67 testes automatizados contra o servidor real
├── tests/                  # Suíte pytest (38 testes, cobre chat, LGPD, dashboard, NPS, NLU)
│   ├── conftest.py
│   ├── test_chat.py
│   ├── test_lgpd.py
│   ├── test_dashboard.py
│   └── test_nlu_unit.py
├── pytest.ini
├── README.md               # Este arquivo
├── CHANGELOG.md            # Histórico de decisões e correções, ciclo a ciclo
└── static/
    ├── index.html          # Estrutura da página (área Cliente + área Interna)
    ├── app.js              # Toda a lógica de frontend (fetch, renderização, gráficos, estado)
    └── style.css           # Identidade visual Claro (vermelho) + estilo WhatsApp
```

Não há build step, bundler ou compilação de frontend — é HTML/CSS/JS puro servido
diretamente pelo FastAPI (`StaticFiles`), de propósito, pra manter o setup em um
único comando. O único recurso externo é a biblioteca Chart.js, carregada via CDN
no `index.html` (usada só para o gráfico de mensagens por canal no dashboard).

**Sobre o banco (`clarounify.db`)**: é criado automaticamente na primeira vez que
você roda `uvicorn main:app`, com 20 clientes mock pré-cadastrados (ver `db.py` →
função de seed). Se quiser recomeçar do zero com dados limpos, apague o arquivo
`clarounify.db` e suba o servidor de novo — ele recria e repopula sozinho (ou use o
botão "reiniciar demo" na interface, que faz o equivalente sem apagar o arquivo).
Esse arquivo não deve ser versionado no Git (já está no `.gitignore`).

## O que está na tela

A interface separa claramente duas áreas, como um produto real teria:

- **👤 Sou Cliente** — o que o cliente final vê. Ao entrar, ele escolhe como quer
  falar com a Claro (Site Web ou WhatsApp) numa tela de seleção de canal — isso
  dispara uma **autenticação silenciosa** (RF005: nenhuma tela de login aparece).
  Dentro do chat, um banner permanente oferece **migrar para o outro canal**
  ("Continuar no WhatsApp →") — uma ação explícita, não só trocar de aba, que
  evidencia o orquestrador: ao migrar, aparece "🔀 Você migrou para o WhatsApp — o
  Context Manager já trouxe todo o histórico, sem repetir nada", e toda mensagem que
  aparece num canal mas se originou no outro ganha a etiqueta "recebido via Site
  Web"/"recebido via WhatsApp". Tem também o botão que simula a campanha proativa de
  fatura (Cenário 4 da documentação), disponível na visão de WhatsApp.
- **🏢 Painel Claro (interno)** — o que a operação da Claro vê, completamente
  separado da área do cliente:
  - **Dashboard Admin**: KPIs em tempo real (conversas ativas, taxa de resolução,
    volume de handoff, NPS, **custo por atendimento estimado**), alertas
    automáticos (ex.: volume de handoff acima do limiar), mensagens por canal, e
    log de eventos ao vivo mostrando inclusive qual motor de NLU respondeu cada
    mensagem (`llm` ou `keywords`).
  - **Painel de Handoff**: fila de conversas escaladas, com transcrição, perfil do
    cliente **mascarado por LGPD**, ações sugeridas e botão "Assumir atendimento".
  - **Iniciar Contato**: busca por nome/telefone, chips de segmento (fatura
    atrasada, fatura em aberto, instabilidade de rede) e paginação — pensado
    pra uma base real com milhões de clientes, onde listar tudo de uma vez
    nunca é a resposta certa. Tem duas formas de contato: **individual**
    (escolhe um cliente específico e o motivo — fatura vencendo, retenção,
    upgrade, ou mensagem livre) e **em massa** (dispara pra todo mundo que bate
    com o filtro atual de uma vez, ex.: "todos os clientes com fatura
    atrasada"). A mensagem fica esperando o cliente na área "Sou Cliente" assim
    que ele entrar naquele canal.

Use o seletor **"Simulando o cliente"** no topo (visível nas duas áreas) para trocar
entre os dois clientes mock e testar cenários diferentes — um tem instabilidade de
rede no OSS, o outro não; um tem fatura em aberto, o outro paga.

Clique em **"reiniciar demo"** a qualquer momento para zerar todas as sessões e
eventos e recomeçar do zero.

**Tratamento de erro**: toda chamada do frontend ao backend passa por um wrapper
central (`apiFetch`, em `static/app.js`) que captura falha de rede ou erro HTTP e
mostra um toast no canto da tela — o usuário nunca fica olhando pra uma tela travada
sem explicação. Se o servidor cair no meio de uma conversa, o chat mostra um aviso
inline; se o dashboard, a fila de handoff ou a lista de contato não conseguirem
carregar, cada um mostra o motivo em vez de ficar em branco.

## LLM gratuito (Qwen3 4B via Ollama)

Por padrão, o sistema tenta usar um LLM local e gratuito para classificar intenção e
escrever as respostas de forma mais natural. Se o Ollama não estiver instalado, ele
cai sozinho para o motor por palavras-chave — **você não precisa instalar nada pra
rodar o MVP**, isso é um upgrade opcional.

### Instalação

```bash
# 1. instale o Ollama: https://ollama.com/download
# 2. baixe o modelo (uma vez só, ~2,5GB):
ollama pull qwen3:4b
# 3. deixe o servidor do Ollama rodando (geralmente já fica em segundo plano
#    sozinho depois de instalado; se não, rode manualmente e deixe a janela aberta):
ollama serve
# 4. rode o ClaroUnify normalmente, em outro terminal:
uvicorn main:app --reload
```

Se o instalador oficial falhar no Windows (erro envolvendo `cmd.exe`), use a versão
portátil — ver [Troubleshooting](#troubleshooting).

### Como confirmar que está ativo

Acesse **http://localhost:8000/api/llm-status**. Se aparecer:

```json
{"ollama_rodando": true, "modelo_encontrado": true, "modo_ativo": "llm"}
```

o Qwen3 está respondendo de verdade. Se `modo_ativo` vier como
`"palavras-chave (fallback)"`, o Ollama não foi encontrado — o MVP continua
funcionando normalmente, só sem o LLM.

Outra forma de confirmar: no **Painel Claro (interno) → Dashboard → Log de
eventos**, cada `resposta_bot` mostra `motor: llm` ou `motor: keywords`.

### Como funciona a proteção contra alucinação

O LLM **nunca** recebe autonomia para inventar fatos. Cada agente busca o dado real
no mock primeiro (fatura, status de rede, catálogo) e só então pede ao LLM para
**reescrever** esse fato de forma mais natural — ele nunca cria valor de fatura,
prazo ou plano que não exista no mock. Ver `compose_reply()` e `llm_rewrite_reply()`
em `main.py`.

### Trocar de modelo

```bash
# Linux/Mac
OLLAMA_MODEL=llama3.2 uvicorn main:app --reload

# Windows (PowerShell)
$env:OLLAMA_MODEL="llama3.2"; uvicorn main:app --reload
```

## Variáveis de ambiente

Todas opcionais — o sistema roda com os padrões abaixo sem precisar configurar nada.
Veja também `.env.example` (copie para `.env` para customizar sem editar código).

**LLM (Qwen3 via Ollama)**

| Variável | Padrão | Para que serve |
|---|---|---|
| `USE_LLM` | `true` | Define `false` para desligar completamente as tentativas de usar o Ollama e forçar sempre o motor por palavras-chave. |
| `OLLAMA_URL` | `http://localhost:11434` | Endereço do servidor Ollama, caso rode em outra máquina/porta (no Docker, aponta para `http://ollama:11434`). |
| `OLLAMA_MODEL` | `qwen3:4b` | Nome do modelo Ollama a usar (precisa ter sido baixado com `ollama pull` antes). |
| `LLM_TIMEOUT_S` | `20` | Timeout (segundos) por chamada ao Ollama depois do modelo já estar "aquecido". A primeira chamada (cold start) usa automaticamente pelo menos 60s. |
| `HANDOFF_WEBHOOK_URL` | *(vazio)* | Se definida, todo handoff passa a enviar um POST com os dados do ticket para essa URL (ex.: webhook de entrada do Zendesk/Salesforce), em vez de só cair na fila interna. |

**Banco de dados e logging**

| Variável | Padrão | Para que serve |
|---|---|---|
| `DATABASE_PATH` | `./clarounify.db` | Caminho do arquivo SQLite. No Docker, aponta para o volume persistente (`/data/clarounify.db`). |
| `LOG_LEVEL` | `INFO` | Nível de log da aplicação: `DEBUG`, `INFO`, `WARNING` ou `ERROR`. |

**Regras de negócio**

| Variável | Padrão | Para que serve |
|---|---|---|
| `DEFAULT_CUSTOMER` | `11999990001` | Cliente usado como fallback quando um `customer_id` inexistente é informado. |
| `RATE_LIMIT_MAX_MSGS` | `20` | Máximo de mensagens por cliente dentro da janela de tempo abaixo, antes de bloquear. |
| `RATE_LIMIT_WINDOW_S` | `60` | Duração (segundos) da janela de rate limiting. |
| `ALERT_VOLUME_HANDOFF_PCT_MAX` | `40.0` | Limiar do alerta automático de volume de handoff (RNF006). |
| `ALERT_TAXA_RESOLUCAO_PCT_MIN` | `50.0` | Limiar do alerta automático de taxa de resolução baixa. |
| `ALERT_RATE_LIMIT_BLOQUEIOS_MAX` | `3` | Quantidade de bloqueios de rate limit que dispara alerta de possível abuso. |
| `CUSTO_BOT_ESTIMADO` | `0.15` | Custo ilustrativo (R$) atribuído a cada atendimento resolvido 100% pelo bot, usado só para exemplificar o cálculo de custo/atendimento no dashboard. |
| `CUSTO_HANDOFF_ESTIMADO` | `8.50` | Custo ilustrativo (R$) atribuído a cada atendimento que escala para humano. |

Exemplo desligando o LLM e forçando só o motor determinístico (útil para
demonstrações onde a previsibilidade da resposta importa mais que a naturalidade):

```bash
USE_LLM=false uvicorn main:app --reload
```

## Referência completa da API

Todos os endpoints abaixo existem em `main.py` e são exercitados por
`prova_dos_9.py`. Base local: `http://localhost:8000`.

### Autenticação e consentimento

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/auth/silent` | RF005 — emite um token de sessão sem tela de login. Corpo: `{customer_id, channel}`. Retorna `{token, customer_id}`. |
| `POST` | `/api/consent` | Registra aceite/recusa do aviso de LGPD. Corpo: `{customer_id, accepted}`. |

### Conversa

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/chat` | Endpoint principal. Corpo: `{customer_id, channel, text, auth_token?}`. Roda consentimento → rate limit → NLU → roteamento → agente → (LLM opcional) → resposta. |
| `GET` | `/api/session/{customer_id}` | Retorna o CRM mock e a sessão completa (histórico, status, intenção atual) daquele cliente. |
| `POST` | `/api/nps` | Registra a avaliação NPS do cliente ao final de uma conversa resolvida. Corpo: `{customer_id, score}`. |
| `POST` | `/api/campanha-proativa/{customer_id}` | Simula o Cenário 4: BSS avisando o orquestrador de fatura próxima do vencimento; dispara mensagem proativa. |
| `GET` | `/api/admin/clientes?q=&fatura_status=&status_rede=&limit=20&offset=0` | Busca paginada e filtrável de clientes (nunca retorna a base inteira de uma vez) — dados completos, não mascarados (visão interna legítima). `fatura_status` ∈ `paga`, `em_aberto`, `atrasada`; `status_rede` ∈ `normal`, `instabilidade`. |
| `POST` | `/api/admin/iniciar-contato` | Painel Claro inicia contato com **um** cliente escolhido. Corpo: `{customer_id, channel, motivo, mensagem_personalizada?}`. `motivo` ∈ `fatura_vencendo`, `retencao`, `upgrade_oferta`, `outro` (este último exige `mensagem_personalizada`). |
| `POST` | `/api/admin/iniciar-contato-lote` | Painel Claro inicia contato com **todos os clientes que baterem com um filtro** (mesmo formato de filtro do `/api/admin/clientes`). Corpo: `{channel, motivo, mensagem_personalizada?, busca?, fatura_status?, status_rede?}`. Retorna `{total_filtrado, total_enviado, falhas}`. |

### Handoff

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/handoff-queue` | Lista as conversas atualmente escaladas para atendimento humano (dados já mascarados por LGPD). |
| `POST` | `/api/handoff-queue/{customer_id}/assumir` | Marca a conversa como assumida por um atendente humano. |

### LGPD (direitos do titular)

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/lgpd/exportar/{customer_id}` | Direito de portabilidade — devolve tudo que o Hub guarda sobre o cliente. |
| `DELETE` | `/api/lgpd/excluir/{customer_id}` | Direito de eliminação — apaga a sessão conversacional daquele cliente. |

### Observabilidade e operação

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/kpis` | Métricas agregadas: taxa de resolução, volume de handoff, NPS médio, custo por atendimento estimado, mensagens por canal. |
| `GET` | `/api/alertas` | Compara os KPIs atuais contra limiares fixos e retorna alertas ativos (RNF006). |
| `GET` | `/api/events?limit=30&mask=true` | Log de eventos mais recentes (mascarados por padrão). |
| `GET` | `/api/capacidade` | Relatório honesto de capacidade: arquitetura atual, P95 real medido, o que falta para escalar. |
| `GET` | `/api/llm-status` | Mostra se o Ollama/Qwen3 está disponível ou se o sistema está no fallback. |
| `POST` | `/api/reset` | Zera todas as sessões, eventos e contadores de rate limit — usado pelo botão "reiniciar demo". |

## O que é real vs. mockado nesta versão

| Componente | Nesta versão | Na arquitetura completa (Sprint 1/2) |
|---|---|---|
| NLU | Qwen3 4B local via Ollama (grátis) com fallback automático por palavras-chave | LLM (GPT-4o/Gemini Pro) via LangChain |
| Context Manager | **SQLite** (`clarounify.db`), tabelas `sessions`+`messages` — sobrevive a restart/crash do servidor | Redis (sessão) + PostgreSQL (histórico) |
| CRM / Faturas / Rede / Catálogo | **SQLite** (`clarounify.db`), populado com 5 clientes mock via `db.py` — não é mais dicionário hardcoded | Sistemas legados da Claro via API |
| Roteador de Agentes | Real — decide o agente pela intenção | LangGraph |
| Agentes (Vendas/Suporte/Info) | Reais — consultam o banco e respondem | Mesmos agentes, com LLM + APIs reais |
| Canal WhatsApp | Simulado na própria interface | Meta Cloud API (WhatsApp Business) |
| Canal App Mobile | **Não existe no MVP** | SDK integrado (iOS/Android) |
| Mensageria (eventos) | Lista em memória (real, funcional) | Kafka / Google Pub-Sub |
| Handoff | Real — fila, transcrição, ações sugeridas; conector plugável (`HANDOFF_WEBHOOK_URL`) para sistema real | Integrado a um sistema de atendimento real (Zendesk/Salesforce) |
| Contato iniciado pela Claro | Real — Painel Claro escolhe qualquer cliente do banco e o motivo (`/api/admin/iniciar-contato`) | Igual, disparado por regras de negócio automáticas monitorando o BSS/CRM |
| Dashboard / KPIs | Real — calculado a partir dos eventos, incluindo custo por atendimento | Looker Studio / Metabase sobre BigQuery |
| Auth SSO | Token opaco simulado, emitido silenciosamente (`/api/auth/silent`) | OAuth 2.0 / OIDC com provedor de identidade real |
| Observabilidade | Trace ID por jornada + alertas por limiar (real, funcional) | OpenTelemetry + Grafana/Cloud Monitoring |

Tudo marcado como "real, funcional" roda de ponta a ponta agora — a troca por Redis,
Kafka, LLM em nuvem ou pela API do WhatsApp é uma **substituição de implementação**,
não uma mudança de arquitetura: as interfaces (rotas da API, contrato dos agentes,
formato dos eventos) já seguem o desenho documentado.

## Checklist de Requisitos Funcionais e Não Funcionais

Conferência linha a linha contra o Documento de Visão (Sprint 1) — ✅ completo,
🟡 parcial, 🔴 não implementado. Nada aqui foi marcado como completo sem um teste
automatizado provando (ver `prova_dos_9.py`).

| # | Requisito | Status | Evidência / gap |
|---|---|---|---|
| RF001 | Orquestração unificada de canais | 🟡 | Site + WhatsApp cobertos e testados; **App Mobile não existe no MVP** |
| RF002 | Memória contextual de sessão | ✅ | Context Manager persistido em SQLite (sobrevive a restart), testes 4, 20 |
| RF003 | Classificação de intenção via NLU | ✅ | Qwen3 4B (LLM) + fallback por palavra-chave, testes 3 |
| RF004 | Agentes especializados plugáveis | ✅ | Vendas/Suporte/Informação, registry pattern |
| RF005 | Autenticação silenciosa por canal | 🟡 | Token opaco emitido sem tela de login (`/api/auth/silent`, teste 14) — não é OAuth 2.0/OIDC real com IdP externo |
| RF006 | Dashboard de KPIs (resolução por canal, transbordo, NPS, custo/atendimento) | ✅ | Todos os 4 campos presentes, teste 15 |
| RF007 | Fallback para atendimento humano | ✅ | Cancelamento, pedido explícito, insistência, frustração recorrente, testes 5-8 |
| RF008 | Campanhas proativas | 🟡 | WhatsApp implementado; push app não existe (não há canal app) |
| RNF001 | Tempo de resposta P95 ≤ 2s | ✅* | P95 **medido de verdade** (não estimado) em `/api/capacidade`, teste 16 — *tráfego sequencial de demo, não carga concorrente de produção |
| RNF002 | SLA 99,5% uptime | 🔴 | Não aplicável a processo único local — documentado sem inventar número |
| RNF003 | Escalabilidade horizontal 10x | 🔴 | Não implementado — exige Redis/K8s (ver "Próximos passos") |
| RNF004 | LGPD + TLS 1.3 + AES-256 | 🟡 | Consentimento, mascaramento, portabilidade, exclusão ✅ (testes 2, 10); TLS/AES-256 não se aplicam a `localhost` |
| RNF005 | Portabilidade de canal | ✅ | Agentes não recebem nem dependem do parâmetro de canal para decidir a resposta |
| RNF006 | Observabilidade (trace ID, alertas) | ✅ | Trace ID por jornada em toda sessão/evento + `/api/alertas` com limiares, teste 17 |

**Sobre os 🔴/🟡 restantes**: nenhum deles é "esquecimento" — são simplificações de
escopo conscientes e documentadas, coerentes com o que um MVP acadêmico deve entregar
sem virar um projeto de produção de verdade.

## Troubleshooting

### `pip install` falha tentando compilar (`maturin`, `cargo`, `link.exe not found`)

O `requirements.txt` não fixa versões exatas por causa disso — se mesmo assim
acontecer, force instalação binária:

```bash
pip install --only-binary :all: fastapi uvicorn pydantic httpx
```

Costuma acontecer quando a versão do Python é muito nova e ainda não tem wheel
pronta pra alguma dependência.

### Servidor sobe e "Shutting down" aparece sozinho logo em seguida

Geralmente é `Ctrl+C` apertado sem querer, ou o terminal perdeu foco durante o
boot. Rode o comando de novo e não mexa no terminal até ver "Application startup
complete".

### Instalador do Ollama falha no Windows com erro de `cmd.exe`

```
Unable to execute file: C:\WINDOWS\system32\cmd.exe
CreateProcess failed; code 2.
```

Isso acontece quando o `cmd.exe` está bloqueado/corrompido no Windows (comum em
máquinas de laboratório/faculdade com política de segurança restritiva) — mas o
PowerShell continua funcionando normalmente. Solução: não use o instalador gráfico,
use a versão portátil (zip):

```powershell
# baixe ollama-windows-amd64.zip em https://github.com/ollama/ollama/releases/latest
Expand-Archive -Path "$HOME\Downloads\ollama-windows-amd64.zip" -DestinationPath C:\ollama
cd C:\ollama
.\ollama.exe serve
```

Em outro PowerShell, na mesma pasta:

```powershell
.\ollama.exe pull qwen3:4b
```

### Chat trava ~10-30s na primeira mensagem e depois volta pro fallback

Normal na primeira chamada: o Ollama precisa carregar o modelo (~2,5GB) na
VRAM/RAM. Duas mitigações já implementadas: o timeout da primeira chamada é
automaticamente estendido para 60s, e o servidor dispara um "aquecimento" em
segundo plano assim que sobe (`warm_up_llm_on_startup`). Se persistir, espere
15-20s depois de subir o `uvicorn` antes de mandar a primeira mensagem.

### Respostas do LLM demoram 20-30s mesmo depois de aquecido

O Qwen3 tem um modo de "pensamento" interno (chain-of-thought) que pode gerar
milhares de tokens antes da resposta final. Isso já está desligado por padrão
(`"think": false` na chamada ao Ollama, ver `call_ollama_chat()`). Se estiver usando
outro modelo de raciocínio (deepseek-r1, qwq etc.) via `OLLAMA_MODEL`, confirme que
ele também respeita esse parâmetro, ou troque para um modelo sem "thinking" mode.

### O chat mostra o "raciocínio" do modelo em inglês em vez da resposta final

Se aparecer algo como *"Okay, the user says... Let me phrase it concisely...
&lt;/think&gt;..."* antes da resposta de verdade, é o modo de pensamento do
Qwen3 vazando — o `"think": false` que mandamos pro Ollama nem sempre é
respeitado por todas as versões/builds do modelo. Já existe uma correção
automática para isso: `strip_thinking()` em `main.py` remove qualquer bloco
`<think>...</think>` (ou um `</think>` solto no meio do texto) de toda resposta
do LLM antes dela chegar no cliente. Se você estiver vendo isso, confirme que
está na versão mais recente do `main.py`; se aparecer mesmo assim, avise — pode
ser um padrão de tag que a função ainda não cobre.

### Mensagens de um canal não aparecem no outro

Isso quase sempre é **cache do navegador** servindo uma versão antiga do
`app.js`/`style.css`. O servidor já envia `Cache-Control: no-store` para tudo em
`/static` (ver `NoCacheStaticFiles` em `main.py`) e o HTML referencia os arquivos com
`?v=5` — se mesmo assim persistir, dê um hard refresh (`Ctrl+Shift+R`). Pra confirmar
que o backend está correto independente do navegador, rode `prova_dos_9.py` — a
seção 4 testa exatamente essa continuidade contra a API direto.

### `/api/llm-status` mostra `"ollama_rodando": false` mas o Ollama está rodando

Confira se `OLLAMA_URL` aponta pra porta certa (padrão `11434`) e se o
`.\ollama.exe serve` (ou `ollama serve`) está numa janela ainda aberta, sem erro.

### Docker: `/api/llm-status` mostra `ollama_rodando: false` mesmo com o container `ollama` no ar

Duas causas comuns:
1. O modelo ainda não foi baixado dentro do container — rode
   `docker exec -it clarounify-ollama ollama pull qwen3:4b` (ver
   [Rodando com Docker](#rodando-com-docker)).
2. O `OLLAMA_URL` do serviço `app` está como `http://localhost:11434` em vez de
   `http://ollama:11434` — dentro de um container, `localhost` aponta pro próprio
   container, nunca para outro serviço do compose.

### Docker: porta 8000 ou 11434 já em uso

Se você já tem algo rodando nessas portas na sua máquina, edite o mapeamento no
`docker-compose.yml` (ex.: `"8001:8000"` para expor em outra porta local) ou pare
o processo que já está usando a porta antes de subir o compose.

### `pytest` não encontra os módulos `main`/`db`

Rode o `pytest` sempre a partir da raiz do projeto (onde estão `main.py` e
`pytest.ini`), não de dentro de `tests/`. Se o ambiente virtual não estiver
ativado, ative-o antes.

## Do MVP à produção: como as integrações reais substituiriam os mocks

Esta seção existe porque é a pergunta que qualquer banca/avaliador vai fazer:
"e como isso funcionaria com os sistemas de verdade da Claro?". A resposta curta:
**a arquitetura em camadas não muda** — só a implementação por trás de funções que
já existem hoje. O núcleo orquestrador (NLU, roteador, agentes) nunca precisa saber
se está falando com um mock, um BSS real, ou se a mensagem chegou via `fetch()` do
navegador ou via webhook da Meta.

### Sistemas legados (BSS/OSS/CRM)

A regra que já está no Documento de Visão (Sprint 1, seção 7 — "Regras e
Restrições"): **nunca acesso direto a banco de dados de produção, só via API
documentada**. O que isso significa na prática:

1. **Trocar `db.py` por um cliente HTTP.** Hoje `agente_suporte()` chama
   `db.get_network_status(customer_id)`, que faz um `SELECT` no SQLite. Em
   produção, essa mesma função faria um `GET` autenticado (OAuth 2.0
   client-credentials) para a API real do OSS da Claro — por exemplo
   `GET /oss/v1/clientes/{id}/status-rede`. **A assinatura da função não muda**,
   só o corpo dela — é por isso que separamos essa camada desde o início.
2. **Deploy dentro do perímetro de rede da Claro.** O Hub roda em Cloud
   Run/GKE (como o Documento de Visão já prevê), dentro da rede da empresa —
   não faz sentido (nem é permitido) chamar sistemas internos de fora pela
   internet pública. Só os canais (WhatsApp, site) têm ponta exposta.
3. **Cache e circuit breaker.** Sistemas legados corporativos têm latência e
   limite de taxa. O desenho completo prevê Redis como cache — uma consulta de
   status de fatura pode ficar em cache por alguns minutos, evitando bater no
   BSS a cada mensagem do cliente. Bibliotecas como `tenacity` (retry com
   backoff) evitam que uma instabilidade momentânea do legado derrube a
   conversa inteira.
4. **Fallback de falha.** Se o BSS cair ou demorar, o agente precisa devolver
   algo como "não consegui consultar sua fatura agora, tenta em alguns
   minutos" em vez de travar — o mesmo padrão de tratamento de exceção que já
   usamos em `call_ollama_chat()` para o LLM, aplicado aos sistemas legados.

### WhatsApp real (Meta Cloud API)

Aqui o gargalo é burocrático, não técnico:

1. **Aprovação da Meta (o que mais demora).** Precisa de uma conta Meta
   Business verificada + WhatsApp Business Account (WABA) associada a um
   número da Claro. A verificação de identidade empresarial pode levar dias a
   semanas. Muita empresa brasileira terceiriza isso via um **BSP** (Business
   Solution Provider) como Twilio, Zenvia ou Take Blip, que já têm a
   homologação pronta — o Documento de Visão já cita isso como alternativa
   ("Meta Cloud API ou BSP homologado").
2. **Receber mensagem (webhook de entrada).** A mensagem do cliente chega no
   WhatsApp → a Meta manda um `POST` (webhook) para uma URL pública HTTPS sua,
   por exemplo `https://claro-unify.com/webhooks/whatsapp`. Esse endpoint
   precisa validar a assinatura (header `X-Hub-Signature-256`) para garantir
   que a chamada veio mesmo da Meta, não de alguém se passando por ela.
3. **Processar com o mesmo núcleo.** Depois de validado, o webhook extrai o
   número de telefone e o texto, e chama a **mesma função interna** que hoje
   processa `/api/chat` (`classify_intent_hybrid` → `compose_reply`). Nenhuma
   lógica de negócio muda — só a "porta de entrada" troca de um `fetch()` do
   navegador para um webhook da Meta.
4. **Enviar resposta (Graph API).** Para responder, o backend faz um `POST`
   autenticado (Bearer token) para `POST /v18.0/{numero-id}/messages` na Graph
   API. É isso que substitui o `session["history"].append(...)` que hoje só
   grava no banco — em produção, precisa *realmente* mandar a mensagem de
   volta pro WhatsApp do cliente.
5. **Regra das 24 horas (a pegadinha mais importante).** A Meta só permite
   texto livre dentro de 24h da última mensagem do cliente. Fora dessa janela
   — exatamente o caso da nossa funcionalidade **Iniciar Contato** (retenção,
   fatura vencendo, upgrade) — é **obrigatório** usar um Template de Mensagem
   pré-aprovado pela Meta. Isso significa que cada `motivo` que implementamos
   em `MOTIVOS_CONTATO` (`fatura_vencendo`, `retencao`, `upgrade_oferta`)
   precisaria virar um template registrado e aprovado antes de usar em
   produção — a lógica de decidir a mensagem já está pronta, só a forma de
   envio mudaria de texto livre para template aprovado.

## Próximos passos para evoluir rumo à arquitetura completa

1. Trocar `classify_intent` por uma chamada real a GPT-4o/Gemini (a assinatura da
   função já retorna `(intent, confidence)`, então a troca é isolada) — ou manter o
   Qwen3 4B local, que já está integrado e é gratuito.
2. Trocar o SQLite (`db.py`) por Postgres + Redis, para múltiplas réplicas do
   orquestrador acessarem o mesmo estado concorrentemente (hoje sessões e CRM já
   são persistidos, só não são distribuídos entre instâncias).
3. Trocar `events` (lista em memória) por publicação em Kafka/Pub-Sub.
4. Conectar o canal WhatsApp real via Meta Cloud API (ver seção acima), mantendo
   o mesmo núcleo de processamento de `/api/chat`.
5. Trocar as consultas de `db.py` por clientes HTTP para as APIs reais de
   BSS/OSS/CRM (ver seção acima).
6. Trocar o token opaco de `/api/auth/silent` por OAuth 2.0/OIDC real com um
   provedor de identidade (IdP) — a interface (emitir token, resolver customer_id
   a partir dele) já está pronta, só a implementação interna mudaria.
7. Adicionar o canal App Mobile (RF001/RF008), hoje fora do escopo do MVP.
8. Rodar um teste de carga real (locust/k6) para validar RNF001/RNF003 sob
   concorrência, não só sequencialmente como hoje.
9. Adicionar CI (GitHub Actions) rodando `pytest` a cada push — a suíte já existe
   e roda em menos de 1s, falta só o workflow.

