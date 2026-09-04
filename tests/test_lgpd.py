def test_export_returns_crm_faturas_and_session(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "oi"})

    res = client.get(f"/api/lgpd/exportar/{cliente_id}")
    assert res.status_code == 200
    body = res.json()
    assert body["crm"]["id"] == cliente_id
    assert body["faturas"] is not None
    assert body["sessao_conversacional"]["customer_id"] == cliente_id
    assert len(body["sessao_conversacional"]["history"]) > 0


def test_export_session_is_none_when_no_session_exists(client):
    novo_cliente = "11999990099"
    res = client.get(f"/api/lgpd/exportar/{novo_cliente}")
    assert res.status_code == 200
    assert res.json()["sessao_conversacional"] is None


def test_delete_removes_session_but_keeps_crm(client, cliente_id):
    client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    client.post("/api/chat", json={"customer_id": cliente_id, "channel": "site", "text": "oi"})

    res = client.delete(f"/api/lgpd/excluir/{cliente_id}")
    assert res.status_code == 200
    assert res.json()["existia"] is True

    # CRM (sistema de registro fora do escopo do Hub) continua intacto.
    export_res = client.get(f"/api/lgpd/exportar/{cliente_id}")
    assert export_res.json()["crm"] is not None
    # A sessão conversacional foi de fato apagada.
    assert export_res.json()["sessao_conversacional"] is None


def test_delete_on_customer_without_session_reports_not_existed(client, cliente_id):
    res = client.delete(f"/api/lgpd/excluir/{cliente_id}")
    assert res.status_code == 200
    assert res.json()["existia"] is False


def test_consent_is_recorded_per_customer(client, cliente_id):
    res = client.post("/api/consent", json={"customer_id": cliente_id, "accepted": True})
    assert res.status_code == 200

    session_res = client.get(f"/api/session/{cliente_id}")
    assert session_res.json()["session"]["consent_given"] is True
