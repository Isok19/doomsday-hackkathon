"""Проверки контракта на изолированной БД.

Технические ответы ниже нужны только для проверки арифметики и переходов состояний.
Они не являются оценкой качества выданных диалогов и не попадают в рабочую БД.
"""

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.workflows import calculate

SOURCE = Path(__file__).resolve().parent.parent / "dialogs.json"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(SOURCE, tmp_path / "workflows.sqlite3")) as client:
        yield client


def checklist_body(checklist):
    return deepcopy({key: checklist[key] for key in ("name", "description", "criteria")})


def technical_payload(client):
    checklist = client.get('/api/checklists/cqa-default').json()
    first = client.get('/api/dialogs?limit=1').json()['items'][0]
    dialog = client.get('/api/dialogs/' + first['conversation_id']).json()
    results = []
    for criterion in checklist['criteria']:
        value = max(s['value'] for s in criterion['scale']) if criterion['answer_type'] == 'numeric' else criterion['answer_type'] != 'flag'
        results.append(dict(criterion_id=criterion['id'], value=value,
                            explanation='Техническая фикстура теста, не содержательная оценка диалога.',
                            evidence_message_ids=[dialog['transcript'][0]['message_id']], na_reason=''))
    return dict(dataset_group_id='DEV', conversation_id=dialog['conversation_id'],
                checklist_id=checklist['id'], checklist_version=checklist['version'], results=results)


def test_frontend_and_read_only_capabilities(client):
    assert client.get('/').status_code == 200
    for path in ('app.js', 'shared/styles.css', 'shared/api.js', 'participant-1/dialogs.js',
                 'participant-1/results.js', 'participant-4/checklists.js', 'participant-4/appeals.js'):
        assert client.get('/frontend/' + path).status_code == 200
    assert client.get('/api/capabilities').json()['evaluation_available'] is False
    request = technical_payload(client)
    request.pop('results')
    assert client.post('/api/evaluations/run', json=request).status_code == 503
    assert client.get('/api/evaluations', params={'dataset_group_id': 'DEV', 'conversation_id': request['conversation_id']}).json() == []


def test_seed_exact_rules_and_versions(client):
    first = client.get('/api/checklists/cqa-default').json()
    assert [c['weight'] for c in first['criteria']] == [10, 15, 20, 20, 15, 15, 5, 0]
    assert [c['id'] for c in first['criteria'] if c['allow_na']] == ['needs', 'identity', 'consultation', 'closing']
    assert [s['value'] for s in first['criteria'][5]['scale']] == [0, 4, 7, 10]
    body = checklist_body(first)
    body['criteria'][0]['name'] = 'Изменённый критерий для проверки версий'
    response = client.put('/api/checklists/cqa-default', json={**body, 'base_version': 1})
    assert response.status_code == 200
    assert response.json()['version'] == 2
    assert client.get('/api/checklists/cqa-default?version=1').json() == first
    assert client.put('/api/checklists/cqa-default', json={**body, 'base_version': 1}).status_code == 409
    assert client.get('/api/checklists/cqa-default?version=3').status_code == 404


@pytest.mark.parametrize('invalid', ['na', 'scale', 'flag', 'ids', 'blank'])
def test_invalid_checklist_is_rejected(client, invalid):
    body = checklist_body(client.get('/api/checklists/cqa-default').json())
    if invalid == 'na': body['criteria'][0]['allow_na'] = True
    if invalid == 'scale': body['criteria'][3]['scale'][1]['value'] = 0
    if invalid == 'flag': body['criteria'][-1]['weight'] = 10
    if invalid == 'ids': body['criteria'][1]['id'] = body['criteria'][0]['id']
    if invalid == 'blank': body['name'] = '  '
    assert client.post('/api/checklists', json=body).status_code == 422
    assert len(client.get('/api/checklists').json()) == 1


def test_new_criteria_flow_into_results_without_code_changes(client):
    body = checklist_body(client.get('/api/checklists/cqa-default').json())
    body['criteria'] = [body['criteria'][0]]
    body['criteria'][0]['id'] = 'new-criterion'
    created = client.post('/api/checklists', json=body)
    assert created.status_code == 201
    payload = technical_payload(client)
    payload.update(checklist_id=created.json()['id'], checklist_version=1)
    payload['results'] = [payload['results'][0]]
    payload['results'][0]['criterion_id'] = 'new-criterion'
    saved = client.post('/api/evaluations', json=payload)
    assert saved.status_code == 201
    assert saved.json()['original']['total'] == 100


def test_na_weight_scaling_and_flag(client):
    payload = technical_payload(client)
    by_id = {r['criterion_id']: r for r in payload['results']}
    by_id['identity'].update(value=None, na_reason='Технический случай неприменимости для проверки перерасчёта.')
    by_id['management']['value'] = 7
    by_id['critical']['value'] = True
    response = client.post('/api/evaluations', json=payload)
    assert response.status_code == 201, response.text
    result = response.json()['original']
    assert result['total'] == 94.38
    assert result['critical_errors'] == ['critical']
    management = next(r for r in result['results'] if r['criterion_id'] == 'management')
    assert management['effective_weight'] == 18.75
    assert management['points'] == 13.13
    assert sum(s['max_points'] for s in result['sections']) == 100


