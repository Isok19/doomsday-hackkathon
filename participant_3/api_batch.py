"""Batch via the running local server: API key remains in its memory, never copied."""

import argparse
import json
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from participant_3.batch import ROOT
from participant_3.submission import write_json

BASE = 'http://127.0.0.1:8000/api'


def api(path, payload=None):
    request = Request(BASE + path, data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=110) as response:
        return json.load(response)


def assess(group, cid):
    try:
        result = api('/evaluations/run', {'dataset_group_id': group, 'conversation_id': cid,
                     'checklist_id': 'cqa-default', 'checklist_version': 1})
        return cid, 'ok', str(result['final']['total']), False
    except HTTPError as exc:
        try:
            detail = json.loads(exc.read(4096)).get('detail', 'Ошибка обработки')
        except (ValueError, UnicodeError):
            detail = 'Ошибка обработки'
        detail = detail if isinstance(detail, str) else 'Ошибка параметров обработки'
        fatal = exc.code in (429, 503) or any(term in detail.lower() for term in (
            'баланс', 'лимит', 'ключ openai', 'нет доступа', 'модель недоступна', 'подключение'))
        return cid, 'error', detail, fatal
    except (URLError, TimeoutError, OSError, ValueError):
        return cid, 'error', 'Нет надёжного ответа локального сервера; проверьте его состояние.', True


def main():
    parser = argparse.ArgumentParser(description='Пакетная оценка через работающий localhost-сервер')
    parser.add_argument('--group', required=True, choices=['DEV', 'FBroker'])
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=3)
    parser.add_argument('--skip-existing', action='store_true', help='Использовать уже сохранённые успешные оценки v1')
    args = parser.parse_args()
    split = 'dev' if args.group == 'DEV' else 'prod'
    try:
        capabilities = api('/capabilities')
        if not capabilities['evaluation_available']:
            parser.exit(1, 'На сервере не включён оценщик. Настройте ключ и запустите participant_3.serve.\n')
        page = api('/dialogs?' + urlencode({'dataset_group_id': args.group, 'limit': 100}))
        expected = 65 if split == 'dev' else 10
        if page['total'] != expected or len(page['items']) != expected:
            parser.exit(1, 'Набор не соответствует ожидаемому числу диалогов.\n')
        ids = []
        for item in page['items']:
            cid = item['conversation_id']
            history = api('/evaluations?' + urlencode({'dataset_group_id': args.group, 'conversation_id': cid})) if args.skip_existing else []
            if any(row['checklist_id'] == 'cqa-default' and row['checklist_version'] == 1 for row in history):
                print(f'{cid}: используется ранее сохранённая оценка v1', flush=True)
            else:
                ids.append(cid)
        stopped, complete = False, 0
        pending_ids = iter(ids)
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = set()
            for _ in range(min(args.workers, len(ids))):
                futures.add(pool.submit(assess, args.group, next(pending_ids)))
            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for task in done:
                    cid, status, detail, fatal = task.result()
                    complete += 1
                    print(f'{complete}/{len(ids)} {cid}: {status} — {detail}', flush=True)
                    stopped = stopped or fatal
                if not stopped:
                    for _ in range(len(done)):
                        cid = next(pending_ids, None)
                        if cid is not None:
                            futures.add(pool.submit(assess, args.group, cid))
        if stopped:
            parser.exit(1, 'Пакет остановлен из-за подключения/лимитов. Выполненные попытки сохранены; итоговый файл не перезаписан.\n')
        payload = api('/submissions?' + urlencode({'dataset_group_id': args.group, 'split': split}))
        destination = ROOT / 'results' / split / 'results.json'
        write_json(destination, payload)
        failures = sum(row['status'] == 'error' for row in payload['results'])
        print(f'Сохранено {destination}: {len(payload["results"])} записей, ошибок {failures}. Push не выполнялся.')
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        parser.exit(1, 'Не удалось завершить пакет. Проверьте сервер и сохранённые результаты; файл не подменяется заглушкой.\n')


if __name__ == '__main__':
    main()
