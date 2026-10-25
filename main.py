"""
ClaroUnify Hub — MVP funcional (v2)
Orquestrador conversacional omnicanal.

Simplificações conscientes em relação à arquitetura completa (documentada no Sprint 2):
- NLU: tenta o Qwen3 4B rodando local via Ollama (gratuito, sem chave de API); se o
  Ollama não estiver instalado/rodando, cai automaticamente para o classificador por
  palavras-chave (multi-intenção + sinais de urgência) — a demo nunca quebra por
  causa disso. Ver README para instalar o Ollama e baixar o modelo.
- Redis/PostgreSQL substituídos por memória de processo (dict) — Context Manager
  funcional, não persistente nem distribuído entre múltiplas instâncias.
- Kafka/Pub-Sub substituído por uma lista de eventos em memória, consumida pelo Dashboard.
- WhatsApp simulado na própria UI (não usa Meta Cloud API real).
- BSS/OSS/CRM/Catálogo são dados mockados fixos.
- Handoff usa um Connector plugável: hoje só grava na fila interna (MockConnector), mas
  já existe o ponto de extensão para enviar a um sistema real (Zendesk/Salesforce/etc)
  via webhook — ver HandoffConnector mais abaixo.
- LGPD: mascaramento de dado exibido, registro de consentimento e rate limiting são
  reais e funcionais nesta versão. Retenção/expiração automática de dados e auditoria
  completa de acesso por perfil ainda não estão implementadas (ver README).

Toda a lógica de roteamento, handoff, continuidade entre canais e KPIs é real e funcional.
"""

import json
import hmac
import hashlib
import secrets
import jwt
from datetime import datetime, timedelta, timezone
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import logging
import os
import re
import time
import uuid
from collections import deque
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Response, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.types import Scope
from pydantic import BaseModel, Field

import db

# Carrega variáveis de um .env na raiz do projeto, se existir — não sobrescreve
# variáveis já definidas no ambiente (ex.: pelo docker-compose). Ver .env.example
# para a lista completa de variáveis suportadas.
load_dotenv()

# ---------------------------------------------------------------------------
# Logging estruturado — nível configurável via LOG_LEVEL, com timestamp e
# módulo, em vez de deixar erros passarem em silêncio.
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("clarounify")

app = FastAPI(title="ClaroUnify Hub — MVP")


# v4 + v5: JWTs duráveis entre reinicializações e isolamento opcional do cliente.
# Ative CUSTOMER_AUTH_REQUIRED apenas após conectar um provedor de identidade real.
JWT_SECRET = os.environ.get("JWT_SECRET", "")
CUSTOMER_AUTH_REQUIRED = os.environ.get("CUSTOMER_AUTH_REQUIRED", "false").lower() == "true"
TOKEN_TTL_S = int(os.environ.get("TOKEN_TTL_S", "3600"))

def _customer_claims(request: Request):
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    if len(JWT_SECRET) < 32:
        raise HTTPException(503, "JWT_SECRET precisa conter pelo menos 32 caracteres")
    try:
        claims = jwt.decode(header.split(" ", 1)[1], JWT_SECRET, algorithms=["HS256"], options={"require": ["sub", "exp"]})
    except jwt.PyJWTError:
        raise HTTPException(401, "Token do cliente expirado ou inválido")
    if claims.get("role") != "customer":
        raise HTTPException(403, "Perfil não autorizado")
    return claims

def _check_customer(request: Request, customer_id: str):
    claims = _customer_claims(request)
    if claims is not None:
        if claims["sub"] != customer_id:
            raise HTTPException(403, "Acesso permitido somente aos próprios dados")
    elif CUSTOMER_AUTH_REQUIRED:
        # O cookie admin não substitui a identificação segura do cliente final.
        require_admin(request)

def record_audit(action: str, target: str = "", request: Request = None):
    actor = "admin" if request is None else "admin-cookie"
    db.audit("admin", actor, action, target)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response

@app.exception_handler(HTTPException)
async def http_error_json(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)

# Admin: password from Railway Variables, signed HttpOnly session cookie.
# No insecure default credential: an unset password means admin is unavailable.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_SESSION_SECRET = os.environ.get("ADMIN_SESSION_SECRET", "")
ADMIN_AUTH_DISABLED = os.environ.get("ADMIN_AUTH_DISABLED", "false").lower() == "true" and os.environ.get("APP_ENV") == "test"
ADMIN_COOKIE = "clarounify_admin"

def _admin_serializer():
    if not ADMIN_PASSWORD or len(ADMIN_SESSION_SECRET) < 32:
        raise HTTPException(503, "Configure ADMIN_PASSWORD e ADMIN_SESSION_SECRET (mínimo 32 caracteres).")
    return URLSafeTimedSerializer(ADMIN_SESSION_SECRET, salt="clarounify-admin-v1")

def require_admin(request: Request):
    if ADMIN_AUTH_DISABLED:
        return
    serializer = _admin_serializer()
    token = request.cookies.get(ADMIN_COOKIE, "")
    try:
        if serializer.loads(token, max_age=8 * 3600) != "admin":
            raise HTTPException(401, "Acesso administrativo não autorizado")
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Faça login no Painel Claro")

class AdminLoginIn(BaseModel):
    password: str

@app.post("/api/admin/login")
def admin_login(payload: AdminLoginIn, request: Request, response: Response):
    serializer = _admin_serializer()
    # Both branches perform a PBKDF2 derivation; avoid an easily measurable cheap failure path.
    supplied = hashlib.pbkdf2_hmac("sha256", payload.password.encode(), b"clarounify-login", 160000)
    expected = hashlib.pbkdf2_hmac("sha256", ADMIN_PASSWORD.encode(), b"clarounify-login", 160000)
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Senha incorreta")
    response.set_cookie(ADMIN_COOKIE, serializer.dumps("admin"), max_age=28800,
                        httponly=True, secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
                        samesite="strict", path="/")
    return {"ok": True}

@app.post("/api/admin/logout")
def admin_logout(response: Response):
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}

@app.get("/api/admin/me")
def admin_me(request: Request):
    require_admin(request)
    return {"authenticated": True}

@app.middleware("http")
async def protect_admin_routes(request: Request, call_next):
    path = request.url.path
    protected = (path.startswith("/api/admin/") and path not in ("/api/admin/login", "/api/admin/me", "/api/admin/logout")) or path in (
        "/api/kpis", "/api/alertas", "/api/events", "/api/handoff-queue", "/api/reset", "/api/capacidade", "/api/llm-status", "/api/campanha-proativa"
    ) or path.startswith(("/api/handoff-queue/", "/api/lgpd/", "/api/campanha-proativa/"))
    if protected:
        try:
            require_admin(request)
        except HTTPException as exc:
            return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
    # Reject cross-origin state-changing requests (SameSite cookie is additional defense).
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and protected:
        origin = request.headers.get("origin")
        if origin:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            host = request.headers.get("host", "")
            if parsed.netloc != host or parsed.scheme not in ("http", "https"):
                return JSONResponse({"error": "Origem não autorizada"}, status_code=403)
    return await call_next(request)

@app.get("/api/health")
@app.get("/health")
def health():
    try:
        connection = db.get_db()
        connection.execute("SELECT 1")
        connection.close()
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Banco de dados indisponível")

