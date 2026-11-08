"""Resolve an organizer player page to a fresh audio URL; never store signed URLs."""

from html.parser import HTMLParser
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi.responses import Response, StreamingResponse

from app.workflows import WorkflowError

PLAYER_HOST = "ec88f21f1accecf38e20095a9fe175.a5.environment.api.powerplatform.com"
AUDIO_HOST = "freedomholdingcorporation.crm11.dynamics.com"
MAX_PLAYER_BYTES = 64 * 1024


def allowed_url(url, host):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == host
                and parts.port in (None, 443) and not parts.username and not parts.password)
    except ValueError:
        return False


class PlayerRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not allowed_url(newurl, PLAYER_HOST):
            raise WorkflowError("Сервис записи перенаправил запрос на неподдерживаемый адрес", 502)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class AudioSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("audio", "source") and attributes.get("src"):
            self.sources.append(attributes["src"])


def resolve_audio(player_url):
    if not player_url:
        raise WorkflowError("У диалога нет аудиозаписи", 404)
    if not allowed_url(player_url, PLAYER_HOST):
        raise WorkflowError("Для этого источника используйте ссылку «Открыть запись»", 422)
    try:
        with build_opener(PlayerRedirects()).open(player_url, timeout=15) as response:
            if response.headers.get_content_type() != "text/html":
                raise WorkflowError("Сервис записи вернул неожиданный формат", 502)
            content = response.read(MAX_PLAYER_BYTES + 1)
            if len(content) > MAX_PLAYER_BYTES:
                raise WorkflowError("Страница записи превышает допустимый размер", 502)
            parser = AudioSources()
            parser.feed(content.decode("utf-8", errors="replace"))
            for candidate in parser.sources:
                source = urljoin(response.geturl(), candidate)
                if allowed_url(source, AUDIO_HOST):
                    return source
    except (URLError, TimeoutError, OSError):
        raise WorkflowError("Не удалось получить аудиозапись. Повторите попытку или откройте внешний плеер.", 502) from None
    raise WorkflowError("В странице записи не найден поддерживаемый аудиоисточник", 502)


class AudioRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not allowed_url(newurl, AUDIO_HOST):
            raise WorkflowError("Неподдерживаемый адрес аудиофайла", 502)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def stream_audio(player_url, byte_range=None):
    """Same-origin playback including seeking; audio is streamed, never saved to disk."""
    if byte_range is not None and not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", byte_range):
        raise WorkflowError("Поддерживается только один диапазон байтов", 416)
    source = resolve_audio(player_url)
    headers = {"Accept-Encoding": "identity"}
    if byte_range:
        headers["Range"] = byte_range
    try:
        upstream = build_opener(AudioRedirects()).open(Request(source, headers=headers), timeout=20)
    except HTTPError as exc:
        if exc.code == 416:
            return Response(status_code=416, headers={"Cache-Control": "no-store"})
        raise WorkflowError("Аудиосервис временно недоступен. Повторите загрузку записи.", 502) from None
    except (URLError, TimeoutError, OSError):
        raise WorkflowError("Не удалось загрузить аудио. Откройте внешний плеер.", 502) from None
    if upstream.status not in (200, 206) or upstream.headers.get_content_type() not in ("audio/mpeg", "audio/mp3"):
        upstream.close()
        raise WorkflowError("Аудиосервис вернул неподдерживаемый формат", 502)
    response_headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
        if upstream.headers.get(name):
            response_headers[name] = upstream.headers[name]

    def chunks():
        try:
            while chunk := upstream.read(64 * 1024):
                yield chunk
        finally:
            upstream.close()

    return StreamingResponse(chunks(), status_code=upstream.status,
                             media_type="audio/mpeg", headers=response_headers)
