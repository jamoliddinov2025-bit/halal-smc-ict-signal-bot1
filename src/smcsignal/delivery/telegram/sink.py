"""The real Telegram transport sink (downstream, offline-testable).

``TelegramSink`` implements the Phase 24D-B ``PayloadSink`` capability: given an
immutable ``TransportPayload`` it sends the already-rendered text caption (and,
when enabled and present, the optional chart document) to a resolved Telegram
chat, and returns a ``DeliveryReceipt`` in the frozen ``DeliveryState`` /
``FailureCategory`` vocabulary.

It is strictly downstream and output-only. It never influences signal
generation, halal, eligibility, governance, or strategy, and it contains no
trading/execution logic. The bot token is injected (never hardcoded); a missing
token disables the transport (``NOT_ATTEMPTED``). All tests run against mocked
HTTP responses through an injectable transport — no real Telegram call occurs.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping

from smcsignal.analysis.errors import AnalysisInputError

from ..models import DeliveryReceipt, DeliveryState, FailureCategory
from ..transport import TransportPayload
from .audit import TelegramAuditRecord, telegram_audit_record
from .config import TelegramConfig
from .destination import resolve_chat_id
from .http import (
    HttpResponse,
    HttpTransport,
    TelegramApiError,
    TelegramHttpClient,
    TelegramTimeoutError,
)
from .rate_limit import TelegramRateLimiter

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _receipt(
    payload: TransportPayload, state: DeliveryState, category: FailureCategory
) -> DeliveryReceipt:
    return DeliveryReceipt(
        delivery_id=payload.delivery_id,
        message_id=payload.message_id,
        signal_id=payload.signal_id,
        destination_id=payload.destination_id,
        attempt_number=payload.attempt_number,
        state=state,
        failure_category=category,
    )


def _ok(response: HttpResponse) -> bool:
    """True when Telegram returned HTTP 200 with ``ok`` true."""
    if response.status != 200:
        return False
    try:
        import json

        body = json.loads(response.body)
        return bool(body.get("ok"))
    except Exception:
        return False


class TelegramSink:
    """Deliver a ``TransportPayload`` to Telegram and report a frozen receipt.

    ``token`` is injected from the approved secret path (never hardcoded). When
    ``token`` is None or ``config.enabled`` is False the transport is disabled and
    reports ``NOT_ATTEMPTED``. ``destinations`` maps the frozen logical
    ``destination_id`` to a real chat/channel id.
    """

    def __init__(
        self,
        token: str | None,
        *,
        config: TelegramConfig | None = None,
        destinations: Mapping[str, str] | None = None,
        http_client: TelegramHttpClient | None = None,
        http_transport: HttpTransport | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if token is not None and not isinstance(token, str):
            raise AnalysisInputError("token must be a string or None")
        self._token: str | None = token
        self._config = config if config is not None else TelegramConfig()
        self._destinations: dict[str, str] = dict(destinations) if destinations else {}
        self._client: TelegramHttpClient | None = None
        if token is not None:
            self._client = (
                http_client
                if http_client is not None
                else TelegramHttpClient(
                    token,
                    transport=http_transport,
                    timeout=self._config.network_timeout_seconds,
                )
            )
        self._rate = TelegramRateLimiter(
            self._config.requests_per_second, self._config.rate_limit_capacity, now=now_fn
        )
        self._sleep = sleep_fn
        self._last_chart_failure: str | None = None

    @property
    def enabled(self) -> bool:
        """The transport is active only when a token is injected and enabled."""
        return self._token is not None and self._config.enabled

    @property
    def last_chart_failure(self) -> str | None:
        """Last best-effort chart-send failure reason (or None)."""
        return self._last_chart_failure

    def deliver_payload(self, payload: TransportPayload) -> DeliveryReceipt:
        """Send one rendered payload and return a frozen delivery receipt.

        Pre-flight checks (disabled transport, unresolvable destination, caption
        over the configured budget) report ``NOT_ATTEMPTED``/``CONFIG`` and make
        no network call. The first two preserve the frozen contract; the caption
        budget is the Phase 24D-C1 hardening guard.
        """
        if not isinstance(payload, TransportPayload):
            raise AnalysisInputError("TelegramSink requires a TransportPayload")
        if not self.enabled or self._client is None:
            # Missing token or disabled: disabled transport, nothing sent.
            return _receipt(payload, DeliveryState.NOT_ATTEMPTED, FailureCategory.CONFIG)
        try:
            chat_id = resolve_chat_id(payload.destination_id, self._destinations)
        except AnalysisInputError:
            # Unknown/invalid destination: fail before any network call.
            return _receipt(payload, DeliveryState.NOT_ATTEMPTED, FailureCategory.CONFIG)

        # Caption budget guard: refuse to send a message Telegram would reject.
        # Nothing is truncated and nothing is re-rendered; the frozen payload is
        # read only (transports never rewrite content).
        if len(payload.caption) > self._config.max_message_chars:
            return _receipt(payload, DeliveryState.NOT_ATTEMPTED, FailureCategory.CONFIG)

        text_state, text_category = self._send_text(chat_id, payload)
        if text_state is not DeliveryState.DELIVERED:
            return _receipt(payload, text_state, text_category)

        # Text is delivered. The optional chart is independent: if it is present
        # and chart delivery is enabled, send best-effort. A chart failure never
        # invalidates the delivered text.
        if payload.chart_present and self._config.chart_enabled:
            self._send_chart_best_effort(chat_id, payload)
        return _receipt(payload, DeliveryState.DELIVERED, FailureCategory.NONE)

    def _send_text(
        self, chat_id: str, payload: TransportPayload
    ) -> tuple[DeliveryState, FailureCategory]:
        """Send the caption with bounded retry; never raises out to the caller.

        Retries are attempted only for transiently-retryable outcomes (HTTP
        429/5xx). A timeout yields ``UNKNOWN`` (no confirmation, never retried
        silently). After the attempt budget is exhausted a terminal ``FAILED`` is
        returned with the appropriate frozen ``FailureCategory``.
        """
        client = self._client
        if client is None:
            return DeliveryState.NOT_ATTEMPTED, FailureCategory.CONFIG
        parse_mode = "HTML" if self._config.html_parse_mode else None
        attempts = self._config.retry_max_attempts
        retryable_final: FailureCategory | None = None
        for attempt in range(attempts):
            if not self._rate.try_acquire():
                return DeliveryState.FAILED, FailureCategory.RATE_LIMIT
            if attempt > 0:
                backoff = min(
                    self._config.retry_backoff_seconds,
                    self._config.retry_backoff_max_seconds,
                )
                self._sleep(backoff)
            try:
                response = client.send_message(chat_id, payload.caption, parse_mode=parse_mode)
            except TelegramTimeoutError:
                # No confirmation either way -> UNKNOWN (not retried silently).
                return DeliveryState.UNKNOWN, FailureCategory.TIMEOUT
            except TelegramApiError as exc:
                if self._retryable(exc.status):
                    retryable_final = self._categorize(exc.status)
                    continue
                return DeliveryState.FAILED, self._categorize(exc.status)
            if _ok(response):
                return DeliveryState.DELIVERED, FailureCategory.NONE
            if response.status == 200:
                # Request accepted by the API but no ``ok`` confirmation: the
                # message was sent (SENT), delivery is not end-to-end confirmed.
                return DeliveryState.SENT, FailureCategory.NONE
            # A non-200 body: only transiently-retryable statuses are retried.
            if self._retryable_status(response.status):
                retryable_final = self._categorize(response.status)
                continue
            return DeliveryState.FAILED, self._categorize(response.status)
        return DeliveryState.FAILED, (
            retryable_final if retryable_final is not None else FailureCategory.TRANSPORT
        )

    def _send_chart_best_effort(self, chat_id: str, payload: TransportPayload) -> None:
        """Send the optional chart document; never invalidates the text."""
        client = self._client
        if client is None:
            self._last_chart_failure = "disabled"
            return
        if not self._rate.try_acquire():
            self._last_chart_failure = "rate-limited"
            return
        chart = payload.chart
        if chart is None:
            return
        try:
            response = client.send_document(
                chat_id,
                chart.filename,
                chart.content.encode("utf-8"),
            )
            if _ok(response):
                self._last_chart_failure = None
            else:
                self._last_chart_failure = f"http-{response.status}"
        except (TelegramTimeoutError, TelegramApiError) as exc:
            self._last_chart_failure = str(exc)

    @staticmethod
    def _retryable(status: int) -> bool:
        return status in _RETRYABLE_STATUS

    @staticmethod
    def _retryable_status(status: int) -> bool:
        return status in _RETRYABLE_STATUS

    @staticmethod
    def _categorize(status: int) -> FailureCategory:
        if status == 401:
            return FailureCategory.CONFIG  # invalid token
        if status == 429:
            return FailureCategory.RATE_LIMIT
        return FailureCategory.TRANSPORT

    def audit(
        self,
        payload: TransportPayload,
        receipt: DeliveryReceipt,
        *,
        chat_id: str | None = None,
    ) -> TelegramAuditRecord:
        """Produce a deterministic, redacted audit record for observability."""
        return telegram_audit_record(payload, receipt, chat_id=chat_id)
