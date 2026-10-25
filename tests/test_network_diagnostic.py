"""Diagnóstico simulado integrado, com consentimento e histórico persistente."""
import main


def test_requires_consent(client, cliente_id):
    res = client.post('/api/network/diagnostic', json={'customer_id': cliente_id, 'channel': 'site'})
    assert res.status_code == 403


def test_network_diagnostic_persists_and_handoff_summary(client, cliente_id):
    import main as app
    session = app.get_session(cliente_id)
    session['consent_given'] = True
    res = client.post('/api/network/diagnostic', json={'customer_id': cliente_id, 'channel': 'site'})
    assert res.status_code == 200
    data = res.json()
    assert data['source'] == 'OSS simulado'
    assert data['status'] in ('instabilidade', 'normal', 'indisponivel')
    history = client.get('/api/session/' + cliente_id).json()['session']['history']
    assert any(x['text'].startswith(app.DIAGNOSTIC_PREFIX) for x in history)
    session['status'] = 'handoff'
    queue = client.get('/api/handoff-queue').json()
    assert next(item for item in queue if item['customer_id'] == cliente_id)['brief']['network_diagnostic'] == data['label']


def test_unknown_network_data(client, cliente_id, monkeypatch):
    import main as app
    monkeypatch.setattr(app.db, 'get_network_status', lambda _: None)
    session = app.get_session(cliente_id)
    session['consent_given'] = True
    data = client.post('/api/network/diagnostic', json={'customer_id': cliente_id}).json()
    assert data['status'] == 'indisponivel'
    assert data['eta'] is None


def test_reject_invalid_channel(client, cliente_id):
    import main as app
    app.get_session(cliente_id)['consent_given'] = True
    assert client.post('/api/network/diagnostic', json={'customer_id': cliente_id, 'channel': 'sms'}).status_code == 422
