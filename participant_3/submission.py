"""Appendix Results 1.0: strict export/validation, without transcripts or audio links."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictInt, model_validator

from app.checklist_seed import initial_checklist
from app.workflow_models import Answer, InputModel
from app.workflows import WorkflowError, calculate, validate_answer

CRITERION_CODES = {"contact": "contact", "needs": "discovery", "identity": "identification",
                   "consultation": "consultation", "empathy": "empathy", "management": "dialogue",
                   "closing": "closure", "critical": "critical"}


class SubmissionModel(InputModel):
    mode: Literal['local', 'external_api', 'hybrid']
    name: str = Field(min_length=1)


class SubmissionCriterion(InputModel):
    criterion_id: str
    value: StrictBool | StrictInt | StrictFloat | None
    rationale: str = Field(min_length=1)
    evidence_turns: list[StrictInt] = Field(min_length=1)


class Score(InputModel):
    final_score: StrictFloat | StrictInt = Field(ge=0, le=100)
    critical_flag: StrictBool


class SubmissionRow(InputModel):
    conversation_id: str = Field(min_length=1)
    status: Literal['ok', 'error']
    criteria: list[SubmissionCriterion]
    score: Score | None
    error: str | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.status == 'error':
            if self.criteria or self.score is not None or not self.error or not self.error.strip():
                raise ValueError('Ошибка требует пустого criteria, score=null и причины')
        elif self.error is not None or self.score is None:
            raise ValueError('Успешный результат требует score и не содержит error')
        return self


class Submission(InputModel):
    schema_version: Literal['1.0']
    team_name: str = Field(min_length=1)
    model: SubmissionModel
    results: list[SubmissionRow]


def dataset_dialogs(dialogs, group, split):
    if group == 'SWAP':
        raise WorkflowError('SWAP нельзя включать в DEV или PROD до отдельной инструкции организаторов')
    if split not in ('dev', 'prod'):
        raise WorkflowError('Выберите dev или prod')
    stats = dialogs.stats(group)
    if split == 'dev':
        valid = stats['total_records'] == 65 and stats['by_channel'] == {'agent chat': 60, 'voice call': 5}
    else:
        valid = stats['total_records'] == 10 and stats['by_channel'] == {'agent chat': 10}
    if not valid:
        raise WorkflowError('Для DEV нужны 60 чатов и 5 звонков; для PROD — ровно 10 чатов')
    return [dialogs.get_dialog(group, item['conversation_id'])
            for item in dialogs.list_dialogs(group, None, None, 100, 0)['items']]


def require_official_rules(checklist):
    actual = {c['id']: c for c in checklist['criteria']}
    seed = initial_checklist().model_dump()['criteria']
    if set(actual) != set(CRITERION_CODES):
        raise WorkflowError('Для сдачи нужны все восемь исходных критериев')
    for expected in seed:
        current = actual[expected['id']]
        if (any(current[key] != expected[key] for key in ('weight', 'answer_type', 'allow_na'))
                or sorted(s['value'] for s in current['scale']) != sorted(s['value'] for s in expected['scale'])):
            raise WorkflowError('Веса, шкалы или допустимость N/A отличаются от формата сдачи')


def validate_submission(payload, source_dialogs, split):
    document = Submission.model_validate(payload)
    expected_count = 65 if split == 'dev' else 10 if split == 'prod' else 0
    ids = [row.conversation_id for row in document.results]
    source = {d['conversation_id']: d for d in source_dialogs}
    if not expected_count or len(ids) != expected_count or len(ids) != len(set(ids)) or set(ids) != set(source):
        raise WorkflowError('Есть пропущенные, повторяющиеся или посторонние ID диалогов')
    checklist = initial_checklist().model_dump()
    reverse = {official: internal for internal, official in CRITERION_CODES.items()}
    for row in document.results:
        if row.status == 'error':
            continue
        criterion_ids = [criterion.criterion_id for criterion in row.criteria]
        if len(criterion_ids) != 8 or set(criterion_ids) != set(reverse):
            raise WorkflowError('Нужны все восемь кодов критериев из дополнения, без повторов')
        messages = source[row.conversation_id]['transcript']
        positions = {message['position']: message['message_id'] for message in messages}
        answers = []
        for criterion in row.criteria:
            turns = criterion.evidence_turns
            if len(turns) != len(set(turns)) or not set(turns).issubset(positions):
                raise WorkflowError('Номер доказательства вне исходного транскрипта или повторяется')
            answer = Answer(criterion_id=reverse[criterion.criterion_id], value=criterion.value,
                            explanation=criterion.rationale, evidence_message_ids=[positions[t] for t in turns],
                            na_reason=criterion.rationale if criterion.value is None else '').model_dump()
            definition = next(c for c in checklist['criteria'] if c['id'] == answer['criterion_id'])
            validate_answer(definition, answer, set(positions.values()))
            answers.append(answer)
        expected = calculate(checklist, answers)
        if row.score.final_score != expected['total'] or row.score.critical_flag != bool(expected['critical_errors']):
            raise WorkflowError('Итоговый балл или critical_flag не соответствует ответам')
    return document


def build_submission(workflows, group, split, team, checklist_id='cqa-default', version=1,
                     errors=None, attempted_model=None):
    errors = errors or {}
    checklist = workflows.checklist(checklist_id, version)
    require_official_rules(checklist)
    source = dataset_dialogs(workflows.dialogs, group, split)
    results, models = [], set()
    for dialog in source:
        cid = dialog['conversation_id']
        history = workflows.evaluations(group, cid)
        chosen = next((item for item in history if item['checklist_id'] == checklist_id
                       and item['checklist_version'] == version), None)
        failure = workflows.last_failure(group, cid, checklist_id, version)
        if failure and (chosen is None or failure['created_at'] > chosen['created_at']):
            results.append({'conversation_id': cid, 'status': 'error', 'criteria': [], 'score': None,
                            'error': failure['error']})
            models.add((failure['model']['mode'], failure['model']['name']))
            continue
        if cid in errors or chosen is None:
            results.append({'conversation_id': cid, 'status': 'error', 'criteria': [], 'score': None,
                            'error': errors.get(cid, 'Оценка отсутствует: обработка диалога не завершена.')})
            if cid in errors and attempted_model:
                models.add((attempted_model['mode'], attempted_model['name']))
            continue
        evaluation = workflows.evaluation(chosen['id'])
        if not evaluation['model']:
            raise WorkflowError('У оценки отсутствует информация о фактически использованной модели')
        models.add((evaluation['model']['mode'], evaluation['model']['name']))
        positions = {message['message_id']: message['position'] for message in dialog['transcript']}
        final = evaluation['final']
        rows = []
        for answer in final['results']:
            rationale = answer['explanation']
            if answer['na_reason'] and answer['na_reason'] not in rationale:
                rationale += ' N/A: ' + answer['na_reason']
            rows.append({'criterion_id': CRITERION_CODES[answer['criterion_id']], 'value': answer['value'],
                         'rationale': rationale,
                         'evidence_turns': sorted({positions[mid] for mid in answer['evidence_message_ids']})})
        results.append({'conversation_id': cid, 'status': 'ok', 'criteria': rows,
                        'score': {'final_score': final['total'], 'critical_flag': bool(final['critical_errors'])}})
    if not models:
        raise WorkflowError('Нет результатов или попыток запуска модели. Пустая имитация сдачи не создаётся.')
    modes = {mode for mode, name in models}
    payload = {'schema_version': '1.0', 'team_name': team,
               'model': {'mode': next(iter(modes)) if len(modes) == 1 else 'hybrid',
                         'name': ', '.join(sorted({name for mode, name in models}))}, 'results': results}
    validate_submission(payload, source, split)
    return payload


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)
