"""Sprint 4: cobertura dos recursos novos, usando dados fictícios."""
import main


def test_context_followup(client, cliente_id):
    import main as app
    session = app.get_session(cliente_id)
    session['last_intent'] = 'suporte'
    session['consent_given'] = True
    session['history'].append({'author': 'cliente', 'channel': 'site', 'text': 'Minha internet está caindo', 'ts': 100})
    session['history'].append({'author': 'cliente', 'channel': 'site', 'text': 'pode explicar', 'ts': 101})
    result = app.contextual_classification('pode explicar', session)
    assert result['intent'] == 'suporte'
    assert result['_source'] == 'contexto'
    assert app.contextual_classification('quero cancelar', session)['intent'] == 'cancelamento'
    assert app.contextual_classification('uma coisa aleatória', session)['intent'] == 'nao_entendido'


def test_handoff_summary_and_priority(client, cliente_id):
    import main as app
    session = app.get_session(cliente_id)
    session['last_intent'] = 'suporte'
    session['status'] = 'handoff'
    session['frustration_score'] = 2
    session['history'].append({'author': 'cliente', 'channel': 'site', 'text': 'Minha internet cai todo dia', 'ts': 100})
    response = client.get('/api/handoff-queue')
    assert response.status_code == 200
    record = next(x for x in response.json() if x['customer_id'] == cliente_id)
    assert record['brief']['priority'] == 'Alta'
    assert 'internet cai' in record['brief']['summary']


def test_dashboard_comparison(client, cliente_id):
    payload = {'customer_id': cliente_id, 'channel': 'site', 'text': 'internet caindo'}
    response = client.post('/api/chat', json=payload)
    assert response.status_code == 200
    result = client.get('/api/admin/analytics?days=7').json()
    assert result['comparison']['current_messages'] >= 1
    assert result['comparison']['previous_messages'] == 0
    assert result['comparison']['change_pct'] is None
    assert 'operations' in result
    assert len(result['series']) == 7
