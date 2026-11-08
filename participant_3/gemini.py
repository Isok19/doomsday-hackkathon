"""Gemini transport; uses the same checklist, projection and validation as OpenAI."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from participant_3.evaluator import OpenAIEvaluator, EvaluationError, NoRedirects, MAX_RESPONSE_BYTES

DEFAULT_GEMINI_MODEL = 'gemini-3.8-flash'


class GeminiEvaluator(OpenAIEvaluator):
    def __init__(self, api_key, model=DEFAULT_GEMINI_MODEL, knowledge='', transport=None):
        if not api_key.strip():
            raise EvaluationError('Не задан GEMINI_API_KEY', 503)
        super().__init__(api_key, model, knowledge, transport)

    def _request(self, payload):
        fmt = payload['text']['format']
        body = {'model': self.model_name,
                'messages': [{'role': 'system', 'content': payload['instructions']}, *payload['input']],
                'response_format': {'type': 'json_schema', 'json_schema': {
                    'name': fmt['name'], 'strict': True, 'schema': fmt['schema']}}}
        request = Request('https://generativelanguage.googleapis.com/v1beta/openai/chat/completions',
                          data=json.dumps(body, ensure_ascii=False).encode(),
                          headers={'Authorization': 'Bearer ' + self._api_key, 'Content-Type': 'application/json'})
        try:
            with build_opener(NoRedirects()).open(request, timeout=90) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            messages = {400: 'Gemini отклонил формат запроса или параметры модели.',
                        401: 'Ключ Gemini отклонён.', 403: 'Нет доступа к Gemini API: проверьте ключ, проект и регион.',
                        404: 'Модель Gemini недоступна для этого проекта.',
                        429: 'Исчерпана квота Gemini API или превышена частота запросов. Проверьте Usage/Billing в AI Studio.'}
            raise EvaluationError(messages.get(exc.code, 'Ошибка внешнего Gemini API.'),
                                  429 if exc.code == 429 else 502) from None
        except (URLError, TimeoutError, OSError):
            raise EvaluationError('Gemini не ответил вовремя или соединение недоступно.', 502) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise EvaluationError('Ответ Gemini слишком большой.', 502)
        try:
            result = json.loads(raw)
            choice = result['choices'][0]
            if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
                raise ValueError('incomplete')
            content = choice['message']['content']
            if not isinstance(content, str):
                raise ValueError('missing text')
        except (ValueError, KeyError, IndexError, TypeError):
            raise EvaluationError('Gemini не вернул завершённый текстовый ответ. Оценка не сохранена.', 502) from None
        return {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': content}]}]}
