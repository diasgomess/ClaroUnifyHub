def test_kpis_are_zeroed_on_fresh_database(client):
    res = client.get("/api/kpis")
    assert res.status_code == 200
    body = res.json()
    assert body["total_conversas"] == 0
    assert body["taxa_resolucao_pct"] == 0.0
    assert body["mensagens_por_canal"] == {"site": 0, "whatsapp": 0}


def test_kpis_reflect_real_conversation(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "oi"})

    res = client.get("/api/kpis")
    body = res.json()
    assert body["total_conversas"] == 1
    assert body["mensagens_por_canal"]["site"] == 1


def test_handoff_queue_lists_customer_after_explicit_handoff(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "quero falar com atendente",
    })

    res = client.get("/api/handoff-queue")
    assert res.status_code == 200
    fila = res.json()
    assert len(fila) == 1
    assert fila[0]["customer_id"] == cliente_id
    # Minimização de dados: nome vem mascarado, não em claro.
    assert fila[0]["nome"] != "João Pereira"


def test_assumir_handoff_moves_customer_out_of_queue(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "quero falar com atendente",
    })

    res = client.post(f"/api/handoff-queue/{cliente_id}/assumir")
    assert res.status_code == 200

    fila = client.get("/api/handoff-queue").json()
    assert fila == []


def test_alertas_endpoint_returns_empty_list_on_healthy_demo(client):
    res = client.get("/api/alertas")
    assert res.status_code == 200
    assert res.json()["alertas"] == []


def test_reset_clears_sessions_and_events(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "oi"})
    assert client.get("/api/kpis").json()["total_conversas"] == 1

    res = client.post("/api/reset")
    assert res.status_code == 200

    assert client.get("/api/kpis").json()["total_conversas"] == 0
    assert client.get("/api/events").json() == []


def test_admin_clientes_lists_seeded_customer(client, cliente_id):
    res = client.get("/api/admin/clientes?limit=100")
    assert res.status_code == 200
    ids = [c["id"] for c in res.json()["items"]]
    assert cliente_id in ids


def test_admin_clientes_pagination_respects_limit(client):
    res = client.get("/api/admin/clientes?limit=5")
    body = res.json()
    assert len(body["items"]) <= 5
    assert body["total"] >= len(body["items"])


# ---------------------------------------------------------------------------
# RF006 — Pesquisa de satisfação (NPS): sem isso, uma sessão nunca virava
# "resolved" e taxa_resolucao_pct ficava travada em 0% pra sempre, não importa
# quão bem o bot respondesse. Estes testes garantem que o fluxo é exercitado
# de ponta a ponta através do chat, não só chamando /api/nps isoladamente.
# ---------------------------------------------------------------------------

def test_closing_signal_in_chat_triggers_nps_prompt(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "quero saber da fatura"})

    res = client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "muito obrigado, era isso mesmo",
    })
    assert res.json()["ask_nps"] is True


def test_nps_prompt_pending_shows_in_history_until_answered(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "quero saber da fatura"})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "valeu, obrigado"})

    session = client.get(f"/api/session/{cliente_id}").json()["session"]
    assert session["nps"] is None
    assert "recomendaria" in session["history"][-1]["text"]


def test_nps_submission_resolves_session_and_feeds_kpis(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "quero saber da fatura"})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "obrigado, era isso"})

    res = client.post("/api/nps", json={"customer_id": cliente_id, "score": 9, "channel": "site"})
    assert res.status_code == 200
    assert res.json()["ok"] is True

    session = client.get(f"/api/session/{cliente_id}").json()["session"]
    assert session["status"] == "resolved"
    assert session["nps"] == 9

    kpis = client.get("/api/kpis").json()
    assert kpis["taxa_resolucao_pct"] > 0
    assert kpis["nps_medio"] == 9


def test_nps_score_out_of_range_is_rejected(client, cliente_id):
    res = client.post("/api/nps", json={"customer_id": cliente_id, "score": 15, "channel": "site"})
    assert res.status_code == 422