@app.get("/api/demo/clientes")
def demo_clientes():
    # Fixture identities only; never use with real customer data.
    return {"items": [{"id": c["id"], "nome": c["nome"]} for c in db.list_customers(limit=100)["items"]]}

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Rede de segurança: erro não tratado vira um JSON de erro consistente
    (nunca um HTML de stack trace cru), logado com contexto da rota. O front
    depende desse formato para mostrar o toast de erro (ver apiFetch em
    static/app.js)."""
    logger.exception("Erro não tratado em %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Ocorreu um erro inesperado no servidor. Tente novamente em instantes."},
    )

# ---------------------------------------------------------------------------
# RNF001 — Tempo de Resposta: mede a latência REAL de cada /api/chat (não é
# estimativa) via middleware, para reportar honestamente se o P95 real (com o
# LLM ligado ou não) está dentro da meta de 2s definida no Documento de Visão.
# ---------------------------------------------------------------------------

_chat_latencies_ms: deque = deque(maxlen=200)


@app.middleware("http")
async def measure_chat_latency(request, call_next):
    if request.url.path == "/api/chat":
        t0 = time.perf_counter()
        response = await call_next(request)
        _chat_latencies_ms.append((time.perf_counter() - t0) * 1000)
        return response
    return await call_next(request)

# ---------------------------------------------------------------------------
# RF005 — Autenticação Silenciosa por Canal (SSO simulado)
# ---------------------------------------------------------------------------
#
# Simula o comportamento exigido pelo RF005: ao trocar de canal, o cliente não deve
# reaparecer como "deslogado" nem precisar se reidentificar. Aqui isso é feito com um
# token opaco emitido silenciosamente (sem tela de login) na primeira interação —
# equivalente em espírito a um OAuth 2.0/OIDC real, mas sem provedor de identidade
# externo (fora de escopo para o MVP). O token é apenas um proxy pro customer_id;
# num ambiente real ele carregaria claims assinadas e expiração.

AUTH_TOKENS: dict = {}  # token -> {"customer_id": ..., "issued_at": ..., "channel_origem": ...}


def issue_silent_token(customer_id: str, channel: str) -> str:
    if len(JWT_SECRET) >= 32:
        now = int(time.time())
        return jwt.encode({"sub": customer_id, "ch": channel, "iat": now, "exp": now + TOKEN_TTL_S,
                           "jti": uuid.uuid4().hex, "role": "customer"}, JWT_SECRET, algorithm="HS256")
    token = uuid.uuid4().hex
    AUTH_TOKENS[token] = {"customer_id": customer_id, "issued_at": time.time(), "channel_origem": channel}
    return token


def resolve_token(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    if len(JWT_SECRET) >= 32:
        try:
            claims = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"require": ["sub", "exp"]})
            return claims["sub"] if claims.get("role") == "customer" else None
        except jwt.PyJWTError:
            return None
    entry = AUTH_TOKENS.get(token)
    return entry["customer_id"] if entry else None


# ---------------------------------------------------------------------------
# RNF006 — Observabilidade: trace ID por jornada + alertas automáticos
# ---------------------------------------------------------------------------
# (o trace_id em si agora é gerado dentro de db.get_or_create_session — ver db.py)

ALERT_THRESHOLDS = {
    "volume_handoff_pct_max": float(os.environ.get("ALERT_VOLUME_HANDOFF_PCT_MAX", "40.0")),
    "taxa_resolucao_pct_min": float(os.environ.get("ALERT_TAXA_RESOLUCAO_PCT_MIN", "50.0")),
    "rate_limit_bloqueios_max": int(os.environ.get("ALERT_RATE_LIMIT_BLOQUEIOS_MAX", "3")),
}

# ---------------------------------------------------------------------------
# LLM (Qwen3 4B via Ollama — 100% gratuito, roda local, sem chave de API)
# ---------------------------------------------------------------------------
#
# Se o Ollama não estiver instalado/rodando na máquina, cada chamada abaixo falha
# silenciosamente (timeout curto) e o sistema cai automaticamente no motor por
# palavras-chave — a demo nunca fica travada esperando um LLM que não existe.

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b")
LLM_TIMEOUT_S = float(os.environ.get("LLM_TIMEOUT_S", "20"))
USE_LLM = os.environ.get("USE_LLM", "true").lower() in ("1", "true", "yes")
_llm_warmed = False  # vira True depois da 1ª chamada bem-sucedida (modelo já carregado na VRAM)


def strip_thinking(text: str) -> str:
    """Remove qualquer resquício de raciocínio interno que vaze na resposta.

    Mesmo com `"think": false` no payload, alguns modelos (Qwen3 incluso, em
    certas versões do Ollama) ignoram esse parâmetro e devolvem o bloco de
    pensamento junto com a resposta final — foi exatamente isso que apareceu no
    chat em produção. Esta função é uma segunda camada de defesa, independente
    do parâmetro funcionar ou não:
    1) remove qualquer bloco <think>...</think> completo;
    2) se sobrar um "</think>" solto (pensamento sem a tag de abertura visível),
       descarta tudo antes dele e fica só com o texto final.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    if "</think>" in text.lower():
        idx = text.lower().rfind("</think>")
        text = text[idx + len("</think>"):]
    return text.strip()


def call_ollama_chat(messages: list, json_mode: bool = False) -> Optional[str]:
    global _llm_warmed
    # Enquanto o modelo ainda não foi carregado na VRAM (1ª chamada), o Ollama pode
    # demorar bem mais que o normal — damos uma margem extra só nesse momento.
    timeout = LLM_TIMEOUT_S if _llm_warmed else max(LLM_TIMEOUT_S, 60.0)
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            # Qwen3 (e outros modelos de raciocínio) geram um bloco de "pensamento"
            # interno enorme antes da resposta final por padrão — isso sozinho já
            # estourava os 30s de timeout gerando +2000 tokens. Desligar o "think"
            # aqui é o que faz a resposta sair em poucos segundos em vez de travar.
            # ATENÇÃO: nem sempre é respeitado pelo modelo/versão do Ollama — por
            # isso strip_thinking() abaixo é uma segunda camada de proteção, não
            # opcional.
            "think": False,
        }
        if json_mode:
            payload["format"] = "json"
        resp = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
        resp.raise_for_status()
        _llm_warmed = True
        content = resp.json().get("message", {}).get("content")
        return strip_thinking(content) if content else content
    except Exception as exc:
        logger.warning("Chamada ao Ollama falhou, caindo no fallback por palavras-chave: %s", exc)
        return None


def check_llm_status() -> dict:
    try:
        resp = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2.0)
        resp.raise_for_status()
        modelos = [m.get("name", "") for m in resp.json().get("models", [])]
        encontrado = any(OLLAMA_MODEL.split(":")[0] in m for m in modelos)
        return {
            "ollama_rodando": True,
            "modelo_configurado": OLLAMA_MODEL,
            "modelo_encontrado": encontrado,
            "modelos_disponiveis": modelos,
            "modo_ativo": "llm" if (USE_LLM and encontrado) else "palavras-chave (fallback)",
        }
    except Exception as exc:
        return {
            "ollama_rodando": False,
            "modelo_configurado": OLLAMA_MODEL,
            "erro": str(exc),
            "modo_ativo": "palavras-chave (fallback)",
        }


class NoCacheStaticFiles(StaticFiles):
    """Evita que o navegador guarde em cache uma versão antiga do JS/CSS durante
    o desenvolvimento — cada F5 sempre pega o arquivo mais recente do disco."""
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

# ---------------------------------------------------------------------------
# "Backends" BSS / OSS / CRM / Catálogo — agora vêm do banco (db.py), não mais
# de dicionários hardcoded. Ver db.py para o schema e os dados de seed.
# ---------------------------------------------------------------------------

DEFAULT_CUSTOMER = os.environ.get("DEFAULT_CUSTOMER", "11999990001")


def get_customer(customer_id: str) -> str:
    return customer_id if db.get_customer_record(customer_id) else DEFAULT_CUSTOMER


# ---------------------------------------------------------------------------
# LGPD — mascaramento, consentimento, portabilidade/exclusão
# ---------------------------------------------------------------------------

def mask_id(customer_id: str) -> str:
    """Mascara o identificador do cliente para exibição (dashboard, logs, handoff-queue).
    Mantém os 4 últimos dígitos, conforme prática comum de minimização de dados."""
    if len(customer_id) <= 4:
        return "*" * len(customer_id)
    return "*" * (len(customer_id) - 4) + customer_id[-4:]


def mask_name(full_name: str) -> str:
    """Mostra primeiro nome + inicial do sobrenome — suficiente para o atendente
    identificar humanamente sem expor o nome completo em telas de log/dashboard."""
    parts = full_name.split(" ")
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


# Preserve mensagens antigas para que o aceite após uma atualização do projeto
# ainda processe a pergunta feita antes da atualização.
LEGACY_CONSENT_TEXT = (
    "Para te atender, preciso registrar esta conversa e consultar seus dados de conta "
    "(fatura, plano, status de rede) conforme a LGPD. Posso continuar?"
)
CONSENT_TEXT = (
    "Para continuar seu atendimento, preciso salvar esta conversa e consultar "
    "os dados necessários da sua conta, como plano, faturas e situação da rede. "
    "Usarei essas informações para analisar sua solicitação, conforme a LGPD. "
    "Você autoriza?"
)
CONSENT_PROMPT_TEXTS = (CONSENT_TEXT, LEGACY_CONSENT_TEXT)


# ---------------------------------------------------------------------------
# Rate limiting simples (mitigação de abuso — não é substituto de WAF/API Gateway real)
# ---------------------------------------------------------------------------

RATE_LIMIT_MAX_MSGS = int(os.environ.get("RATE_LIMIT_MAX_MSGS", "20"))
RATE_LIMIT_WINDOW_S = int(os.environ.get("RATE_LIMIT_WINDOW_S", "60"))
_rate_log: dict = {}  # customer_id -> deque[timestamps]


def check_rate_limit(customer_id: str) -> bool:
    """Retorna True se a requisição pode prosseguir; False se estourou o limite."""
    now = time.time()
    dq = _rate_log.setdefault(customer_id, deque())
    while dq and now - dq[0] > RATE_LIMIT_WINDOW_S:
        dq.popleft()
    if len(dq) >= RATE_LIMIT_MAX_MSGS:
        return False
    dq.append(now)
    return True


# ---------------------------------------------------------------------------
# Context Manager (em memória — substitui Redis+PostgreSQL do desenho real)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Context Manager — agora persistido em SQLite (ver db.Session em db.py), não
# mais um dicionário em memória. A interface session["campo"] continua idêntica;
# só sobrevive a um restart do servidor agora, que é o ponto do RF002.
# ---------------------------------------------------------------------------

events: list = []  # log de eventos permanece em memória (ver README: simplificação documentada, substitui Kafka/Pub-Sub)


def get_session(customer_id: str) -> db.Session:
    return db.get_or_create_session(customer_id)


def log_event(event_type: str, customer_id: str, **kwargs):
    trace_id = db.get_trace_id(customer_id)
    events.append({
        "id": str(uuid.uuid4()),
        "type": event_type,
        "customer_id": customer_id,                    # mantido em claro internamente (necessário operacionalmente)
        "customer_id_masked": mask_id(customer_id),     # o que a UI deve exibir
        "trace_id": trace_id,
        "ts": time.time(),
        **kwargs,
    })


