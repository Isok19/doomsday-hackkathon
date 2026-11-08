"""Visible terminal launch with a hidden API key prompt; key stays in process memory."""

import getpass
import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=['openai', 'gemini'], default='openai')
    args = parser.parse_args()
    os.environ['CQA_AI_PROVIDER'] = args.provider
    key_name = 'GEMINI_API_KEY' if args.provider == 'gemini' else 'OPENAI_API_KEY'
    if not os.getenv(key_name, '').strip():
        os.environ[key_name] = getpass.getpass(f'{key_name} (скрытый ввод; Enter для продолжения): ').strip()
    if not os.environ[key_name]:
        raise SystemExit('Ключ не введён. Запуск ИИ отменён.')
    os.environ['CQA_ENABLE_AI'] = '1'
    print('Ключ принят в память процесса. Оценка запускается только по запросу пользователя.', flush=True)
    uvicorn.run('app.main:app', host='127.0.0.1', port=8000, reload=True)


if __name__ == '__main__':
    main()
