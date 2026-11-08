"""Visible terminal launch with a hidden API key prompt; key stays in process memory."""

import getpass
import os

import uvicorn


def main():
    if not os.getenv('OPENAI_API_KEY', '').strip():
        os.environ['OPENAI_API_KEY'] = getpass.getpass('OpenAI API key (скрытый ввод; Enter для продолжения): ').strip()
    if not os.environ['OPENAI_API_KEY']:
        raise SystemExit('Ключ не введён. Запуск ИИ отменён.')
    os.environ['CQA_ENABLE_AI'] = '1'
    print('Ключ принят в память процесса. Оценка запускается только по запросу пользователя.', flush=True)
    uvicorn.run('app.main:app', host='127.0.0.1', port=8000)


if __name__ == '__main__':
    main()