# ---------------------------------------------------------------------------
# Módulo NLU — multi-intenção por palavras-chave + sinais de urgência/frustração
# ---------------------------------------------------------------------------
#
# Limitação assumida: isto ainda não é NLU semântico (não generaliza para frases nunca
# vistas). O que foi endereçado da revisão: (1) uma mensagem pode conter mais de uma
# intenção e as duas são reportadas; (2) sinais de reclamação recorrente/urgência
# ("de novo", "terceira vez") aumentam a prioridade e podem forçar handoff mesmo sem
# a palavra "cancelar" — porque cliente frustrado que não fala "cancelar" ainda é
# risco de churn.

INTENT_KEYWORDS = {
    "cancelamento": ["cancelar", "cancelamento", "encerrar plano", "não quero mais"],
    "suporte": ["internet", "caindo", "sem sinal", "lenta", "não funciona", "problema", "defeito", "conexão", "wifi", "wi-fi", "net"],
    "venda": ["plano", "planos", "contratar", "assinar", "upgrade", "comprar", "quero conhecer", "mais dados", "desconto"],
    "informacao": ["fatura", "boleto", "segunda via", "vencimento", "pagar", "conta", "valor"],
}

URGENCY_SIGNALS = ["de novo", "novamente", "terceira vez", "toda semana", "todo dia", "não aguento", "já liguei", "já reclamei", "há dias", "há semanas"]

HANDOFF_TRIGGERS = ["falar com atendente", "atendente humano", "quero um humano", "falar com humano"]
GREETINGS = ["oi", "olá", "ola", "bom dia", "boa tarde", "boa noite", "eae", "e ai", "e aí", "opa", "hey", "oii"]

# RF006 — sinais de que o cliente considera o assunto encerrado. Usado para
# disparar a pesquisa de satisfação (NPS) no momento certo da conversa, em vez
# de interromper o cliente no meio do atendimento ou nunca perguntar nada.
CLOSING_SIGNALS = [
    "obrigado", "obrigada", "valeu", "brigado", "brigada", "ok obrigado", "ok, obrigado",
    "isso ajudou", "isso resolveu", "resolveu", "era isso", "só isso", "por hoje é só",
    "muito obrigado", "muito obrigada", "perfeito", "beleza então", "ajudou muito",
]

INTENT_PRIORITY = ["cancelamento", "suporte", "venda", "informacao"]


def classify_intent(text: str) -> dict:
    """Retorna um dicionário com a intenção primária, confiança, todas as intenções
    detectadas (para mensagens compostas) e um flag de urgência/frustração."""
    t = text.lower().strip()

    for phrase in HANDOFF_TRIGGERS:
        if phrase in t:
            return {"intent": "handoff_explicito", "confidence": 0.99, "all_intents": ["handoff_explicito"], "urgent": False}

    if t in GREETINGS or any(t.startswith(g) for g in GREETINGS):
        return {"intent": "saudacao", "confidence": 0.95, "all_intents": ["saudacao"], "urgent": False}

    scores = {}
    for intent, keywords in INTENT_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in t)
        if hits:
            scores[intent] = min(0.55 + 0.15 * hits, 0.97)

    urgent = any(sig in t for sig in URGENCY_SIGNALS)

    if not scores:
        return {"intent": "nao_entendido", "confidence": 0.35, "all_intents": [], "urgent": urgent}

    # intenção primária: por prioridade de negócio (churn > suporte > venda > info),
    # não só pela pontuação bruta — uma reclamação de suporte pesa mais que uma
    # menção incidental a "plano" na mesma frase.
    detected = sorted(scores.keys(), key=lambda i: INTENT_PRIORITY.index(i) if i in INTENT_PRIORITY else 99)
    primary = detected[0]

    return {
        "intent": primary,
        "confidence": scores[primary],
        "all_intents": detected,
        "urgent": urgent,
    }


VALID_INTENTS = {"cancelamento", "suporte", "venda", "informacao", "saudacao", "handoff_explicito", "nao_entendido"}


def classify_intent_llm(text: str) -> Optional[dict]:
    """Pede ao Qwen3 4B (via Ollama) para classificar a mensagem. Retorna None se o
    modelo não respondeu ou respondeu algo que não conseguimos interpretar como JSON
    válido — nesses casos quem chama cai para classify_intent (palavras-chave)."""
    system = (
        "Você é o módulo de NLU do ClaroUnify, uma operadora de telecom brasileira. "
        "Classifique a MENSAGEM DO CLIENTE em uma intenção primária dentre exatamente: "
        "cancelamento, suporte, venda, informacao, saudacao, handoff_explicito, nao_entendido. "
        "'cancelamento' = quer cancelar/encerrar o plano. 'suporte' = problema técnico "
        "(internet, sinal, conexão). 'venda' = quer conhecer/contratar planos. "
        "'informacao' = fatura, boleto, pagamento. 'saudacao' = só um cumprimento. "
        "'handoff_explicito' = pediu para falar com atendente humano. "
        "'nao_entendido' = não deu pra entender o que o cliente quer. "
        "Também retorne all_intents (lista com todas as intenções presentes na frase, "
        "pode ter mais de uma) e urgent (true se houver sinal de reclamação recorrente, "
        "como 'de novo', 'já liguei antes', 'toda semana', 'terceira vez'). "
        'Responda SOMENTE com um JSON válido, sem explicações: '
        '{"intent": "...", "confidence": 0.0-1.0, "all_intents": ["..."], "urgent": true|false}'
    )
    content = call_ollama_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": text}],
        json_mode=True,
    )
    if not content:
        return None
    try:
        parsed = json.loads(content)
        if parsed.get("intent") not in VALID_INTENTS:
            return None
        parsed.setdefault("all_intents", [parsed["intent"]])
        parsed.setdefault("confidence", 0.8)
        parsed.setdefault("urgent", False)
        return parsed
    except Exception as exc:
        logger.warning("Resposta do LLM não era JSON válido, caindo no fallback: %s", exc)
        return None


def classify_intent_hybrid(text: str) -> dict:
    """Tenta o LLM primeiro; cai para o classificador por palavras-chave se o Ollama
    não estiver rodando ou responder algo inválido. Marca a origem em '_source' para
    aparecer no log de eventos (transparência sobre qual motor respondeu)."""
    if USE_LLM:
        result = classify_intent_llm(text)
        if result is not None:
            result["_source"] = "llm"
            return result
    result = classify_intent(text)
    result["_source"] = "keywords"
    return result


# ---------------------------------------------------------------------------
# Agentes especializados
# ---------------------------------------------------------------------------
#
# Cada agente devolve (fatos, resposta_padrão). Os "fatos" vêm sempre do mock de
# BSS/OSS/CRM/Catálogo — nunca do LLM. Isso é o que impede o modelo de "inventar"
# valor de fatura, prazo de rede etc.: ele só pode reescrever de forma mais natural
# os fatos que já buscamos do backend, nunca criar fatos novos.

def agente_vendas(customer_id: str, text: str, extra_intents=None) -> tuple[str, str]:
    opcoes = "\n".join(f"• {p['nome']} — R$ {p['preco']:.2f} — {p['descricao']}" for p in db.get_catalog())
    template = (
        "Consultei o Catálogo de Produtos para você. Estas são as opções compatíveis com seu perfil:\n"
        f"{opcoes}\n\nQuer que eu inicie a contratação de alguma delas?"
    )
    fatos = "Catálogo disponível: " + "; ".join(f"{p['nome']} por R$ {p['preco']:.2f} ({p['descricao']})" for p in db.get_catalog())
    return fatos, template


def agente_suporte(customer_id: str, text: str, extra_intents=None) -> tuple[str, str]:
    oss = db.get_network_status(customer_id) or {"status_rede": "normal", "regiao": "-", "previsao_normalizacao": None}
    if oss["status_rede"] == "instabilidade":
        template = (
            f"Consultei o OSS: detectei instabilidade na {oss['regiao']}. "
            f"Previsão de normalização: {oss['previsao_normalizacao']}."
        )
        fatos = f"Status de rede: instabilidade detectada em {oss['regiao']}. Previsão de normalização: {oss['previsao_normalizacao']}."
    else:
        template = "Consultei o OSS e não encontrei nenhuma instabilidade na sua região no momento. Pode me dar mais detalhes do problema?"
        fatos = "Status de rede: normal, nenhuma instabilidade detectada na região do cliente."
    if extra_intents and "informacao" in extra_intents:
        template += " Também vi que você mencionou a fatura — posso puxar os detalhes dela em seguida, se precisar."
        fatos += " O cliente também mencionou a fatura na mesma mensagem."
    return fatos, template


def agente_info(customer_id: str, text: str, extra_intents=None) -> tuple[str, str]:
    bss = db.get_invoice(customer_id)
    if not bss:
        template = "Não encontrei fatura em aberto no BSS para o seu cadastro."
        fatos = "Nenhuma fatura encontrada no BSS para este cliente."
    elif bss["status"] == "paga":
        template = f"Sua última fatura de R$ {bss['fatura_valor']:.2f} já está paga. Precisa da segunda via mesmo assim?"
        fatos = f"Fatura de R$ {bss['fatura_valor']:.2f} já está com status pago."
    else:
        template = (
            f"Sua fatura de R$ {bss['fatura_valor']:.2f} vence em {bss['vencimento']}. "
            "Posso te ajudar com o pagamento por aqui mesmo."
        )
        fatos = f"Fatura em aberto: R$ {bss['fatura_valor']:.2f}, vencimento em {bss['vencimento']}."
    if extra_intents and "venda" in extra_intents:
        template += " E se quiser, também posso te mostrar planos com mais dados enquanto isso."
        fatos += " O cliente também mencionou interesse em planos na mesma mensagem."
    return fatos, template


