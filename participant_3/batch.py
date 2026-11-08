"""Evaluate a complete dataset and export Appendix Results JSON; never pushes Git."""

import argparse
import json
import os
from pathlib import Path

from app.storage import DialogStore, read_dataset
from app.workflow_models import EvaluationInput
from app.workflows import WorkflowError, WorkflowStore
from participant_3.evaluator import configured_evaluator
from participant_3.submission import build_submission, dataset_dialogs, require_official_rules, validate_submission, write_json

ROOT = Path(__file__).resolve().parent.parent


def run_batch(workflows, evaluator, group, split, team, checklist_id, version, resume, progress):
    checklist = workflows.checklist(checklist_id, version)
    require_official_rules(checklist)
    source = dataset_dialogs(workflows.dialogs, group, split)
    errors = {}
    for index, dialog in enumerate(source, 1):
        cid = dialog['conversation_id']
        existing = workflows.evaluations(group, cid)
        matching = next((item for item in existing if item['checklist_id'] == checklist_id
                         and item['checklist_version'] == version), None)
        failure = workflows.last_failure(group, cid, checklist_id, version)
        if (resume and matching and workflows.evaluation(matching['id'])['model'] == evaluator.metadata
                and (not failure or failure['created_at'] < matching['created_at'])):
            progress(index, len(source), cid, 'saved')
            continue
        try:
            results = evaluator(dialog, checklist)
            workflows.create_evaluation(EvaluationInput(dataset_group_id=group, conversation_id=cid,
                checklist_id=checklist_id, checklist_version=version, results=results, model=evaluator.metadata))
        except WorkflowError as exc:
            errors[cid] = str(exc)
        except Exception:
            errors[cid] = 'Обработка диалога завершилась технической ошибкой; оценка не получена.'
        if cid in errors:
            workflows.record_failure(group, cid, checklist_id, version, evaluator.metadata, errors[cid])
        progress(index, len(source), cid, 'error' if cid in errors else 'ok')
    return build_submission(workflows, group, split, team, checklist_id, version, errors, evaluator.metadata)


def main():
    parser = argparse.ArgumentParser(description='Оценка и выгрузка CQA Results 1.0')
    parser.add_argument('action', choices=['run', 'export', 'validate'])
    parser.add_argument('--group', required=True, help='Точный dataset_group_id, полученный от организаторов')
    parser.add_argument('--split', required=True, choices=['dev', 'prod'])
    parser.add_argument('--team', default=os.getenv('CQA_TEAM_NAME', 'zenoxxk'))
    parser.add_argument('--checklist', default='cqa-default')
    parser.add_argument('--version', type=int, default=1)
    parser.add_argument('--resume', action='store_true', help='Не повторять успешные оценки той же моделью/версией/базой знаний')
    args = parser.parse_args()
    output = ROOT / 'results' / args.split / 'results.json'
    store = DialogStore(Path(os.getenv('CQA_DATABASE_PATH') or ROOT / 'data/cqa.sqlite3'))
    store.initialize()
    source_path = Path(os.getenv('CQA_DATASET_PATH') or ROOT / 'dialogs.json')
    if source_path.exists():
        store.import_dataset(read_dataset(source_path))
    workflows = WorkflowStore(store)
    workflows.initialize()
    try:
        if args.action == 'validate':
            validate_submission(json.loads(output.read_text(encoding='utf-8')),
                                dataset_dialogs(store, args.group, args.split), args.split)
            print('JSON соответствует формату 1.0; это не проверка смысловой точности ИИ.')
            return
        if args.action == 'run':
            evaluator = configured_evaluator()
            if evaluator is None:
                raise WorkflowError('Настройте OPENAI_API_KEY и CQA_ENABLE_AI=1. Вызовов модели не было.', 503)
            def progress(index, total, cid, status):
                print(f'{index}/{total} {cid}: {status}', flush=True)
            payload = run_batch(workflows, evaluator, args.group, args.split, args.team,
                                args.checklist, args.version, args.resume, progress)
        else:
            payload = build_submission(workflows, args.group, args.split, args.team, args.checklist, args.version)
        write_json(output, payload)
        errors = sum(row['status'] == 'error' for row in payload['results'])
        print(f'Сохранено: {output}; записей: {len(payload["results"])}; ошибок: {errors}. Git push не выполнялся.')
    except (ValueError, OSError) as exc:
        parser.exit(1, f'Ошибка: {exc}\n')


if __name__ == '__main__':
    main()
