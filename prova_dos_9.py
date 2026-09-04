"""
Prova dos 9 do ClaroUnify Hub.

Roda TODOS os fluxos importantes contra o servidor que já está no ar (uvicorn +
opcionalmente Ollama) e imprime PASS/FAIL de cada um. Não inventa nada: se alguma
checagem falhar, é porque algo realmente não está funcionando como deveria.

Uso:
    1. Deixe o servidor rodando: uvicorn main:app --reload
    2. Em outro terminal, na mesma pasta:  python prova_dos_9.py
"""

import sys
import time
import requests

BASE = "http://localhost:8000"
CLIENTE_A = "11999990001"
CLIENTE_B = "11999990002"

passed = []
failed = []


def check(nome, condicao, detalhe=""):
    if condicao:
        passed.append(nome)
        print(f"  ✅ {nome}")
    else:
        failed.append(nome)
        print(f"  ❌ {nome}  {('— ' + detalhe) if detalhe else ''}")


def chat(customer_id, channel, text):
    return requests.post(f"{BASE}/api/chat", json={
        "customer_id": customer_id, "channel": channel, "text": text
    }, timeout=40).json()


def consent(customer_id, accepted=True):
    return requests.post(f"{BASE}/api/consent", json={
        "customer_id": customer_id, "accepted": accepted
    }, timeout=10).json()


def reset():
    requests.post(f"{BASE}/api/reset", timeout=10)


def section(titulo):
    print(f"\n— {titulo} —")


try:
    requests.get(BASE, timeout=3)
except Exception:
    print(f"❌ Não consegui conectar em {BASE}. O servidor (uvicorn) está rodando?")
    sys.exit(1)

reset()

# ---------------------------------------------------------------------------
section("0. Servidor no ar")
# ---------------------------------------------------------------------------
r = requests.get(BASE)
check("Página inicial responde (200)", r.status_code == 200)

# ---------------------------------------------------------------------------
section("1. Status do LLM")
# ---------------------------------------------------------------------------
llm = requests.get(f"{BASE}/api/llm-status").json()
llm_ativo = llm.get("modo_ativo") == "llm"
print(f"  ℹ️  modo_ativo = {llm.get('modo_ativo')}  (isso não é falha nem sucesso — é informativo)")
check("Endpoint /api/llm-status responde", "modo_ativo" in llm)

# ---------------------------------------------------------------------------
section("2. Consentimento LGPD obrigatório")
# ---------------------------------------------------------------------------
r1 = chat(CLIENTE_A, "site", "oi")
check("1ª mensagem pede consentimento (needs_consent=True)", r1.get("needs_consent") is True)
r2 = chat(CLIENTE_A, "site", "quero saber da fatura")
check("Sem aceitar, continua pedindo consentimento", r2.get("needs_consent") is True)
consent(CLIENTE_A, True)

# ---------------------------------------------------------------------------
section("3. Roteamento de intenção")
# ---------------------------------------------------------------------------
r = chat(CLIENTE_A, "site", "quero saber da minha fatura")
check("Intenção 'informacao' roteada certa", r.get("intent") == "informacao", f"veio: {r.get('intent')}")
check("Resposta tem conteúdo (não vazia)", bool(r.get("reply")) and len(r["reply"]) > 5)

reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
r = chat(CLIENTE_A, "site", "minha internet está caindo toda hora")
check("Intenção 'suporte' roteada certa", r.get("intent") == "suporte", f"veio: {r.get('intent')}")

r = chat(CLIENTE_A, "site", "quero conhecer planos com mais dados")
check("Intenção 'venda' roteada certa", r.get("intent") == "venda", f"veio: {r.get('intent')}")

# ---------------------------------------------------------------------------
section("4. Continuidade de contexto entre canais (Site -> WhatsApp)")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_B, "site", "oi")
consent(CLIENTE_B, True)
chat(CLIENTE_B, "site", "quero saber da minha fatura")
sessao = requests.get(f"{BASE}/api/session/{CLIENTE_B}").json()["session"]
canais_no_historico = set(m["channel"] for m in sessao["history"])
check("Histórico existe após conversa iniciada no Site", len(sessao["history"]) >= 2)