def agente_saudacao(customer_id: str, text: str, extra_intents=None) -> tuple[str, str]:
    crm = db.get_customer_record(customer_id) or {}
    nome = crm.get("nome", "").split(" ")[0]
    saudacao = f"Oi, {nome}! " if nome else "Oi! "
    template = saudacao + "Posso ajudar com planos, suporte técnico ou sua fatura. O que você precisa hoje?"
    fatos = f"Nome do cliente: {nome or 'não identificado'}. O bot pode ajudar com: planos, suporte técnico, fatura."
    return fatos, template


def agente_nao_entendido(customer_id: str, text: str, extra_intents=None) -> tuple[str, str]:
    template = (
        "Não tenho certeza se entendi. Posso ajudar com: planos e contratação, "
        "problemas de internet/sinal, ou fatura e pagamento. Pode me contar com suas palavras?"
    )
    fatos = "O sistema não conseguiu identificar a intenção da mensagem do cliente."
    return fatos, template


AGENTS = {
    "venda": ("Agente de Vendas", agente_vendas),
    "suporte": ("Agente de Suporte", agente_suporte),
    "informacao": ("Agente de Informações", agente_info),
    "saudacao": ("Assistente ClaroUnify", agente_saudacao),
    "nao_entendido": ("Assistente ClaroUnify", agente_nao_entendido),
}


def llm_rewrite_reply(agent_name: str, user_text: str, fatos: str, template_reply: str) -> Optional[str]:
    """Pede ao LLM para reescrever a resposta padrão de forma mais natural, mas
    proibido de usar qualquer fato que não esteja em 'fatos' — isso é o que evita
    alucinação de valores de fatura, prazos, planos etc."""
    system = (
        f"Você é o {agent_name} do ClaroUnify, assistente virtual da Claro Brasil. "
        "Responda ao cliente em português brasileiro, tom cordial e objetivo, em no "
        "máximo 3 frases curtas. Use SOMENTE os fatos abaixo — nunca invente valor, "
        "prazo, plano ou promessa que não esteja neles. Se não houver fato suficiente, "
        "diga isso educadamente.\n"
        f"FATOS DISPONÍVEIS: {fatos}"
    )
    content = call_ollama_chat([
        {"role": "system", "content": system},
        {"role": "user", "content": user_text},
    ])
    if content:
        return content.strip()
    return None


def is_closing_signal(text: str) -> bool:
    """RF006 — detecta se o cliente sinalizou que o assunto está encerrado, para
    disparar a pesquisa de satisfação no momento certo (não a cada mensagem)."""
    t = text.lower().strip()
    return any(sig in t for sig in CLOSING_SIGNALS)


NPS_PROMPT_TEXT = (
    "Fico feliz em ajudar! De 0 a 10, o quanto você recomendaria o atendimento da Claro "
    "para um amigo ou familiar?"
)


def compose_reply(intent: str, customer_id: str, text: str, extra_intents=None) -> tuple[str, str]:
    """Retorna (nome_do_agente, texto_da_resposta), tentando o LLM e caindo para o
    template determinístico se o LLM estiver indisponível ou desabilitado."""
    agent_name, agent_fn = AGENTS.get(intent, AGENTS["informacao"])
    fatos, template_reply = agent_fn(customer_id, text, extra_intents)
    reply = template_reply
    if USE_LLM:
        llm_reply = llm_rewrite_reply(agent_name, text, fatos, template_reply)
        if llm_reply:
            reply = llm_reply
    return agent_name, reply


# ---------------------------------------------------------------------------
# Handoff Connector — ponto de extensão para sistema de atendimento real
# ---------------------------------------------------------------------------
#
# Hoje só a fila interna (MockHandoffConnector) está ativa. Para plugar um sistema
# real (Zendesk, Salesforce Service Cloud, Freshdesk etc.), basta implementar
# WebhookHandoffConnector.send() apontando pra URL de criação de ticket daquele
# sistema e trocar HANDOFF_CONNECTOR abaixo — o resto do fluxo não muda.

class HandoffConnector:
    def send(self, ticket: dict) -> dict:
        raise NotImplementedError


class MockHandoffConnector(HandoffConnector):
    """Só registra na fila interna (comportamento atual do MVP)."""
    def send(self, ticket: dict) -> dict:
        return {"ok": True, "sistema": "fila_interna_mvp"}


class WebhookHandoffConnector(HandoffConnector):
    """Pronto para produção: envia o ticket para uma URL de webhook externa
    (Zendesk/Salesforce/Freshdesk/etc). Requer HANDOFF_WEBHOOK_URL configurada
    no ambiente — sem isso, cai automaticamente no MockHandoffConnector."""
    def __init__(self, url: str):
        self.url = url

    def send(self, ticket: dict) -> dict:
        try:
            import httpx
            resp = httpx.post(self.url, json=ticket, timeout=5.0)
            return {"ok": resp.status_code < 300, "sistema": "webhook_externo", "status_code": resp.status_code}
        except Exception as exc:
            return {"ok": False, "sistema": "webhook_externo", "erro": str(exc)}


_webhook_url = os.environ.get("HANDOFF_WEBHOOK_URL")
HANDOFF_CONNECTOR: HandoffConnector = WebhookHandoffConnector(_webhook_url) if _webhook_url else MockHandoffConnector()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class ChatIn(BaseModel):
    customer_id: str
    channel: str
    text: str
    auth_token: Optional[str] = None


class AuthIn(BaseModel):
    customer_id: str
    channel: str


@app.post("/api/auth/silent")
def auth_silent(payload: AuthIn, request: Request):
    if CUSTOMER_AUTH_REQUIRED:
        require_admin(request)
    """RF005 — chamado automaticamente pelo frontend ao trocar de canal (nunca pelo
    cliente digitando login/senha). Emite um token opaco que identifica o cliente
    silenciosamente nos canais seguintes, sem repetir autenticação."""
    customer_id = get_customer(payload.customer_id)
    token = issue_silent_token(customer_id, payload.channel)
    log_event("auth_silenciosa", customer_id, canal=payload.channel)
    return {"token": token, "customer_id": customer_id}


class ChatOut(BaseModel):
    customer_id: str
    reply: str
    intent: str
    confidence: float
    agent: Optional[str]
    status: str
    handoff: bool
    needs_consent: bool = False
    ask_nps: bool = False


class NPSIn(BaseModel):
    customer_id: str
    score: int = Field(ge=0, le=10)
    channel: str = "site"


class ConsentIn(BaseModel):
    customer_id: str
    accepted: bool


@app.post("/api/consent")
def give_consent(payload: ConsentIn, request: Request):
    _check_customer(request, payload.customer_id)

    customer_id = get_customer(payload.customer_id)
    session = get_session(customer_id)
    was_accepted = session["consent_given"]
    # A primeira mensagem foi registrada antes de solicitar o aceite, mas ainda
    # não foi processada. Recupera-a do SQLite para sobreviver a um refresh.
    pending = None
    if payload.accepted and not was_accepted:
        history = list(session["history"])
        # Só reproduz mensagens realmente interrompidas pelo aviso de LGPD.
        for idx in range(len(history) - 1, 0, -1):
            item = history[idx]
            if item["author"] == "bot" and item["text"] in CONSENT_PROMPT_TEXTS:
                previous = history[idx - 1]
                if previous["author"] == "cliente" and previous["text"].strip():
                    pending = previous
                break
    session["consent_given"] = payload.accepted
    log_event("consentimento", customer_id, aceito=payload.accepted)
    result = {"ok": True, "processed_previous": False}
    if pending is not None:
        # Processa normalmente (NLU, backend, handoff e NPS), sem registrar
        # novamente a mensagem do cliente ou consumir rate limit de envio.
        response = _process_chat(
            ChatIn(customer_id=customer_id, channel=pending["channel"], text=pending["text"]),
            request, replay_pending=True,
        )
        result.update(processed_previous=True, reply=response.reply, ask_nps=response.ask_nps, intent=response.intent, handoff=response.handoff)
    return result


def contextual_classification(text: str, session: db.Session) -> dict:
    """Recupera intenção somente para respostas curtas ligadas a um assunto anterior conhecido.
    Não supõe contratação, cancelamento nem afirmações que o cliente não fez.
    """
    result = classify_intent_hybrid(text)
    if result["intent"] != "nao_entendido":
        return result
    phrase = " ".join(text.lower().strip(" .!?¡¿").split())
    short_followups = {"sim", "pode ser", "quero essa opção", "quero saber mais", "me explica", "pode explicar", "e o valor", "qual o valor", "continua", "pode continuar", "essa opção"}
    previous = session.get("last_intent")
    if phrase not in short_followups or previous not in {"suporte", "venda", "informacao"}:
        return result
    customer_history = [item for item in session["history"][:-1] if item["author"] == "cliente"]
    if not customer_history:
        return result
    return {"intent": previous, "confidence": 0.72, "all_intents": [previous], "urgent": False, "_source": "contexto"}