@pytest.mark.parametrize('invalid', ['na', 'bool_numeric', 'scale', 'evidence', 'duplicate', 'missing'])
def test_invalid_evaluation_is_rejected(client, invalid):
    payload = technical_payload(client)
    if invalid == 'na': payload['results'][0].update(value=None, na_reason='Нет')
    if invalid == 'bool_numeric': payload['results'][3]['value'] = True
    if invalid == 'scale': payload['results'][5]['value'] = 9
    if invalid == 'evidence': payload['results'][0]['evidence_message_ids'] = ['missing-message']
    if invalid == 'duplicate': payload['results'][1]['criterion_id'] = payload['results'][0]['criterion_id']
    if invalid == 'missing': payload['results'].pop()
    assert client.post('/api/evaluations', json=payload).status_code == 422


@pytest.mark.parametrize('action', ['confirm', 'change'])
def test_appeal_decision_preserves_original_and_recalculates(client, action):
    payload = technical_payload(client)
    evaluation = client.post('/api/evaluations', json=payload).json()
    appeal_body = dict(evaluation_id=evaluation['id'], criterion_id='contact', comment='Проверка процесса апелляции в тесте')
    response = client.post('/api/appeals', json=appeal_body)
    assert response.status_code == 201
    appeal = response.json()
    assert appeal['status'] == 'open'
    assert client.post('/api/appeals', json=appeal_body).status_code == 409
    decision = dict(action=action, reason='Проверка сохранения причины решения')
    if action == 'change': decision['replacement'] = {**payload['results'][0], 'value': False}
    response = client.post(f"/api/appeals/{appeal['id']}/decision", json=decision)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'resolved'
    updated = client.get(f"/api/evaluations/{evaluation['id']}").json()
    assert updated['original'] == evaluation['original']
    assert updated['final']['total'] == (90 if action == 'change' else 100)
    assert client.post(f"/api/appeals/{appeal['id']}/decision", json=decision).status_code == 409


def test_decision_rejects_invalid_replacement(client):
    payload = technical_payload(client)
    evaluation = client.post('/api/evaluations', json=payload).json()
    appeal = client.post('/api/appeals', json=dict(evaluation_id=evaluation['id'], criterion_id='contact', comment='Техническая проверка')).json()
    decision = dict(action='change', reason='Проверка', replacement={**payload['results'][0], 'value': None, 'na_reason': 'Недопустимый N/A'})
    assert client.post(f"/api/appeals/{appeal['id']}/decision", json=decision).status_code == 422
    assert client.get(f"/api/appeals/{appeal['id']}").json()['status'] == 'open'


def test_persistence_and_snapshot_after_restart(tmp_path):
    path = tmp_path / 'persistent.sqlite3'
    with TestClient(create_app(SOURCE, path)) as client:
        payload = technical_payload(client)
        evaluation = client.post('/api/evaluations', json=payload).json()
        body = checklist_body(evaluation['checklist'])
        body['criteria'][0]['weight'] = 99
        assert client.put('/api/checklists/cqa-default', json={**body, 'base_version': 1}).status_code == 200
        appeal = client.post('/api/appeals', json=dict(evaluation_id=evaluation['id'], criterion_id='contact', comment='Проверка перезапуска')).json()
        client.post(f"/api/appeals/{appeal['id']}/decision", json=dict(action='confirm', reason='Проверка истории'))
    with TestClient(create_app(SOURCE, path)) as client:
        assert client.get('/api/checklists/cqa-default').json()['version'] == 2
        result = client.get(f"/api/evaluations/{evaluation['id']}").json()
        assert result['original'] == evaluation['original']
        assert result['checklist']['version'] == 1
        assert result['appeals'][0]['decision']['reason'] == 'Проверка истории'


def test_dataset_upload_and_group_isolation(client):
    import json
    source = json.loads(SOURCE.read_text())
    response = client.post('/api/datasets/import', content=json.dumps(source))
    assert response.status_code == 201
    assert response.json()['total_records'] == 65
    source['data']['dataset_group_id'] = 'TEST-ONLY'
    assert client.post('/api/datasets/import', json=source).status_code == 201
    assert len(client.get('/api/datasets').json()) == 2
    assert client.get('/api/dialogs?dataset_group_id=TEST-ONLY').json()['total'] == 65
    assert client.get('/api/dialogs?dataset_group_id=missing').status_code == 404
    assert client.post('/api/datasets/import', content='{').status_code == 422
    assert client.post('/api/datasets/import', content=b' ' * (10 * 1024 * 1024 + 1)).status_code == 413


def test_all_na_and_no_weight_do_not_produce_score(client):
    checklist = client.get('/api/checklists/cqa-default').json()
    checklist['criteria'] = [checklist['criteria'][1]]
    result = calculate(checklist, [dict(criterion_id='needs', value=None, explanation='Тест', evidence_message_ids=['test'], na_reason='Тест')])
    assert result['total'] is None


def test_configured_evaluator_contract(client):
    payload = technical_payload(client)
    client.app.state.evaluator = lambda dialog, checklist: payload['results']
    request = {key: value for key, value in payload.items() if key != 'results'}
    response = client.post('/api/evaluations/run', json=request)
    assert response.status_code == 201
    assert response.json()['final']['total'] == 100