r = chat(CLIENTE_B, "whatsapp", "continua aqui?")
sessao = requests.get(f"{BASE}/api/session/{CLIENTE_B}").json()["session"]
canais_no_historico = set(m["channel"] for m in sessao["history"])
check(
    "Mesma sessão contém mensagens de AMBOS os canais (site + whatsapp)",
    {"site", "whatsapp"}.issubset(canais_no_historico),
    f"canais encontrados: {canais_no_historico}",
)
check("Nenhum dado foi perdido ao trocar de canal (histórico cresceu, não resetou)", len(sessao["history"]) >= 4)

# ---------------------------------------------------------------------------
section("5. Handoff por cancelamento")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
r = chat(CLIENTE_A, "site", "quero cancelar meu plano")
check("Cancelamento aciona handoff imediatamente", r.get("handoff") is True)
fila = requests.get(f"{BASE}/api/handoff-queue").json()
check("Cliente aparece na fila de handoff", any(item["customer_id"] == CLIENTE_A for item in fila))
if fila:
    check("Nome do cliente vem mascarado na fila (LGPD)", "." in fila[0]["nome"] or len(fila[0]["nome"].split()) <= 2)

# ---------------------------------------------------------------------------
section("6. Handoff por frustração recorrente (sem dizer 'cancelar')")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_B, "whatsapp", "oi")
consent(CLIENTE_B, True)
chat(CLIENTE_B, "whatsapp", "minha internet cai toda semana")
r = chat(CLIENTE_B, "whatsapp", "já liguei antes e ninguém resolveu")
check(
    "2 sinais de urgência seguidos escalam para handoff mesmo sem 'cancelar'",
    r.get("handoff") is True,
    f"status veio: {r.get('status')}",
)

# ---------------------------------------------------------------------------
section("7. Tolerância a mensagens não entendidas (sem handoff na 1ª tentativa)")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
r = chat(CLIENTE_A, "site", "xkjshdaksjhd")
check("1ª mensagem confusa NÃO vira handoff (tolerância)", r.get("handoff") is False)
chat(CLIENTE_A, "site", "asdasdasd")
r = chat(CLIENTE_A, "site", "qweqweqwe")
check("3ª mensagem confusa seguida ESCALA para handoff", r.get("handoff") is True)

# ---------------------------------------------------------------------------
section("8. Saudação isolada não vira handoff")
# ---------------------------------------------------------------------------
reset()
r = chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
r = chat(CLIENTE_A, "site", "bom dia")
check("Saudação sozinha recebe resposta amigável, sem handoff", r.get("handoff") is False and r.get("intent") == "saudacao")

# ---------------------------------------------------------------------------
section("9. Rate limiting")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
ultima = None
for _ in range(21):
    ultima = chat(CLIENTE_A, "site", "fatura")
check("Após 21 mensagens rápidas, rate limit bloqueia", ultima.get("intent") == "rate_limited")

# ---------------------------------------------------------------------------
section("10. LGPD — portabilidade e exclusão")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
export = requests.get(f"{BASE}/api/lgpd/exportar/{CLIENTE_A}").json()
check("Exportação de dados retorna sessão do cliente", export.get("sessao_conversacional") is not None)
delete = requests.delete(f"{BASE}/api/lgpd/excluir/{CLIENTE_A}").json()
check("Exclusão confirma que a sessão existia", delete.get("existia") is True)
sessao_depois = requests.get(f"{BASE}/api/session/{CLIENTE_A}").json()["session"]
check("Sessão foi realmente apagada (histórico voltou a zero)", len(sessao_depois["history"]) == 0)

# ---------------------------------------------------------------------------
section("11. Campanha proativa")
# ---------------------------------------------------------------------------
reset()
r = requests.post(f"{BASE}/api/campanha-proativa/{CLIENTE_A}").json()
check("Campanha proativa dispara mensagem", r.get("ok") is True and "fatura" in r.get("mensagem", "").lower())

# ---------------------------------------------------------------------------
section("12. Dashboard / KPIs")
# ---------------------------------------------------------------------------
kpis = requests.get(f"{BASE}/api/kpis").json()
check("KPIs retornam estrutura esperada", all(k in kpis for k in ["taxa_resolucao_pct", "volume_handoff_pct", "aviso_metodologico"]))
check("Aviso metodológico está presente (não finge ser dado real de negócio)", bool(kpis.get("aviso_metodologico")))

# ---------------------------------------------------------------------------
section("13. Capacidade (transparência, não número inventado)")
# ---------------------------------------------------------------------------
cap = requests.get(f"{BASE}/api/capacidade").json()
check("Endpoint de capacidade não inventa SLA de produção", "nota" in cap and "fabricamos" in cap["nota"])