def handoff_brief(session: db.Session) -> dict:
    """Resumo extrativo auditável de mensagens do cliente, sem inventar fatos pessoais."""
    questions = [x["text"].strip() for x in session["history"] if x["author"] == "cliente" and x["text"].strip()]
    last = questions[-3:]
    reason = {"cancelamento": "Pedido de cancelamento", "suporte": "Problema técnico",
              "informacao": "Informações de conta", "venda": "Contratação/plano",
              "handoff_explicito": "Solicitação de operador"}.get(session["last_intent"], "Análise da solicitação")
    urgency = session["frustration_score"] >= 2
    priority = "Alta" if urgency or session["last_intent"] == "cancelamento" else "Normal"
    diagnostic_messages = [x["text"] for x in session["history"]
                           if x["author"] == "bot" and x["text"].startswith(DIAGNOSTIC_PREFIX)]
    network_status = None
    if diagnostic_messages:
        try:
            item = json.loads(diagnostic_messages[-1][len(DIAGNOSTIC_PREFIX):])
            network_status = item.get("label")
        except (ValueError, TypeError):
            pass
    return {"reason": reason, "priority": priority,
            "summary": " · ".join(x[:180] for x in last) or "Sem mensagens do cliente registradas",
            "customer_messages": len(questions), "network_diagnostic": network_status}


PLAN_PREFIX = "[[SIMULADOR_PLANOS_V1]]"

class PlanSimulationIn(BaseModel):
    customer_id: str
    channel: str = "site"
    usage: str = "moderado"
    budget: str = "intermediario"

class PlanSelectionIn(BaseModel):
    customer_id: str
    channel: str = "site"
    plan_name: str


def simulate_plans(customer_id: str, usage: str, budget: str) -> dict:
    if usage not in ("leve", "moderado", "intenso", "residencial") or budget not in ("economico", "intermediario", "premium"):
        raise HTTPException(422, "Perfil ou orçamento inválido")
    catalog = db.get_catalog()
    if not catalog:
        return {"plans": [], "recommended": None, "current_plan": (db.get_customer_record(customer_id) or {}).get("plano"),
                "usage": usage, "budget": budget, "notice": "Catálogo demonstrativo sem ofertas cadastradas"}
    desired = 40 if usage == "leve" else 80
    def score(plan):
        name = plan["nome"].lower()
        residential = "fibra" in name
        capacity = 500 if residential else (80 if "80gb" in name else 40)
        target_match = 0 if (usage == "residencial") == residential else 1000
        demand_gap = 0 if residential else max(0, desired - capacity) * (3 if usage == "intenso" else 1)
        price_factor = {"economico": 2.0, "intermediario": 0.7, "premium": 0.1}[budget]
        return (target_match + demand_gap + float(plan["preco"]) * price_factor, float(plan["preco"]), name)
    ordered = sorted(catalog, key=score)
    return {"plans": ordered, "recommended": ordered[0]["nome"],
            "current_plan": (db.get_customer_record(customer_id) or {}).get("plano"),
            "usage": usage, "budget": budget, "notice": "Preços e planos fictícios do catálogo demonstrativo; nenhuma contratação é efetuada."}

@app.post("/api/plans/simulate")
def plans_simulate(payload: PlanSimulationIn, request: Request):
    _check_customer(request, payload.customer_id)
    if payload.channel not in ("site", "whatsapp"):
        raise HTTPException(422, "Canal inválido")
    cid = get_customer(payload.customer_id)
    session = get_session(cid)
    if not session["consent_given"]:
        raise HTTPException(403, "Aceite a LGPD antes de consultar seus planos")
    result = simulate_plans(cid, payload.usage, payload.budget)
    session["history"].append({"author": "bot", "channel": payload.channel,
                               "text": PLAN_PREFIX + json.dumps(result, ensure_ascii=False), "ts": time.time()})
    session["updated_at"] = time.time()
    log_event("simulacao_planos", cid, perfil=payload.usage, orcamento=payload.budget)
    return result

@app.post("/api/plans/select")
def plans_select(payload: PlanSelectionIn, request: Request):
    _check_customer(request, payload.customer_id)
    if payload.channel not in ("site", "whatsapp"):
        raise HTTPException(422, "Canal inválido")
    cid = get_customer(payload.customer_id)
    session = get_session(cid)
    if not session["consent_given"]:
        raise HTTPException(403, "Aceite a LGPD antes de solicitar um plano")
    catalog = db.get_catalog()
    match = next((p for p in catalog if p["nome"] == payload.plan_name), None)
    if not match:
        raise HTTPException(422, "Plano não encontrado no catálogo")
    session["history"].append({"author": "cliente", "channel": payload.channel,
      "text": f"Tenho interesse no plano {match['nome']} (R$ {match['preco']:.2f}/mês). Quero falar com um atendente sobre essa opção.", "ts": time.time()})
    session["status"] = "handoff"
    session["last_intent"] = "venda"
    session["history"].append({"author": "bot", "channel": payload.channel,
      "text": "Solicitação encaminhada ao atendimento humano. A escolha foi incluída no histórico; nenhuma contratação foi realizada.", "ts": time.time()})
    session["updated_at"] = time.time()
    log_event("interesse_plano_handoff", cid, plano=match["nome"])
    return {"ok": True, "status": "handoff", "plan": match["nome"]}

DIAGNOSTIC_PREFIX = "[[DIAGNOSTICO_REDE_V1]]"


def build_network_diagnostic(customer_id: str) -> dict:
    """Fatos da base OSS fictícia; não são medições de rede reais."""
    oss = db.get_network_status(customer_id)
    if not oss:
        return {"status": "indisponivel", "label": "Dados indisponíveis", "region": "Não informada",
                "eta": None, "description": "Não há dados de rede cadastrados para esta conta.",
                "next_step": "Solicite análise de um atendente.", "source": "OSS simulado"}
    status = str(oss.get("status_rede") or "").lower()
    region = str(oss.get("regiao") or "Não informada")
    eta = str(oss.get("previsao_normalizacao") or "") if status == "instabilidade" else None
    if status == "instabilidade":
        return {"status": "instabilidade", "label": "Instabilidade detectada", "region": region,
                "eta": eta or None, "description": "Uma ocorrência de rede pode afetar sua conexão.",
                "next_step": "Aguarde a previsão estimada ou peça suporte humano.", "source": "OSS simulado"}
    if status == "normal":
        return {"status": "normal", "label": "Rede sem ocorrência registrada", "region": region,
                "eta": None, "description": "O OSS simulado não registra instabilidade coletiva na região.",
                "next_step": "Se persistir, solicite suporte humano para análise individual.", "source": "OSS simulado"}
    return {"status": "indisponivel", "label": "Status não identificado", "region": region,
            "eta": None, "description": "Não foi possível interpretar o status cadastrado.",
            "next_step": "Solicite atendimento humano.", "source": "OSS simulado"}


class DiagnosticIn(BaseModel):
    customer_id: str
    channel: str = "site"


@app.post("/api/network/diagnostic")
def network_diagnostic(payload: DiagnosticIn, request: Request):
    _check_customer(request, payload.customer_id)
    if payload.channel not in ("site", "whatsapp"):
        raise HTTPException(422, "Canal inválido")
    cid = get_customer(payload.customer_id)
    session = get_session(cid)
    if not session["consent_given"]:
        raise HTTPException(403, "Aceite a LGPD antes de consultar a rede")
    diagnostic = build_network_diagnostic(cid)
    # Mensagem estruturada persistida no histórico, tratada como card pelo frontend.
    session["history"].append({"author": "bot", "channel": payload.channel,
        "text": DIAGNOSTIC_PREFIX + json.dumps(diagnostic, ensure_ascii=False), "ts": time.time()})
    session["updated_at"] = time.time()
    log_event("diagnostico_rede_simulado", cid, status=diagnostic["status"], canal=payload.channel)
    return diagnostic


@app.post("/api/chat", response_model=ChatOut)
def chat(payload: ChatIn, request: Request):
    return _process_chat(payload, request)


