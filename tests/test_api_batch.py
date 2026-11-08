"""No network or real scores: verify scheduling and stopping on provider failures."""

from unittest.mock import Mock

import pytest

from participant_3 import api_batch


def setup(monkeypatch, fatal=False):
    monkeypatch.setattr('sys.argv', ['api_batch', '--group', 'FBroker', '--workers', '1'])
    def api(path, payload=None):
        if path == '/capabilities':
            return {'evaluation_available': True}
        if path.startswith('/dialogs?'):
            return {'total': 10, 'items': [{'conversation_id': f'technical-{i}'} for i in range(10)]}
        if path.startswith('/submissions?'):
            return {'results': [{'status': 'error'} for _ in range(10)]}
        raise AssertionError(path)
    monkeypatch.setattr(api_batch, 'api', api)
    assess = Mock(side_effect=lambda group, cid: (cid, 'error', 'Технический тест', fatal))
    monkeypatch.setattr(api_batch, 'assess', assess)
    write = Mock()
    monkeypatch.setattr(api_batch, 'write_json', write)
    return assess, write


def test_provider_limit_stops_queue_without_fake_export(monkeypatch):
    assess, write = setup(monkeypatch, fatal=True)
    with pytest.raises(SystemExit) as exc:
        api_batch.main()
    assert exc.value.code == 1
    assert assess.call_count == 1
    write.assert_not_called()


def test_per_dialog_review_errors_do_not_drop_ids(monkeypatch):
    assess, write = setup(monkeypatch)
    api_batch.main()
    assert assess.call_count == 10
    assert write.call_args.args[0] == api_batch.ROOT / 'results/prod/results.json'
    assert len(write.call_args.args[1]['results']) == 10