# ---------------------------------------------------------------------------
section("14. RF005 — Autenticação silenciosa por canal (SSO simulado)")
# ---------------------------------------------------------------------------
reset()
auth = requests.post(f"{BASE}/api/auth/silent", json={"customer_id": CLIENTE_A, "channel": "site"}).json()
check("Token de autenticação silenciosa é emitido sem tela de login", bool(auth.get("token")))

# ---------------------------------------------------------------------------
section("15. RF006 — Custo por atendimento e resolução por canal")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
chat(CLIENTE_A, "site", "quero saber da fatura")
kpis = requests.get(f"{BASE}/api/kpis").json()
check("KPI de custo por atendimento existe na resposta", "custo_por_atendimento_estimado" in kpis)
check("Taxa de resolução quebrada por canal existe na resposta", "taxa_resolucao_por_canal" in kpis
      and set(kpis["taxa_resolucao_por_canal"].keys()) == {"site", "whatsapp"})

# ---------------------------------------------------------------------------
section("16. RNF001 — Tempo de resposta medido de verdade (não estimado)")
# ---------------------------------------------------------------------------
cap = requests.get(f"{BASE}/api/capacidade").json()
rnf001 = cap.get("rnf001_tempo_resposta", {})
check("Endpoint de capacidade reporta P95 medido de requisições reais", rnf001.get("amostras_medidas", 0) > 0)
check("P95 vem acompanhado da meta do RNF001 (2000ms) e veredito", "meta_ms" in rnf001 and "dentro_da_meta" in rnf001)

# ---------------------------------------------------------------------------
section("17. RNF006 — Trace ID por jornada e alertas automáticos")
# ---------------------------------------------------------------------------
reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
chat(CLIENTE_A, "site", "quero saber da fatura")
sessao = requests.get(f"{BASE}/api/session/{CLIENTE_A}").json()["session"]
check("Sessão tem um trace_id único por jornada", bool(sessao.get("trace_id")))
eventos = requests.get(f"{BASE}/api/events?limit=5").json()
check("Eventos carregam o trace_id da jornada (rastreamento distribuído)", all("trace_id" in e for e in eventos))
alertas = requests.get(f"{BASE}/api/alertas").json()
check("Endpoint de alertas automáticos responde com a estrutura esperada", "alertas" in alertas)

# ---------------------------------------------------------------------------
section("18. Dados vêm do banco (SQLite), não de dicionário hardcoded")
# ---------------------------------------------------------------------------
resposta_clientes = requests.get(f"{BASE}/api/admin/clientes?limit=100").json()
clientes = resposta_clientes["items"]
check("Banco tem mais de 5 clientes cadastrados (prova que não é mais hardcoded)", resposta_clientes["total"] >= 5)
check("Cada cliente do banco traz fatura e status de rede via JOIN", all("fatura_status" in c and "status_rede" in c for c in clientes))

CLIENTE_C = "11999990003"  # Carlos Andrade — só existe no banco, nunca esteve hardcoded no Python
reset()
r = chat(CLIENTE_C, "site", "oi")
check("Cliente que só existe no banco consegue conversar (pede consentimento normalmente)", r.get("needs_consent") is True)
consent(CLIENTE_C, True)
r = chat(CLIENTE_C, "site", "oi")
check("Depois do consentimento, saudação é roteada certa", r["intent"] == "saudacao")
r = chat(CLIENTE_C, "site", "quero saber da fatura")
check("Resposta do agente usa dado real do banco (não erro/dado ausente)", "R$" in r["reply"])

# ---------------------------------------------------------------------------
section("19. Painel Claro pode iniciar contato com qualquer cliente")
# ---------------------------------------------------------------------------
reset()
r = requests.post(f"{BASE}/api/admin/iniciar-contato", json={
    "customer_id": CLIENTE_B, "channel": "whatsapp", "motivo": "retencao",
}).json()
check("Contato de retenção iniciado com sucesso", r.get("ok") is True)

sessao_b = requests.get(f"{BASE}/api/session/{CLIENTE_B}").json()["session"]
check("Mensagem iniciada pela Claro aparece no histórico do cliente", len(sessao_b["history"]) == 1 and sessao_b["history"][0]["author"] == "bot")

