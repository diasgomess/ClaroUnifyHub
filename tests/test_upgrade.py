"""Regression tests for production admin gates, analytics and handoff."""
import importlib
from fastapi.testclient import TestClient


def test_admin_login_and_protected_routes(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'secure.db'))
    monkeypatch.setenv('USE_LLM', 'false')
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('ADMIN_AUTH_DISABLED', 'false')
    monkeypatch.setenv('ADMIN_PASSWORD', 'Secret-stored-only-in-Railway!')
    monkeypatch.setenv('ADMIN_SESSION_SECRET', 'f8d30546a2c9ff84e87b801f8e4d7f8958bbd52e')
    import db, main
    importlib.reload(db)
    importlib.reload(main)
    with TestClient(main.app) as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/admin/clientes').status_code == 401
        assert client.get('/api/kpis').status_code == 401
        assert client.post('/api/reset').status_code == 401
        assert client.get('/api/demo/clientes').status_code == 200
        assert client.post('/api/admin/login', json={'password':'wrong'}).status_code == 401
        result = client.post('/api/admin/login', json={'password':'Secret-stored-only-in-Railway!'})
        assert result.status_code == 200
        assert 'httponly' in result.headers['set-cookie'].lower()
        assert client.get('/api/admin/clientes').status_code == 200
        stats = client.get('/api/admin/analytics?days=7')
        assert stats.status_code == 200 and len(stats.json()['series']) == 7
        assert client.post('/api/admin/logout').status_code == 200
        assert client.get('/api/kpis').status_code == 401


def test_handoff_reply_requires_assigned_agent(client, cliente_id):
    assert client.post(f'/api/handoff-queue/{cliente_id}/responder', json={'text':'Hello'}).status_code == 409
    assert client.get('/api/admin/analytics?days=500').json()['days'] == 90
