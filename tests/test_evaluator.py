"""Technical fixtures test the adapter, not the accuracy of AI judgements."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError
from io import BytesIO

import pytest

from app.checklist_seed import initial_checklist
from app.storage import DialogStore, read_dataset
from participant_3.evaluator import EvaluationError, OpenAIEvaluator, configured_evaluator, validated_results


@pytest.fixture
def sample(tmp_path):
    store = DialogStore(tmp_path / 'ai.sqlite3')
    store.initialize()
    store.import_dataset(read_dataset(Path(__file__).resolve().parent.parent / 'dialogs.json'))
    dialog = store.get_dialog('DEV', 'p1-ru-001')
    checklist = initial_checklist().model_dump()
    answers = [dict(criterion_id=c['id'], value=max(s['value'] for s in c['scale']) if c['answer_type'] == 'numeric' else False,
                    explanation='Техническая фикстура; не оценка диалога.',
                    evidence_message_ids=[dialog['transcript'][0]['message_id']], na_reason='') for c in checklist['criteria']]
    return dialog, checklist, {'status': 'ok', 'reason': '', 'results': answers}


def response(document):
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps(document)}]}]}


def test_minimal_input_and_dynamic_rules(sample):
    dialog, checklist, document = sample
    checklist['criteria'][0]['description'] = 'Изменённое правило из редактора'
    transport = Mock(return_value=response(document))
    evaluator = OpenAIEvaluator('test-secret', transport=transport)
    assert evaluator(dialog, checklist) == document['results']
    payload = transport.call_args.args[0]
    assert payload['store'] is False
    sent = json.loads(payload['input'][0]['content'])
    assert sent['checklist']['criteria'][0]['description'] == 'Изменённое правило из редактора'
    assert 'audio_url' not in sent
    assert 'test-secret' not in json.dumps(payload)
    assert sent['transcript'] == dialog['transcript']
    assert sent['company_knowledge'] is None


@pytest.mark.parametrize('issue', ['missing', 'duplicate', 'wrong_evidence', 'wrong_scale', 'bool_as_number', 'na_forbidden', 'na_reason_missing'])
def test_invalid_ai_results_are_rejected(sample, issue):
    dialog, checklist, document = sample
    rows = document['results']
    if issue == 'missing': rows.pop()
    if issue == 'duplicate': rows.append(deepcopy(rows[0]))
    if issue == 'wrong_evidence': rows[0]['evidence_message_ids'] = ['not-in-dialog']
    if issue == 'wrong_scale': rows[5]['value'] = 9
    if issue == 'bool_as_number': rows[5]['value'] = True
    if issue == 'na_forbidden': rows[0].update(value=None, na_reason='Тест')
    if issue == 'na_reason_missing': rows[1]['value'] = None
    with pytest.raises(EvaluationError):
        validated_results(document, dialog, checklist)


def test_uncertainty_is_not_na_or_zero(sample):
    dialog, checklist, _ = sample
    with pytest.raises(EvaluationError, match='Требуется проверка человеком'):
        validated_results({'status': 'needs_review', 'reason': 'Нужны правила компании для проверки тарифа.', 'results': []}, dialog, checklist)


@pytest.mark.parametrize('raw', [
    {'status': 'incomplete', 'output': []},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'test'}]}]},
    {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'invalid'}]}]},
])
def test_incomplete_refusal_and_invalid_json(sample, raw):
    dialog, checklist, _ = sample
    with pytest.raises(EvaluationError):
        OpenAIEvaluator('test', transport=lambda _: raw)(dialog, checklist)


def test_input_is_never_silently_truncated(sample):
    dialog, checklist, _ = sample
    dialog['transcript'][0]['text'] = 'x' * 250_001
    transport = Mock()
    with pytest.raises(EvaluationError) as exc:
        OpenAIEvaluator('test', transport=transport)(dialog, checklist)
    assert exc.value.status == 413
    transport.assert_not_called()


def test_requires_key_and_explicit_enable(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.delenv('CQA_ENABLE_AI', raising=False)
    assert configured_evaluator() is None
    monkeypatch.setenv('CQA_ENABLE_AI', '1')
    monkeypatch.delenv('CQA_KNOWLEDGE_PATH', raising=False)
    assert configured_evaluator().model_name == 'gpt-6.1-sol'
    monkeypatch.setenv('OPENAI_API_KEY', '')
    assert configured_evaluator() is None


def test_provider_failure_hides_secret(monkeypatch, sample):
    import participant_3.evaluator as module
    transport = Mock()
    transport.open.side_effect = HTTPError('private-url', 401, 'test-secret', {}, None)
    monkeypatch.setattr(module, 'build_opener', lambda *args: transport)
    with pytest.raises(EvaluationError) as exc:
        OpenAIEvaluator('test-secret')(sample[0], sample[1])
    assert 'Ключ' in str(exc.value)
    assert 'test-secret' not in str(exc.value)


def test_swap_waits_for_organizer_instructions(sample):
    dialog, checklist, _ = sample
    dialog['dataset_group_id'] = 'SWAP'
    transport = Mock()
    with pytest.raises(EvaluationError, match='SWAP'):
        OpenAIEvaluator('test', transport=transport)(dialog, checklist)
    transport.assert_not_called()


@pytest.mark.parametrize('code, phrase', [
    ('insufficient_quota', 'Недостаточно квоты'),
    ('credit_balance_exhausted', 'предоплаченные'),
    ('project_spend_limit_exceeded', 'API-проекта'),
    ('rate_limit_exceeded', 'в минуту'),
    ('slow_down', 'слишком быстро'),
])
def test_provider_429_diagnostics(code, phrase):
    from participant_3.evaluator import provider_error
    body = json.dumps({'error': {'code': code, 'message': 'test-secret-do-not-expose'}}).encode()
    exc = HTTPError('private-url', 429, '', {'Retry-After': '30'}, BytesIO(body))
    error = provider_error(exc)
    assert error.status == 429
    assert code in str(error) and phrase in str(error)
    assert 'test-secret' not in str(error)
    if code in ('rate_limit_exceeded', 'slow_down'):
        assert '30 с.' in str(error)
