import main

def test_simulator_requires_consent(client, cliente_id):
    response=client.post('/api/plans/simulate',json={'customer_id':cliente_id})
    assert response.status_code==403

def test_plan_recommendation_catalog_and_history(client, cliente_id):
    main.get_session(cliente_id)['consent_given']=True
    response=client.post('/api/plans/simulate',json={'customer_id':cliente_id,'usage':'intenso','budget':'premium'})
    assert response.status_code==200
    data=response.json()
    assert data['recommended']=='Claro Pós 80GB'
    assert len(data['plans'])==3
    assert any('SIMULADOR_PLANOS_V1' in m['text'] for m in client.get('/api/session/'+cliente_id).json()['session']['history'])

def test_residential_preference(client, cliente_id):
    main.get_session(cliente_id)['consent_given']=True
    data=client.post('/api/plans/simulate',json={'customer_id':cliente_id,'usage':'residencial','budget':'economico'}).json()
    assert data['recommended']=='Claro Fibra 500MB'

def test_select_plan_handoff_not_contract(client, cliente_id):
    main.get_session(cliente_id)['consent_given']=True
    result=client.post('/api/plans/select',json={'customer_id':cliente_id,'plan_name':'Claro Pós 40GB'})
    assert result.status_code==200
    assert result.json()['status']=='handoff'
    queue=client.get('/api/handoff-queue').json()
    assert any('Claro Pós 40GB' in item['brief']['summary'] for item in queue if item['customer_id']==cliente_id)

def test_invalid_selection_and_profile(client, cliente_id):
    main.get_session(cliente_id)['consent_given']=True
    assert client.post('/api/plans/select',json={'customer_id':cliente_id,'plan_name':'Plano inexistente'}).status_code==422
    assert client.post('/api/plans/simulate',json={'customer_id':cliente_id,'usage':'errado'}).status_code==422