r_erro = requests.post(f"{BASE}/api/admin/iniciar-contato", json={
    "customer_id": CLIENTE_A, "channel": "site", "motivo": "outro",
}).json()
check("Motivo 'outro' sem mensagem_personalizada retorna erro claro", r_erro.get("ok") is False)

r_custom = requests.post(f"{BASE}/api/admin/iniciar-contato", json={
    "customer_id": CLIENTE_A, "channel": "site", "motivo": "outro",
    "mensagem_personalizada": "Mensagem de teste customizada",
}).json()
check("Motivo 'outro' com mensagem customizada funciona", r_custom.get("ok") is True and r_custom.get("mensagem") == "Mensagem de teste customizada")

# ---------------------------------------------------------------------------
section("20. Contexto de sessão realmente persistido em SQLite (não é RAM)")
# ---------------------------------------------------------------------------
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "clarounify.db"
check("Arquivo clarounify.db existe em disco", DB_PATH.exists())

reset()
chat(CLIENTE_A, "site", "oi")
consent(CLIENTE_A, True)
chat(CLIENTE_A, "site", "quero saber da fatura")

# Lê o banco com uma conexão TOTALMENTE separada da que o servidor usa — se os
# dados aparecerem aqui, é prova de que estão em disco, não só na memória do
# processo do uvicorn.
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
row_sessao = conn.execute("SELECT * FROM sessions WHERE customer_id=?", (CLIENTE_A,)).fetchone()
check("Linha da sessão existe na tabela 'sessions' do arquivo .db", row_sessao is not None)
check("consent_given gravado corretamente no banco (1 = True)", row_sessao is not None and row_sessao["consent_given"] == 1)

mensagens_no_banco = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE customer_id=?", (CLIENTE_A,)).fetchone()["n"]
sessao_via_api = requests.get(f"{BASE}/api/session/{CLIENTE_A}").json()["session"]
check(
    "Nº de mensagens no banco bate com o histórico retornado pela API",
    mensagens_no_banco == len(sessao_via_api["history"]) and mensagens_no_banco > 0,
)
conn.close()

# ---------------------------------------------------------------------------
section("21. Painel Claro escalável: busca, filtro por segmento e paginação")
# ---------------------------------------------------------------------------
pagina1 = requests.get(f"{BASE}/api/admin/clientes?limit=5&offset=0").json()
pagina2 = requests.get(f"{BASE}/api/admin/clientes?limit=5&offset=5").json()
check("Paginação nunca devolve tudo de uma vez (limit é respeitado)", len(pagina1["items"]) == 5)
check("Página 2 tem clientes diferentes da página 1", {c["id"] for c in pagina1["items"]}.isdisjoint({c["id"] for c in pagina2["items"]}))
check("Total reportado bate com o total real de clientes na base", pagina1["total"] == resposta_clientes["total"])

busca_nome = requests.get(f"{BASE}/api/admin/clientes?q=Andrade").json()
check("Busca por nome encontra o cliente certo", any("Andrade" in c["nome"] for c in busca_nome["items"]))
check("Busca por nome não traz clientes que não batem", all("Andrade" in c["nome"] for c in busca_nome["items"]))

segmento_atrasada = requests.get(f"{BASE}/api/admin/clientes?fatura_status=atrasada&limit=100").json()
check("Filtro por segmento (fatura atrasada) só traz clientes atrasados", all(c["fatura_status"] == "atrasada" for c in segmento_atrasada["items"]))

segmento_rede = requests.get(f"{BASE}/api/admin/clientes?status_rede=instabilidade&limit=100").json()
check("Filtro por segmento (instabilidade de rede) só traz clientes com instabilidade", all(c["status_rede"] == "instabilidade" for c in segmento_rede["items"]))

# ---------------------------------------------------------------------------
section("22. Contato em massa por segmento (campanha realista em escala)")
# ---------------------------------------------------------------------------
reset()
total_atrasados_antes = requests.get(f"{BASE}/api/admin/clientes?fatura_status=atrasada&limit=100").json()["total"]
r_lote = requests.post(f"{BASE}/api/admin/iniciar-contato-lote", json={
    "channel": "whatsapp", "motivo": "retencao", "fatura_status": "atrasada",
}).json()
check("Contato em massa retorna sucesso", r_lote.get("ok") is True)
check("Contato em massa atinge todos os clientes do segmento filtrado", r_lote.get("total_enviado") == total_atrasados_antes and total_atrasados_antes > 0)

