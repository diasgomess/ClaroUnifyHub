def test_home_serves_frontend(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ClaroUnify" in res.text


def test_llm_status_reports_fallback_when_llm_disabled(client):
    res = client.get("/api/llm-status")
    assert res.status_code == 200
    assert res.json()["modo_ativo"] == "palavras-chave (fallback)"


def test_chat_requires_consent_before_answering(client, cliente_id):
    res = client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "oi",
    })
    assert res.status_code == 200
    assert res.json()["needs_consent"] is True


def test_chat_answers_after_consent_given(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})

    res = client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "quero a segunda via da minha fatura",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["needs_consent"] is False
    assert body["intent"] == "informacao"


def test_chat_refusing_consent_still_responds_without_leaking_data(client, cliente_id):
    res = client.post("/api/consent", json={"customer_id": cliente_id, "accepted": False})
    assert res.status_code == 200

    res = client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "quero a segunda via da minha fatura",
    })
    assert res.status_code == 200
    # Sem consentimento, needs_consent continua sinalizado — o bot não deve
    # seguir para dados pessoais.
    assert res.json()["needs_consent"] is True


def test_chat_detects_cancelamento_intent(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    res = client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "quero cancelar meu plano",
    })
    assert res.json()["intent"] == "cancelamento"


def test_chat_explicit_handoff_request_escalates(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    res = client.post("/api/chat", json={
        "customer_id": cliente_id, "channel": "site", "text": "quero falar com atendente humano",
    })
    body = res.json()
    assert body["handoff"] is True
    assert body["status"] == "handoff"


def test_chat_history_persists_across_requests(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "oi"})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "quero saber da fatura"})

    res = client.get(f"/api/session/{cliente_id}")
    history = res.json()["session"]["history"]
    autores = [m["author"] for m in history]
    assert "cliente" in autores and "bot" in autores
    assert len(history) >= 4


def test_rate_limit_blocks_after_threshold(client, cliente_id, monkeypatch):
    import main as main_module
    monkeypatch.setattr(main_module, "RATE_LIMIT_MAX_MSGS", 2)

    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    for _ in range(2):
        r = client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "oi"})
        assert r.status_code == 200

    r = client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "oi"})
    # Independente do texto exato de resposta, o rate limit deve ser refletido
    # de alguma forma (via evento) — validado indiretamente pelo /api/events.
    events = client.get("/api/events?limit=50").json()
    assert any(e["type"] == "rate_limit_bloqueado" for e in events)
