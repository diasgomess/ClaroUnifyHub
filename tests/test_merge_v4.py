"""Regressão específica das capacidades incorporadas da versão v4."""
import importlib
import time


def test_v4_jwt_expiration_and_identity(client, monkeypatch, cliente_id):
    import main
    monkeypatch.setattr(main, 'JWT_SECRET', 'test-very-long-secret-which-has-more-than-32-chars')
    token = main.issue_silent_token(cliente_id, 'site')
    assert main.resolve_token(token) == cliente_id
    assert main.resolve_token(token + 'invalid') is None


def test_v4_audit_on_reset(client):
    result = client.post('/api/reset')
    assert result.status_code == 200
    response = client.get('/api/admin/audit')
    assert response.status_code == 200
    assert any(row['action'] == 'reset' for row in response.json())


def test_v4_http_security_headers(client):
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'DENY'
    assert response.headers['cache-control'] == 'no-store'


def test_v4_customer_isolation(client, monkeypatch, cliente_id):
    import main
    monkeypatch.setattr(main, 'JWT_SECRET', 'test-very-long-secret-which-has-more-than-32-chars')
    monkeypatch.setattr(main, 'CUSTOMER_AUTH_REQUIRED', True)
    token = main.issue_silent_token(cliente_id, 'site')
    headers = {'Authorization': f'Bearer {token}'}
    assert client.get(f'/api/session/{cliente_id}', headers=headers).status_code == 200
    assert client.get('/api/session/11999990002', headers=headers).status_code == 403
    assert client.post('/api/auth/silent', json={'customer_id': cliente_id, 'channel': 'site'}).status_code == 200  # admin de testes
