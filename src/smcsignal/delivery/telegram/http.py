"""Thin, injectable Telegram Bot API HTTP client.

This module defines a minimal client for ``sendMessage`` (text) and
``sendDocument`` (optional chart). The actual network transport is injectable so
tests run against mocked HTTP responses with **no real Telegram call**. When no
transport is injected a stdlib ``urllib`` transport is used (the real HTTP path).

The bot token is stored on the client and used only to build the request URL; it
is never logged or serialized. ``redact_token`` never exposes any portion of it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from smcsignal.analysis.errors import AnalysisInputError

API_BASE_URL = "https://api.telegram.org"
_JSON_CONTENT_TYPE = "application/json"
_SVG_MIME = "image/svg+xml"
_BOUNDARY = "smcsignal-telegram-boundary"
_MULTIPART = f"multipart/form-data; boundary={_BOUNDARY}"


class TelegramTimeoutError(RuntimeError):
    """A Telegram request timed out (outcome is UNKNOWN, never FAILED)."""


class TelegramApiError(RuntimeError):
    """A Telegram API error with an HTTP status and response body."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"telegram api error status={status}")
        self.status = status
        self.body = body


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A transport HTTP response (status code + text body)."""

    status: int
    body: str


class HttpTransport(Protocol):
    """Performs one POST and returns a response; may raise timeout errors."""

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


class _StdlibTransport:
    """Real stdlib ``urllib`` transport used when none is injected."""

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = response.read().decode("utf-8")
                return HttpResponse(status=int(response.status), body=body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return HttpResponse(status=int(exc.code), body=body)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                raise TelegramTimeoutError(str(reason)) from exc
            raise TelegramApiError(0, str(reason)) from exc
        except TimeoutError as exc:
            raise TelegramTimeoutError(str(exc)) from exc


def _build_multipart(
    fields: list[tuple[str, str]],
    file_field: str,
    filename: str,
    content: bytes,
    mime_type: str,
) -> bytes:
    """Build a deterministic multipart/form-data body for sendDocument."""
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(f"--{_BOUNDARY}\r\n".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        parts.append(value.encode("utf-8"))
        parts.append(b"\r\n")
    parts.append(f"--{_BOUNDARY}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {mime_type}\r\n\r\n".encode())
    parts.append(content)
    parts.append(b"\r\n")
    parts.append(f"--{_BOUNDARY}--\r\n".encode())
    return b"".join(parts)


class TelegramHttpClient:
    """Minimal Telegram Bot API client for text and document sends.

    ``transport`` is injectable (default: stdlib ``urllib``). ``token`` is the
    injected bot token and is never exposed outside request URL construction.
    """

    def __init__(
        self,
        token: str,
        *,
        transport: HttpTransport | None = None,
        timeout: float = 10.0,
    ) -> None:
        if not isinstance(token, str) or not token.strip():
            raise AnalysisInputError("token must be a nonempty string")
        if type(timeout) not in (int, float) or timeout <= 0:
            raise AnalysisInputError("timeout must be positive")
        self._token = token
        self._transport: HttpTransport = transport if transport is not None else _StdlibTransport()
        self._timeout = timeout

    def _url(self, method: str) -> str:
        return f"{API_BASE_URL}/bot{self._token}/{method}"

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        """POST sendMessage with an HTML/parse-mode text caption."""
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise AnalysisInputError("chat_id must be a nonempty string")
        if not isinstance(text, str) or not text.strip():
            raise AnalysisInputError("text must be a nonempty string")
        payload: dict[str, object] = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        body = json.dumps(payload).encode("utf-8")
        return self._transport.post(
            self._url("sendMessage"),
            headers={"Content-Type": _JSON_CONTENT_TYPE},
            data=body,
            timeout=self._timeout if timeout is None else timeout,
        )

    def send_document(
        self,
        chat_id: str,
        filename: str,
        content: bytes,
        *,
        caption: str | None = None,
        mime_type: str = _SVG_MIME,
        timeout: float | None = None,
    ) -> HttpResponse:
        """POST sendDocument with the chart file content (SVG by default)."""
        if not isinstance(chat_id, str) or not chat_id.strip():
            raise AnalysisInputError("chat_id must be a nonempty string")
        if not isinstance(filename, str) or not filename.strip():
            raise AnalysisInputError("filename must be a nonempty string")
        if not isinstance(content, bytes) or not content:
            raise AnalysisInputError("content must be nonempty bytes")
        fields: list[tuple[str, str]] = [("chat_id", chat_id)]
        if caption:
            fields.append(("caption", caption))
        body = _build_multipart(fields, "document", filename, content, mime_type)
        return self._transport.post(
            self._url("sendDocument"),
            headers={"Content-Type": _MULTIPART},
            data=body,
            timeout=self._timeout if timeout is None else timeout,
        )
