"""Appendix example is only an arithmetic fixture, never a production assessment."""

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.storage import DialogStore, read_dataset
from app.workflow_models import ChecklistInput, EvaluationInput
from app.workflows import WorkflowError, WorkflowStore
from participant_3.batch import run_batch
from participant_3.evaluator import EvaluationError
from participant_3.submission import build_submission, dataset_dialogs, validate_submission


@pytest.fixture
def workflows(tmp_path):
    store = DialogStore(tmp_path / 'submission.sqlite3')
    store.initialize()
    store.import_dataset(read_dataset(Path(__file__).resolve().parent.parent / 'dialogs.json'))
    workflows = WorkflowStore(store)
    workflows.initialize()
    return workflows


def save_fixture(workflows):
    checklist = workflows.checklist('cqa-default', 1)
    dialog = workflows.dialogs.get_dialog('DEV', 'p1-ru-001')
    values = [True, None, None, 15, True, 7, True, False]
    answers = [dict(criterion_id=c['id'], value=value, explanation='Только технический тест из приложения PDF.',
                    evidence_message_ids=[dialog['transcript'][0]['message_id']], na_reason='Тест N/A' if value is None else '')
               for c, value in zip(checklist['criteria'], values)]
    return workflows.create_evaluation(EvaluationInput(dataset_group_id='DEV', conversation_id=dialog['conversation_id'],
        checklist_id='cqa-default', checklist_version=1, results=answers,
        model={'mode': 'local', 'name': 'test-fixture-not-ai'}))


def test_exact_appendix_codes_score_and_all_ids(workflows):
    save_fixture(workflows)
    payload = build_submission(workflows, 'DEV', 'dev', 'zenoxxk')
    assert payload['team_name'] == 'zenoxxk'
    assert len(payload['results']) == 65
    first = payload['results'][0]
    assert first['score'] == {'final_score': 85.38, 'critical_flag': False}
    assert [c['criterion_id'] for c in first['criteria']] == ['contact', 'discovery', 'identification', 'consultation', 'empathy', 'dialogue', 'closure', 'critical']
    assert first['criteria'][0]['evidence_turns'] == [1]
    assert all(r['status'] == 'error' and r['criteria'] == [] and r['score'] is None for r in payload['results'][1:])
    assert 'audio_url' not in str(payload) and 'transcript' not in str(payload)


@pytest.mark.parametrize('issue', ['id_duplicate', 'id_missing', 'extra_id', 'criterion', 'turn_zero', 'turn_large', 'turn_bool', 'score', 'flag', 'error_is_zero'])
def test_submission_rejects_invalid_rows(workflows, issue):
    save_fixture(workflows)
    payload = build_submission(workflows, 'DEV', 'dev', 'zenoxxk')
    rows = payload['results']
    if issue == 'id_duplicate': rows[1]['conversation_id'] = rows[0]['conversation_id']
    if issue == 'id_missing': rows.pop()
    if issue == 'extra_id': rows[-1]['conversation_id'] = 'unexpected'
    if issue == 'criterion': rows[0]['criteria'][1]['criterion_id'] = 'needs'
    if issue == 'turn_zero': rows[0]['criteria'][0]['evidence_turns'] = [0]
    if issue == 'turn_large': rows[0]['criteria'][0]['evidence_turns'] = [9999]
    if issue == 'turn_bool': rows[0]['criteria'][0]['evidence_turns'] = [True]
    if issue == 'score': rows[0]['score']['final_score'] = 99
    if issue == 'flag': rows[0]['score']['critical_flag'] = True
    if issue == 'error_is_zero': rows[1]['score'] = {'final_score': 0, 'critical_flag': False}
    with pytest.raises((WorkflowError, ValidationError)):
        validate_submission(payload, dataset_dialogs(workflows.dialogs, 'DEV', 'dev'), 'dev')


def test_wrong_dataset_or_modified_weights_cannot_be_submitted(workflows):
    save_fixture(workflows)
    with pytest.raises(WorkflowError):
        build_submission(workflows, 'DEV', 'prod', 'zenoxxk')
    checklist = workflows.checklist('cqa-default')
    body = {key: deepcopy(checklist[key]) for key in ('name', 'description', 'criteria')}
    body['criteria'][0]['weight'] = 11
    workflows.save_checklist(ChecklistInput(**body), 'cqa-default', 1)
    with pytest.raises(WorkflowError):
        build_submission(workflows, 'DEV', 'dev', 'zenoxxk', version=2)


def test_empty_production_db_does_not_produce_fake_file(workflows):
    with pytest.raises(WorkflowError, match='Нет результатов'):
        build_submission(workflows, 'DEV', 'dev', 'zenoxxk')
    with pytest.raises(WorkflowError, match='SWAP'):
        build_submission(workflows, 'SWAP', 'prod', 'zenoxxk')


def test_failure_survives_export_and_is_not_replaced_by_zero(workflows):
    save_fixture(workflows)
    workflows.record_failure('DEV', 'p1-ru-001', 'cqa-default', 1,
                             {'mode': 'external_api', 'name': 'test-model'}, 'Тестовая ошибка модели')
    result = build_submission(workflows, 'DEV', 'dev', 'zenoxxk')['results'][0]
    assert result == {'conversation_id': 'p1-ru-001', 'status': 'error', 'criteria': [], 'score': None, 'error': 'Тестовая ошибка модели'}
    save_fixture(workflows)
    assert build_submission(workflows, 'DEV', 'dev', 'zenoxxk')['results'][0]['status'] == 'ok'


def test_prod_batch_keeps_all_ten_chats_when_model_fails(workflows):
    source = read_dataset(Path(__file__).resolve().parent.parent / 'dialogs.json').model_copy(deep=True)
    source.data.dataset_group_id = 'CONTROL-TEST'
    source.data.records = [r for r in source.data.records if r.channel == 'agent chat'][:10]
    source.data.total_records = 10
    workflows.dialogs.import_dataset(source)
    class Unavailable:
        metadata = {'mode': 'local', 'name': 'test-failure-only'}
        def __call__(self, dialog, checklist):
            raise EvaluationError('Тестовый отказ подключения')
    progress = []
    payload = run_batch(workflows, Unavailable(), 'CONTROL-TEST', 'prod', 'zenoxxk', 'cqa-default', 1, True,
                        lambda *args: progress.append(args))
    assert len(payload['results']) == len(progress) == 10
    assert all(row['status'] == 'error' for row in payload['results'])
    assert payload == build_submission(workflows, 'CONTROL-TEST', 'prod', 'zenoxxk')
