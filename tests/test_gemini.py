import json
from io import BytesIO
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from participant_3.gemini import GeminiEvaluator
from participant_3.evaluator import EvaluationError, configured_evaluator


def payload():
    return {'instructions': 'rules', 'input': [{'role': 'user', 'content': 'transcript'}],
            'text': {'format': {'name': 'cqa', 'schema': {'type': 'object'}}}}


def test_gemini_wire_format_and_key_is_header_only(monkeypatch):
    import participant_3.gemini as module
    client = Mock()
    client.open.return_value = BytesIO(json.dumps({
        'choices': [{'finish_reason': 'stop', 'message': {'content': '{"status":"ok"}'}}]}).encode())
    monkeypatch.setattr(module, 'build_opener', lambda *args: client)
    result = GeminiEvaluator('private-test-key')._request(payload())
    request = client.open.call_args.args[0]
    assert request.full_url == 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions'
    body = json.loads(request.data)
    assert body['messages'][0]['role'] == 'system'
    assert body['response_format']['json_schema']['strict'] is True
    assert b'private-test-key' not in request.data
    assert 'private-test-key' not in request.full_url
    assert result['status'] == 'completed'


@pytest.mark.parametrize('finish', ['length', 'content_filter', None])
def test_gemini_never_accepts_incomplete_reply(monkeypatch, finish):
    import participant_3.gemini as module
    client = Mock()
    client.open.return_value = BytesIO(json.dumps({
        'choices': [{'finish_reason': finish, 'message': {'content': '{}'}}]}).encode())
    monkeypatch.setattr(module, 'build_opener', lambda *args: client)
    with pytest.raises(EvaluationError):
        GeminiEvaluator('test')._request(payload())


def test_gemini_quota_does_not_expose_provider_body(monkeypatch):
    import participant_3.gemini as module
    client = Mock()
    client.open.side_effect = HTTPError('url', 429, 'secret', {}, BytesIO(b'secret'))
    monkeypatch.setattr(module, 'build_opener', lambda *args: client)
    with pytest.raises(EvaluationError) as exc:
        GeminiEvaluator('test')._request(payload())
    assert exc.value.status == 429
    assert 'secret' not in str(exc.value)


def test_explicit_gemini_selection(monkeypatch):
    monkeypatch.setenv('CQA_ENABLE_AI', '1')
    monkeypatch.setenv('CQA_AI_PROVIDER', 'gemini')
    monkeypatch.setenv('GEMINI_API_KEY', 'test')
    monkeypatch.delenv('CQA_AI_MODEL', raising=False)
    monkeypatch.delenv('CQA_KNOWLEDGE_PATH', raising=False)
    evaluator = configured_evaluator()
    assert isinstance(evaluator, GeminiEvaluator)
    assert evaluator.metadata['name'] == 'gemini-3.8-flash'