def _process_chat(payload: ChatIn, request: Request, replay_pending: bool = False):
    _check_customer(request, payload.customer_id)

    # RF005: se veio um token de SSO válido, ele manda — o customer_id no corpo vira
    # só um fallback (usado pelo seletor de demo e pelos testes automatizados).
    customer_id = resolve_token(payload.auth_token) or get_customer(payload.customer_id)
    session = get_session(customer_id)

    if not replay_pending and not check_rate_limit(customer_id):
        logger.warning("Rate limit estourado para cliente %s no canal %s", mask_id(customer_id), payload.channel)
        log_event("rate_limit_bloqueado", customer_id, canal=payload.channel)
        return ChatOut(
            customer_id=customer_id,
            reply="Notei um volume incomum de mensagens em pouco tempo. Aguarde um instante antes de continuar.",
            intent="rate_limited", confidence=1.0, agent=None, status=session["status"], handoff=False,
        )

    if not replay_pending:
        session["history"].append({"author": "cliente", "channel": payload.channel, "text": payload.text, "ts": time.time()})
        log_event("mensagem_cliente", customer_id, channel=payload.channel, text=payload.text)

    # LGPD: primeira interação exige aviso e aceite antes de processar a solicitação.
    if not session["consent_given"]:
        session["history"].append({"author": "bot", "channel": payload.channel, "text": CONSENT_TEXT, "ts": time.time()})
        return ChatOut(customer_id=customer_id, reply=CONSENT_TEXT, intent="consentimento_pendente",
                       confidence=1.0, agent=None, status=session["status"], handoff=False, needs_consent=True)

    if session["status"] in ("handoff", "human"):
        reply = "Seu atendimento humano está em andamento. Um atendente responderá em breve."
        session["history"].append({"author": "bot", "channel": payload.channel, "text": reply, "ts": time.time()})
        return ChatOut(customer_id=customer_id, reply=reply, intent=session["last_intent"] or "-",
                       confidence=1.0, agent=session["current_agent"], status=session["status"], handoff=True)

    classification = contextual_classification(payload.text, session)
    intent = classification["intent"]
    confidence = classification["confidence"]
    all_intents = classification["all_intents"]
    nlu_source = classification.get("_source", "keywords")
    session["last_intent"] = intent

    if classification["urgent"]:
        session["frustration_score"] += 1

    if intent == "nao_entendido":
        session["unclear_count"] += 1
    else:
        session["unclear_count"] = 0

    handoff_por_insistencia = session["unclear_count"] >= 3
    handoff_por_frustracao = session["frustration_score"] >= 2
    if intent in ("cancelamento", "handoff_explicito") or handoff_por_insistencia or handoff_por_frustracao:
        session["status"] = "handoff"
        session["current_agent"] = "Atendimento Humano"
        if intent == "cancelamento":
            motivo = "Intenção de cancelamento detectada"
        elif intent == "handoff_explicito":
            motivo = "Cliente solicitou atendente humano"
        elif handoff_por_frustracao:
            motivo = "Sinais de frustração recorrente detectados (risco de churn)"
        else:
            motivo = "NLU não conseguiu entender após várias tentativas"

        reply = (
            "Entendi. Estou te encaminhando para um atendente humano agora, com todo o histórico "
            "da nossa conversa e seu perfil — você não vai precisar repetir nada."
        )
        session["history"].append({"author": "bot", "channel": payload.channel, "text": reply, "ts": time.time()})

        ticket = {
            "customer_id_masked": mask_id(customer_id),
            "nome_mascarado": mask_name((db.get_customer_record(customer_id) or {}).get("nome", "Cliente")),
            "motivo": motivo,
            "intent": intent,
            "canal": payload.channel,
            "transcript_tamanho": len(session["history"]),
        }
        connector_result = HANDOFF_CONNECTOR.send(ticket)
        logger.info("Handoff acionado para cliente %s — motivo: %s", mask_id(customer_id), motivo)
        log_event("handoff", customer_id, motivo=motivo, canal=payload.channel, connector=connector_result)

        return ChatOut(customer_id=customer_id, reply=reply, intent=intent, confidence=confidence,
                       agent="Atendimento Humano", status="handoff", handoff=True)

    agent_name, reply = compose_reply(intent, customer_id, payload.text, all_intents)
    session["current_agent"] = agent_name
    session["history"].append({"author": "bot", "channel": payload.channel, "text": reply, "ts": time.time()})
    session["updated_at"] = time.time()
    log_event("resposta_bot", customer_id, agent=agent_name, intent=intent, canal=payload.channel,
               all_intents=all_intents, nlu_source=nlu_source)

    # RF006 — pesquisa de satisfação: dispara quando o cliente sinaliza que o
    # assunto está encerrado, uma única vez por sessão (nunca insiste se o
    # cliente já avaliou, e não interrompe o cliente no meio do atendimento).
    ask_nps = False
    if session["nps"] is None and is_closing_signal(payload.text):
        session["history"].append({"author": "bot", "channel": payload.channel, "text": NPS_PROMPT_TEXT, "ts": time.time()})
        log_event("nps_solicitado", customer_id, canal=payload.channel)
        ask_nps = True

    return ChatOut(customer_id=customer_id, reply=reply, intent=intent, confidence=confidence,
                   agent=agent_name, status="bot", handoff=False, ask_nps=ask_nps)


@app.get("/api/session/{customer_id}")
def get_session_view(customer_id: str, request: Request):
    _check_customer(request, customer_id)

    customer_id = get_customer(customer_id)
    session = get_session(customer_id)
    return {"crm": db.get_customer_record(customer_id), "session": session}


@app.post("/api/nps")
def submit_nps(payload: NPSIn, request: Request):
    _check_customer(request, payload.customer_id)

    customer_id = get_customer(payload.customer_id)
    session = get_session(customer_id)
    session["nps"] = payload.score
    session["status"] = "resolved"

    if payload.score >= 9:
        agradecimento = "Muito obrigado pela nota! Ficamos felizes em ajudar. 😊"
    elif payload.score >= 7:
        agradecimento = "Obrigado pelo retorno! Vamos continuar melhorando."
    else:
        agradecimento = "Obrigado pelo retorno — vamos usar isso para melhorar seu atendimento."

    session["history"].append({"author": "bot", "channel": payload.channel, "text": agradecimento, "ts": time.time()})
    log_event("nps", customer_id, score=payload.score)
    return {"ok": True, "reply": agradecimento}


@app.get("/api/handoff-queue")
def handoff_queue():
    """Dados exibidos ao supervisor/atendente já vêm com nome e ID mascarados
    (LGPD/minimização de dados) — o atendente que 'assumir' o caso teria, num
    sistema real, acesso completo autenticado e auditado; aqui simulamos a
    visão de fila (antes da autenticação por caso)."""
    queue = []
    for s in db.list_sessions():
        cid = s["customer_id"]
        if s["status"] in ("handoff", "human"):
            crm = db.get_customer_record(cid) or {}
            queue.append({
                "customer_id": cid,
                "status": s["status"],
                "assigned_agent": s["current_agent"] if s["status"] == "human" else None,
                "nome": mask_name(crm.get("nome", "Cliente")),
                "plano": crm.get("plano", "-"),
                "cliente_desde": crm.get("cliente_desde", "-"),
                "nps_historico": crm.get("nps_historico", "-"),
                "last_intent": s["last_intent"],
                "frustration_score": s["frustration_score"],
                "history": s["history"],
                "brief": handoff_brief(s),
            })
    return queue


# Preferência de canal para retorno da fila (simulação: sem integração Meta/WhatsApp).
notify_prefs = {}

class NotifyIn(BaseModel):
    customer_id: str
    channel: str

@app.post("/api/handoff/notify")
def handoff_notify(payload: NotifyIn, request: Request):
    _check_customer(request, payload.customer_id)
    if payload.channel not in ("site", "whatsapp"):
        raise HTTPException(422, "Canal inválido")
    cid = get_customer(payload.customer_id)
    session = get_session(cid)
    if session["status"] != "handoff":
        raise HTTPException(409, "Conversa não está aguardando atendimento")
    notify_prefs[cid] = payload.channel
    label = "WhatsApp" if payload.channel == "whatsapp" else "Site Web"
    msg = f"Preferência registrada: {label}. Nesta demonstração, o aviso fica registrado no histórico, sem envio externo."
    session["history"].append({"author": "bot", "channel": payload.channel, "text": msg, "ts": time.time()})
    log_event("handoff_aviso_configurado", cid, canal=payload.channel)
    return {"ok": True, "reply": msg}

class AgentActionIn(BaseModel):
    agent: str = Field(default="Operador Claro", min_length=2, max_length=80)

class AgentReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

@app.post("/api/handoff-queue/{customer_id}/assumir")
def assumir_handoff(customer_id: str, payload: AgentActionIn = AgentActionIn()):
    record_audit("handoff_assumir", customer_id)
    cid = get_customer(customer_id)
    session = get_session(cid)
    if session["status"] not in ("handoff", "human"):
        raise HTTPException(409, "Esta conversa não está na fila")
    session["status"] = "human"
    session["current_agent"] = payload.agent
    conn = db.get_db()
    conn.execute("INSERT OR REPLACE INTO handoff_agents VALUES (?,?,?)", (cid, payload.agent, time.time()))
    conn.commit()
    conn.close()
    log_event("handoff_assumido", cid, operador=payload.agent)
    return {"ok": True, "status": "human"}

@app.post("/api/handoff-queue/{customer_id}/responder")
def responder_handoff(customer_id: str, payload: AgentReplyIn):
    record_audit("handoff_responder", customer_id)
    cid = get_customer(customer_id)
    session = get_session(cid)
    if session["status"] != "human":
        raise HTTPException(409, "Assuma a conversa antes de responder")
    history = session["history"]
    channel = history[-1]["channel"] if history else "site"
    history.append({"author": "atendente", "channel": channel, "text": payload.text, "ts": time.time()})
    log_event("resposta_humana", cid)
    return {"ok": True}

@app.post("/api/handoff-queue/{customer_id}/encerrar")
def encerrar_handoff(customer_id: str):
    record_audit("handoff_encerrar", customer_id)
    cid = get_customer(customer_id)
    session = get_session(cid)
    if session["status"] != "human":
        raise HTTPException(409, "Esta conversa não está em atendimento")
    session["status"] = "resolved"
    session["history"].append({"author": "atendente", "channel": "site", "text": "Atendimento finalizado.", "ts": time.time()})
    log_event("atendimento_humano_finalizado", cid)
    return {"ok": True}


@app.post("/api/campanha-proativa/{customer_id}")
def campanha_proativa(customer_id: str):
    customer_id = get_customer(customer_id)
    session = get_session(customer_id)
    bss = db.get_invoice(customer_id)
    crm = db.get_customer_record(customer_id) or {}
    if not bss:
        return {"ok": False, "error": "sem fatura"}
    nome_primeiro = crm.get("nome", "Cliente").split(" ")[0]
    msg = (
        f"Olá, {nome_primeiro}! Sua fatura de R$ {bss['fatura_valor']:.2f} vence em breve "
        f"({bss['vencimento']}). Posso te ajudar com o pagamento por aqui mesmo?"
    )
    session["history"].append({"author": "bot", "channel": "whatsapp", "text": msg, "ts": time.time()})
    session["status"] = "bot"
    session["current_agent"] = "Agente de Informações"
    log_event("campanha_proativa", customer_id, canal="whatsapp")
    return {"ok": True, "mensagem": msg}


