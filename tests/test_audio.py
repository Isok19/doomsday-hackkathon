"""Player integration without downloading recordings or publishing signed links."""

from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import URLError
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import audio
from app.main import create_app
from app.workflows import WorkflowError

SOURCE = Path(__file__).resolve().parent.parent / "dialogs.json"
PLAYER = f"https://{audio.PLAYER_HOST}/player"
RECORDING = f"https://{audio.AUDIO_HOST}/api/filedownload/test.mp3?token=test&part=1"


def player(monkeypatch, body, content_type="text/html"):
    response = BytesIO(body.encode())
    response.headers = Message()
    response.headers['Content-Type'] = content_type
    response.geturl = lambda: PLAYER
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(audio, 'build_opener', lambda *args: opener)
    return opener


def test_resolve_html_and_decode_signed_query(monkeypatch):
    opener = player(monkeypatch, f'<audio><source src="{RECORDING.replace("&", "&amp;")}" type="audio/mpeg"></audio>')
    assert audio.resolve_audio(PLAYER) == RECORDING
    opener.open.assert_called_once_with(PLAYER, timeout=15)


@pytest.mark.parametrize('url', ['http://127.0.0.1/player', 'https://example.org/player',
                               f'https://{audio.PLAYER_HOST}:8443/player',
                               f'https://user:password@{audio.PLAYER_HOST}/player'])
def test_untrusted_player_not_requested(monkeypatch, url):
    opener = player(monkeypatch, '')
    with pytest.raises(WorkflowError) as error:
        audio.resolve_audio(url)
    assert error.value.status == 422
    opener.open.assert_not_called()


@pytest.mark.parametrize('body', ['<audio src="http://127.0.0.1/private"></audio>',
                                '<html>Expired link</html>', 'x' * (audio.MAX_PLAYER_BYTES + 1)])
def test_missing_untrusted_or_oversized_source(monkeypatch, body):
    player(monkeypatch, body)
    with pytest.raises(WorkflowError) as error:
        audio.resolve_audio(PLAYER)
    assert error.value.status == 502


def test_external_redirect_and_network_errors_hide_url(monkeypatch):
    with pytest.raises(WorkflowError):
        audio.PlayerRedirects().redirect_request(None, None, 302, '', {}, 'https://example.org/private')
    opener = player(monkeypatch, '')
    opener.open.side_effect = URLError('private-signed-url')
    with pytest.raises(WorkflowError) as error:
        audio.resolve_audio(PLAYER)
    assert error.value.status == 502
    assert 'private-signed-url' not in str(error.value)


def test_audio_endpoint_and_absent_recording(monkeypatch, tmp_path):
    from fastapi.responses import Response
    import app.main
    def stream(url, byte_range):
        assert byte_range == 'bytes=0-2'
        if not url:
            raise WorkflowError('Нет аудио', 404)
        return Response(b'ID3', status_code=206, media_type='audio/mpeg', headers={'Cache-Control': 'no-store'})
    monkeypatch.setattr(app.main, 'stream_audio', stream)
    with TestClient(create_app(SOURCE, tmp_path / 'audio.sqlite3')) as client:
        client.headers['Range'] = 'bytes=0-2'
        response = client.get('/api/dialogs/p4-ru-001/audio?dataset_group_id=DEV')
        assert response.status_code == 206
        assert response.content == b'ID3'
        assert response.headers['cache-control'] == 'no-store'
        assert client.get('/api/dialogs/p1-ru-001/audio').status_code == 404
        assert client.get('/api/dialogs/missing/audio').status_code == 404
        assert client.get('/api/dialogs/p4-ru-001/audio?dataset_group_id=missing').status_code == 404


def test_range_streaming_and_close(monkeypatch, tmp_path):
    monkeypatch.setattr(audio, 'resolve_audio', lambda url: RECORDING)
    response = BytesIO(b'ID3')
    response.status = 206
    response.headers = Message()
    response.headers['Content-Type'] = 'audio/mpeg'
    response.headers['Content-Range'] = 'bytes 0-2/1000'
    response.headers['Content-Length'] = '3'
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(audio, 'build_opener', lambda *args: opener)
    with TestClient(create_app(SOURCE, tmp_path / 'stream.sqlite3')) as client:
        result = client.get('/api/dialogs/p4-ru-001/audio', headers={'Range': 'bytes=0-2'})
        assert result.status_code == 206
        assert result.content == b'ID3'
        assert result.headers['content-range'] == 'bytes 0-2/1000'
        assert response.closed
        assert opener.open.call_args.args[0].get_header('Range') == 'bytes=0-2'
        assert client.get('/api/dialogs/p4-ru-001/audio', headers={'Range': 'bytes=0-2,8-10'}).status_code == 416