algum_id_atrasado = segmento_atrasada["items"][0]["id"] if segmento_atrasada["items"] else None
if algum_id_atrasado:
    sessao_pos_lote = requests.get(f"{BASE}/api/session/{algum_id_atrasado}").json()["session"]
    check("Cliente do segmento realmente recebeu a mensagem em massa", len(sessao_pos_lote["history"]) == 1 and sessao_pos_lote["history"][0]["author"] == "bot")

r_lote_vazio = requests.post(f"{BASE}/api/admin/iniciar-contato-lote", json={
    "channel": "whatsapp", "motivo": "retencao", "busca": "NomeQueNaoExisteDeJeitoNenhum",
}).json()
check("Contato em massa com filtro sem resultado retorna erro claro", r_lote_vazio.get("ok") is False)

# ---------------------------------------------------------------------------
section("23. RF006 — Pesquisa de satisfação (NPS) é realmente exercitada no chat")
# ---------------------------------------------------------------------------
# Este bloco existe porque, antes dele, NADA no sistema chamava /api/nps — nem o
# frontend, nem a suíte de testes. Sem isso, uma sessão nunca virava "resolved" e
# taxa_resolucao_pct ficava travada em 0% pra sempre, não importa quão bem o bot
# respondesse. Aqui validamos o fluxo completo: sinal de encerramento -> prompt
# de nota -> nota enviada -> sessão "resolved" -> KPI reflete de verdade.
reset()
requests.post(f"{BASE}/api/consent", json={"customer_id": CLIENTE_A, "accepted": True})
requests.post(f"{BASE}/api/chat", json={"customer_id": CLIENTE_A, "channel": "site", "text": "quero saber da minha fatura"})

r_fechamento = requests.post(f"{BASE}/api/chat", json={
    "customer_id": CLIENTE_A, "channel": "site", "text": "muito obrigado, era isso mesmo",
}).json()
check("Sinal de encerramento ('obrigado') dispara ask_nps=True", r_fechamento.get("ask_nps") is True)

sessao_pos_prompt = requests.get(f"{BASE}/api/session/{CLIENTE_A}").json()["session"]
check("nps continua None enquanto o cliente não respondeu a pesquisa", sessao_pos_prompt["nps"] is None)
check("A pergunta da pesquisa foi de fato registrada no histórico", "recomendaria" in sessao_pos_prompt["history"][-1]["text"])

r_nps = requests.post(f"{BASE}/api/nps", json={"customer_id": CLIENTE_A, "score": 9, "channel": "site"}).json()
check("Envio da nota retorna ok", r_nps.get("ok") is True)

sessao_pos_nps = requests.get(f"{BASE}/api/session/{CLIENTE_A}").json()["session"]
check("Sessão vira 'resolved' de verdade através do fluxo de chat (não só via chamada direta)", sessao_pos_nps["status"] == "resolved")
check("Nota fica registrada na sessão", sessao_pos_nps["nps"] == 9)

kpis_pos_nps = requests.get(f"{BASE}/api/kpis").json()
check("taxa_resolucao_pct deixa de ficar travada em 0% depois de uma avaliação real", kpis_pos_nps["taxa_resolucao_pct"] > 0)
check("nps_medio reflete a nota enviada pelo fluxo de chat", kpis_pos_nps["nps_medio"] == 9)

# Segundo cenário: cliente encerra mas NÃO responde a pesquisa — reload/troca de
# canal não deve fazer a pergunta desaparecer nem sumir sem resposta.
reset()
requests.post(f"{BASE}/api/consent", json={"customer_id": CLIENTE_A, "accepted": True})
requests.post(f"{BASE}/api/chat", json={"customer_id": CLIENTE_A, "channel": "site", "text": "quero saber da minha fatura"})
requests.post(f"{BASE}/api/chat", json={"customer_id": CLIENTE_A, "channel": "site", "text": "valeu, obrigado"})

sessao_pendente = requests.get(f"{BASE}/api/session/{CLIENTE_A}").json()["session"]
check(
    "Pesquisa pendente fica visível no histórico (front consegue reexibir o prompt ao recarregar)",
    sessao_pendente["nps"] is None and "recomendaria" in sessao_pendente["history"][-1]["text"],
)

# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print(f"RESULTADO: {len(passed)} passaram, {len(failed)} falharam")
if failed:
    print("Falharam:", ", ".join(failed))
    sys.exit(1)
else:
    print("✅ Tudo passou. MVP validado de ponta a ponta.")