# ---------------------------------------------------------------------------
# Painel Claro (interno) — iniciar contato com um cliente escolhido
# ---------------------------------------------------------------------------
#
# Diferente da campanha proativa (gatilho fixo: fatura vencendo), aqui o atendente
# escolhe QUALQUER cliente da base e o MOTIVO do contato — simula um agente de
# retenção ou vendas decidindo abordar um cliente específico, não um evento
# automático do BSS.

MOTIVOS_CONTATO = {
    "fatura_vencendo": "Agente de Informações",
    "retencao": "Atendimento Humano",
    "upgrade_oferta": "Agente de Vendas",
    "outro": "Assistente ClaroUnify",
}


def montar_mensagem_contato(customer_id: str, motivo: str, mensagem_personalizada: Optional[str] = None) -> tuple[bool, str]:
    """Monta a mensagem de contato a partir do motivo escolhido. Retorna
    (ok, mensagem_ou_erro) — reaproveitada tanto pelo contato individual quanto
    pelo contato em massa, pra não duplicar a regra de negócio em dois lugares."""
    crm = db.get_customer_record(customer_id) or {}
    nome_primeiro = crm.get("nome", "Cliente").split(" ")[0]

    if motivo == "fatura_vencendo":
        bss = db.get_invoice(customer_id)
        if not bss:
            return False, "cliente sem fatura cadastrada"
        return True, (
            f"Olá, {nome_primeiro}! Sua fatura de R$ {bss['fatura_valor']:.2f} vence em "
            f"{bss['vencimento']}. Posso te ajudar com o pagamento por aqui mesmo?"
        )
    if motivo == "retencao":
        return True, (
            f"Olá, {nome_primeiro}! Notamos que você é cliente Claro desde "
            f"{crm.get('cliente_desde', '-')} e queremos te oferecer condições especiais "
            f"de permanência no seu plano {crm.get('plano', '')}. Posso te contar mais?"
        )
    if motivo == "upgrade_oferta":
        catalogo = db.get_catalog()
        oferta = catalogo[0] if catalogo else None
        if not oferta:
            return False, "catálogo vazio"
        return True, (
            f"Olá, {nome_primeiro}! Temos uma oferta de upgrade pra você: "
            f"{oferta['nome']} por R$ {oferta['preco']:.2f} ({oferta['descricao']}). Quer saber mais?"
        )
    if motivo == "outro":
        if not mensagem_personalizada:
            return False, "mensagem_personalizada é obrigatória para motivo 'outro'"
        return True, mensagem_personalizada
    return False, f"motivo desconhecido: {motivo}"


def registrar_contato_iniciado(customer_id: str, channel: str, motivo: str, mensagem: str) -> None:
    session = get_session(customer_id)
    # Contato iniciado pela Claro: consideramos consentimento implícito para esta
    # interação (é a própria empresa contatando, não uma solicitação do cliente
    # que exige o aviso LGPD de tratamento de dados sob demanda dele).
    session["consent_given"] = True
    session["status"] = "bot"
    session["current_agent"] = MOTIVOS_CONTATO.get(motivo, "Assistente ClaroUnify")
    session["history"].append({"author": "bot", "channel": channel, "text": mensagem, "ts": time.time()})
    log_event("contato_iniciado_por_claro", customer_id, motivo=motivo, canal=channel)


class IniciarContatoIn(BaseModel):
    customer_id: str
    channel: str
    motivo: str  # fatura_vencendo | retencao | upgrade_oferta | outro
    mensagem_personalizada: Optional[str] = None


class IniciarContatoLoteIn(BaseModel):
    channel: str
    motivo: str
    mensagem_personalizada: Optional[str] = None
    busca: str = ""
    fatura_status: str = ""
    status_rede: str = ""


@app.get("/api/admin/clientes")
def admin_listar_clientes(q: str = "", fatura_status: str = "", status_rede: str = "",
                            limit: int = 20, offset: int = 0):
    """Busca paginada de clientes para o Painel Claro escolher quem contatar —
    NUNCA um dump da base inteira (numa Claro real seriam milhões de linhas).
    Dados não mascarados aqui de propósito: é a visão interna de quem já está
    legitimamente trabalhando o caso, diferente da fila de handoff (que mascara
    porque é uma visão de triagem antes de qualquer atendente assumir)."""
    limit = max(1, min(limit, 100))  # trava um teto sensato, mesmo se alguém pedir limit=999999
    return db.list_customers(busca=q, fatura_status=fatura_status, status_rede=status_rede, limit=limit, offset=offset)


@app.post("/api/admin/iniciar-contato")
def admin_iniciar_contato(payload: IniciarContatoIn):
    record_audit("iniciar_contato", payload.customer_id)
    customer_id = get_customer(payload.customer_id)
    ok, resultado = montar_mensagem_contato(customer_id, payload.motivo, payload.mensagem_personalizada)
    if not ok:
        return {"ok": False, "error": resultado}
    registrar_contato_iniciado(customer_id, payload.channel, payload.motivo, resultado)
    return {"ok": True, "mensagem": resultado, "customer_id": customer_id}


@app.post("/api/admin/iniciar-contato-lote")
def admin_iniciar_contato_lote(payload: IniciarContatoLoteIn):
    record_audit("contato_em_lote")
    """Contato em massa por segmento — o equivalente realista de 'campanha
    proativa' numa base com milhões de clientes: o atendente não escolhe um por
    um, escolhe um FILTRO (ex.: 'todos com fatura atrasada') e dispara pra todos
    que baterem com ele de uma vez."""
    ids = db.list_customer_ids_matching(
        busca=payload.busca, fatura_status=payload.fatura_status, status_rede=payload.status_rede
    )
    if not ids:
        return {"ok": False, "error": "nenhum cliente bate com esse filtro"}

    enviados, falhas = 0, []
    for customer_id in ids:
        ok, resultado = montar_mensagem_contato(customer_id, payload.motivo, payload.mensagem_personalizada)
        if ok:
            registrar_contato_iniciado(customer_id, payload.channel, payload.motivo, resultado)
            enviados += 1
        else:
            falhas.append({"customer_id": customer_id, "erro": resultado})

    return {"ok": True, "total_filtrado": len(ids), "total_enviado": enviados, "falhas": falhas}


@app.get("/api/kpis")
def kpis():
    todas_sessoes = db.list_sessions()  # 1 leitura só; reaproveitada abaixo
    total_conversas = len(todas_sessoes)
    resolvidas_bot = sum(1 for s in todas_sessoes if s["status"] == "resolved" and s["last_intent"] not in ("cancelamento", "handoff_explicito"))
    handoffs = sum(1 for e in events if e["type"] == "handoff")
    total_finalizadas = sum(1 for s in todas_sessoes if s["status"] in ("resolved", "handoff"))
    taxa_resolucao = (resolvidas_bot / total_finalizadas * 100) if total_finalizadas else 0.0
    volume_handoff = (handoffs / total_finalizadas * 100) if total_finalizadas else 0.0
    nps_scores = [s["nps"] for s in todas_sessoes if s["nps"] is not None]
    nps_medio = sum(nps_scores) / len(nps_scores) if nps_scores else None
    ativos = sum(1 for s in todas_sessoes if s["status"] == "bot")
    por_canal = {"site": 0, "whatsapp": 0}
    for e in events:
        if e["type"] == "mensagem_cliente":
            por_canal[e.get("channel", "site")] = por_canal.get(e.get("channel", "site"), 0) + 1

    # RF006 — taxa de resolução POR CANAL (o requisito pede explicitamente essa
    # quebra, não só o agregado). Atribuímos cada sessão ao canal de ORIGEM (1ª
    # mensagem) — mesmo que ela tenha migrado de canal depois, no mundo real é o
    # canal de origem que o time de produto quer comparar (ex.: "WhatsApp converte
    # melhor que o Site?").
    resolucao_por_canal = {}
    for canal in ("site", "whatsapp"):
        sessoes_do_canal = [s for s in todas_sessoes if s["history"] and s["history"][0]["channel"] == canal]
        finalizadas_canal = [s for s in sessoes_do_canal if s["status"] in ("resolved", "handoff")]
        resolvidas_canal = [s for s in finalizadas_canal
                            if s["status"] == "resolved" and s["last_intent"] not in ("cancelamento", "handoff_explicito")]
        resolucao_por_canal[canal] = round(len(resolvidas_canal) / len(finalizadas_canal) * 100, 1) if finalizadas_canal else None

    # RF006 — "custo por atendimento": estimativa ilustrativa (não é dado real de
    # custo da Claro). Referência de mercado usada só pra ordem de grandeza: contato
    # resolvido 100% por bot custa uma fração de centavos de infraestrutura; contato
    # que escala pra humano carrega o custo do atendente (tempo médio de atendimento).
    CUSTO_BOT = float(os.environ.get("CUSTO_BOT_ESTIMADO", "0.15"))
    CUSTO_HANDOFF = float(os.environ.get("CUSTO_HANDOFF_ESTIMADO", "8.50"))
    custo_total = resolvidas_bot * CUSTO_BOT + handoffs * CUSTO_HANDOFF
    custo_por_atendimento = (custo_total / total_finalizadas) if total_finalizadas else None

    return {
        "total_conversas": total_conversas,
        "conversas_ativas": ativos,
        "taxa_resolucao_pct": round(taxa_resolucao, 1),
        "taxa_resolucao_por_canal": resolucao_por_canal,
        "volume_handoff_pct": round(volume_handoff, 1),
        "nps_medio": round(nps_medio, 1) if nps_medio is not None else None,
        "custo_por_atendimento_estimado": round(custo_por_atendimento, 2) if custo_por_atendimento is not None else None,
        "mensagens_por_canal": por_canal,
        "total_eventos": len(events),
        "aviso_metodologico": (
            "Estes números vêm de conversas simuladas nesta demo, não de tráfego real. "
            "O custo por atendimento usa valores de referência de mercado, não custos reais "
            "da Claro. Só servem como prova de que o cálculo funciona — não como benchmark de negócio."
        ),
    }


@app.get("/api/admin/analytics")
def admin_analytics(days: int = 7):
    """Compara mensagens do período atual com janela imediatamente anterior, sem dados simulados extras."""
    days = max(1, min(days, 90))
    now = time.time()
    start = now - days * 86400
    previous_start = start - days * 86400
    conn = db.get_db()
    rows = conn.execute("SELECT ts, author, channel FROM messages WHERE ts >= ? AND ts <= ? ORDER BY ts", (previous_start, now)).fetchall()
    intents = conn.execute("SELECT last_intent, COUNT(*) total FROM sessions WHERE last_intent IS NOT NULL GROUP BY last_intent ORDER BY total DESC LIMIT 8").fetchall()
    sessions = conn.execute("SELECT status, created_at, updated_at FROM sessions WHERE created_at >= ? AND created_at <= ?", (start, now)).fetchall()
    conn.close()
    series, channels = {}, {"site": 0, "whatsapp": 0}
    current_count = previous_count = 0
    for row in rows:
        if row["author"] != "cliente":
            continue
        if row["ts"] < start:
            previous_count += 1
            continue
        current_count += 1
        day = datetime.fromtimestamp(row["ts"], timezone.utc).strftime("%Y-%m-%d")
        series[day] = series.get(day, 0) + 1
        channels[row["channel"]] = channels.get(row["channel"], 0) + 1
    dates = [(datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    resolved = [row for row in sessions if row["status"] == "resolved"]
    # Duração da sessão não equivale a SLA/tempo de resposta do operador.
    avg_duration = round(sum(max(0, row["updated_at"] - row["created_at"]) for row in resolved) / len(resolved) / 60, 1) if resolved else None
    change = round((current_count - previous_count) / previous_count * 100, 1) if previous_count else None
    return {"days": days, "series": [{"date": day, "messages": series.get(day, 0)} for day in dates],
            "channels": channels, "intents": [dict(r) for r in intents],
            "comparison": {"current_messages": current_count, "previous_messages": previous_count,
                           "change_pct": change, "period_days": days},
            "operations": {"sessions_created": len(sessions), "resolved_sessions": len(resolved),
                           "resolution_rate_pct": round(len(resolved) / len(sessions) * 100, 1) if sessions else None,
                           "average_session_duration_minutes": avg_duration},
            "disclaimer": "Dados de demonstração. Duração da sessão não representa tempo médio de resposta do atendente."}


@app.get("/api/alertas")
def alertas():
    """RNF006 — alertas automáticos simples. Compara os KPIs atuais contra limiares
    fixos e retorna o que está fora do esperado. Não é machine learning de anomalia
    (fora de escopo do MVP), mas cumpre o requisito de alertar automaticamente."""
    dados = kpis()
    bloqueios_rate_limit = sum(1 for e in events if e["type"] == "rate_limit_bloqueado")
    ativos_alertas = []

    if dados["volume_handoff_pct"] > ALERT_THRESHOLDS["volume_handoff_pct_max"]:
        ativos_alertas.append({
            "tipo": "volume_handoff_alto",
            "severidade": "atencao",
            "mensagem": f"Volume de handoff em {dados['volume_handoff_pct']}%, acima do limite de {ALERT_THRESHOLDS['volume_handoff_pct_max']}%.",
        })
    if dados["total_conversas"] >= 3 and dados["taxa_resolucao_pct"] < ALERT_THRESHOLDS["taxa_resolucao_pct_min"]:
        ativos_alertas.append({
            "tipo": "taxa_resolucao_baixa",
            "severidade": "atencao",
            "mensagem": f"Taxa de resolução automática em {dados['taxa_resolucao_pct']}%, abaixo do limite de {ALERT_THRESHOLDS['taxa_resolucao_pct_min']}%.",
        })
    if bloqueios_rate_limit >= ALERT_THRESHOLDS["rate_limit_bloqueios_max"]:
        ativos_alertas.append({
            "tipo": "possivel_abuso",
            "severidade": "critico",
            "mensagem": f"{bloqueios_rate_limit} bloqueios de rate limit — possível uso indevido ou bug em loop.",
        })

    return {"alertas": ativos_alertas, "verificado_em": time.time()}


@app.get("/api/events")
def get_events(limit: int = 30, mask: bool = True):
    out = list(reversed(events))[:limit]
    if mask:
        out = [{**e, "customer_id": e["customer_id_masked"]} for e in out]
    return out


@app.get("/api/lgpd/exportar/{customer_id}")
def exportar_dados(customer_id: str, request: Request):
    _check_customer(request, customer_id)

    """Direito de portabilidade — devolve tudo que o Hub guarda sobre o cliente."""
    customer_id = get_customer(customer_id)
    sessao = dict(db.get_or_create_session(customer_id)) if db.session_exists(customer_id) else None
    return {
        "crm": db.get_customer_record(customer_id),
        "faturas": db.get_invoice(customer_id),
        "sessao_conversacional": sessao,
    }


@app.delete("/api/lgpd/excluir/{customer_id}")
def excluir_dados(customer_id: str, request: Request):
    _check_customer(request, customer_id)

    """Direito de eliminação — apaga a sessão conversacional (não os cadastros
    de CRM/BSS, que são sistemas de registro fora do escopo do Hub)."""
    customer_id = get_customer(customer_id)
    existed = db.session_exists(customer_id)
    db.delete_session(customer_id)
    log_event("lgpd_exclusao", customer_id)
    return {"ok": True, "existia": existed}


@app.get("/api/capacidade")
def capacidade():
    """Nota honesta de capacidade + RNF001 (P95 ≤ 2s). O P95 abaixo é medido de
    verdade (não estimado) a partir das últimas requisições reais a /api/chat nesta
    sessão — inclui o tempo do LLM quando ele está ativo. Ainda assim, é P95 de
    tráfego sequencial de demonstração, não de carga concorrente de produção."""
    latencias = sorted(_chat_latencies_ms)
    p95_ms = None
    if latencias:
        idx = min(int(len(latencias) * 0.95), len(latencias) - 1)
        p95_ms = round(latencias[idx], 1)
    meta_rnf001_ms = 2000
    return {
        "arquitetura_atual": "processo único; sessões já persistidas em SQLite (sobrevivem a restart); eventos ainda em memória; sem réplicas",
        "escala_estimada_sem_mudanca": "dezenas de conversas simultâneas, uso de demonstração",
        "rnf001_tempo_resposta": {
            "amostras_medidas": len(latencias),
            "p95_ms": p95_ms,
            "meta_ms": meta_rnf001_ms,
            "dentro_da_meta": (p95_ms is not None and p95_ms <= meta_rnf001_ms),
            "nota": (
                "P95 real das últimas requisições a /api/chat nesta sessão (inclui o LLM "
                "quando ativo). Sem amostras suficientes ainda, o modo palavra-chave fica "
                "bem abaixo da meta; o modo LLM depende do hardware e pode ultrapassá-la — "
                "reportamos o valor medido, não uma estimativa otimista."
            ),
        },
        "o_que_falta_para_escalar": [
            "trocar SQLite por Postgres/Redis para múltiplas réplicas do orquestrador acessarem o mesmo estado concorrentemente",
            "mover 'events' (ainda em memória) para Kafka/Pub-Sub",
            "rodar atrás de um load balancer com autoescalonamento (Cloud Run/K8s)",
            "medir p95 real sob carga CONCORRENTE com uma ferramenta de load test (ex.: locust/k6) — o número acima é sequencial",
        ],
        "nota": "Não fabricamos número de 'suporta 10x pico' sem medir — isso exigiria o ambiente completo.",
    }


@app.get("/api/admin/audit")
def admin_audit(limit: int = 50):
    return db.list_audit(max(1, min(limit, 200)))

@app.get("/api/llm-status")
def llm_status():
    """Mostra se o Qwen3 4B (via Ollama) está disponível ou se o sistema está
    operando no fallback por palavras-chave — útil para debugar e para mostrar
    na apresentação que a troca de motor é transparente."""
    return check_llm_status()


@app.post("/api/reset")
def reset():
    record_audit("reset")
    notify_prefs.clear()
    db.reset_all_sessions()
    events.clear()
    _rate_log.clear()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Frontend estático
# ---------------------------------------------------------------------------

app.mount("/static", NoCacheStaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def warm_up_llm_on_startup():
    """Dispara uma chamada de 'aquecimento' ao Ollama assim que o servidor sobe, em
    segundo plano, para que o modelo já esteja carregado na VRAM quando o primeiro
    cliente mandar uma mensagem de verdade. Não bloqueia o boot do FastAPI."""
    db.init_db()  # cria as tabelas e popula com dados mock na 1ª execução (idempotente)
    if not USE_LLM:
        return
    import asyncio

    def _warm():
        call_ollama_chat([{"role": "user", "content": "oi"}])

    asyncio.get_event_loop().run_in_executor(None, _warm)


@app.get("/")
def index():
    return FileResponse("static/index.html")
