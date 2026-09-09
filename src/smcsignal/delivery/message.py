"""Immutable signal-message presentation model and deterministic renderer.

A ``SignalMessage`` is a *presentation projection* of an already-published
upstream ``SignalSnapshot``: it carries only the flat facts the renderer needs,
always preserving the immutable upstream ``signal_id``. It is not a second
competing signal model and it contains no invented trading fields (no entry,
stop-loss, take-profit, target, or probability). The WHY text is reused from the
existing ``render_signal_explanation`` engine, never recomputed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.halal_filter.classification import AssetClassification
from smcsignal.analysis.setup_attribution.models import SetupAttribution
from smcsignal.analysis.signal_eligibility.models import EligibilityStatus, MarketBias
from smcsignal.analysis.signal_engine.models import (
    SignalDirection,
    SignalReason,
    SignalSnapshot,
    SignalStatus,
    current_observation,
)
from smcsignal.analysis.visualization import render_signal_explanation

from . import formatting
from .config import DeliveryConfig
from .identity import message_identity
from .models import ChartAttachment, RenderedSignalMessage

# Deterministic human labels for the fixed set of signal reasons we may see.
# Unknown reasons are rendered verbatim (never guessed).
_REASON_LABELS: dict[str, str] = {
    "halal_asset": "HALAL classification",
    "sqs_threshold_passed": "setup quality score passed",
    "long_bias": "long market bias",
    "mtf_alignment": "multi-timeframe alignment",
    "bullish_mss": "bullish market structure shift",
    "bullish_displacement": "bullish displacement",
    "discount_context": "premium/discount discount context",
    "ote_context": "OTE context",
    "bearish_spot_avoid": "bearish spot-avoid context",
}


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


def _block(value: object, name: str) -> str:
    """Validate a multi-line block (newlines and trailing space are allowed)."""
    if not isinstance(value, str) or not value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty string")
    return value


def _reason_human(reason: SignalReason) -> str:
    if not isinstance(reason, SignalReason):
        raise AnalysisInputError("reasons must contain SignalReason values")
    return _REASON_LABELS.get(reason.value, reason.value)


class _HeaderToken(StrEnum):
    BUY = "BUY"
    AVOID = "AVOID"
    NONE = "NO SIGNAL"


def _header_token(status: SignalStatus) -> _HeaderToken:
    if status is SignalStatus.BUY_SIGNAL:
        return _HeaderToken.BUY
    if status is SignalStatus.BEARISH_AVOID:
        return _HeaderToken.AVOID
    return _HeaderToken.NONE


@dataclass(frozen=True, slots=True)
class SignalMessage:
    """Flat, immutable presentation facts of one published signal frame."""

    signal_id: str
    symbol: str
    timeframe: str
    status: SignalStatus
    direction: SignalDirection
    classification: AssetClassification
    eligibility_status: EligibilityStatus
    bias: MarketBias
    score_total: int
    publish_threshold: int
    setup_identity: str
    reasons: tuple[SignalReason, ...]
    closed_at: datetime
    published_at: datetime
    evidence_available_at: datetime
    reference_price: Decimal | None
    explanation: str
    provenance_ids: tuple[str, ...]
    attribution_labels: tuple[str, ...] = ()
    attribution_combination: str | None = None

    def __post_init__(self) -> None:
        _text(self.signal_id, "signal_id")
        _text(self.symbol, "symbol")
        _text(self.timeframe, "timeframe")
        _text(self.setup_identity, "setup_identity")
        for enum_type, value, name in (
            (SignalStatus, self.status, "status"),
            (SignalDirection, self.direction, "direction"),
            (AssetClassification, self.classification, "classification"),
            (EligibilityStatus, self.eligibility_status, "eligibility_status"),
            (MarketBias, self.bias, "bias"),
        ):
            if not isinstance(value, enum_type):
                raise AnalysisInputError(f"{name} must be a {enum_type.__name__}")
        for label, raw in (
            ("score_total", self.score_total),
            ("publish_threshold", self.publish_threshold),
        ):
            if type(raw) is not int or raw < 0 or raw > 100:
                raise AnalysisInputError(f"{label} must be an integer from 0 to 100")
        if not self.reasons or not all(isinstance(r, SignalReason) for r in self.reasons):
            raise AnalysisInputError("reasons must be a nonempty tuple of SignalReason")
        for label, stamp in (
            ("closed_at", self.closed_at),
            ("published_at", self.published_at),
            ("evidence_available_at", self.evidence_available_at),
        ):
            if not isinstance(stamp, datetime):
                raise AnalysisInputError(f"{label} must be a datetime")
        if self.reference_price is not None and (
            not isinstance(self.reference_price, Decimal) or not self.reference_price.is_finite()
        ):
            raise AnalysisInputError("reference_price must be a finite Decimal or None")
        _block(self.explanation, "explanation")
        if not self.provenance_ids or not all(
            isinstance(p, str) and p.strip() for p in self.provenance_ids
        ):
            raise AnalysisInputError("provenance_ids must be a nonempty tuple of strings")
        if not all(isinstance(label, str) and label.strip() for label in self.attribution_labels):
            raise AnalysisInputError("attribution_labels must be trimmed strings")
        if self.attribution_combination is not None:
            _text(self.attribution_combination, "attribution_combination")

    @classmethod
    def from_signal(
        cls,
        frame: SignalSnapshot,
        attribution: SetupAttribution | None = None,
    ) -> SignalMessage:
        """Project one published signal snapshot into presentation facts.

        Reads only already-published fields on the immutable snapshot and its
        candidate/signal records. It never re-runs a detector and never invents
        a value. The WHY explanation is delegated to the existing
        ``render_signal_explanation`` engine.
        """
        if not isinstance(frame, SignalSnapshot):
            raise AnalysisInputError("from_signal requires a SignalSnapshot")
        if attribution is not None and not isinstance(attribution, SetupAttribution):
            raise AnalysisInputError("attribution must be a SetupAttribution or None")
        if attribution is not None and attribution.signal_id != frame.signal_id:
            raise AnalysisInputError("the attribution must explain this exact signal")
        signal = frame.candidate.signal
        observation = current_observation(frame.upstream)
        explanation = (
            render_signal_explanation(frame, attribution)
            if attribution is not None
            else render_signal_explanation(frame)
        )
        return cls(
            signal_id=frame.signal_id,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            status=frame.status,
            direction=frame.direction,
            classification=signal.classification,
            eligibility_status=signal.eligibility_status,
            bias=signal.bias,
            score_total=signal.score_total,
            publish_threshold=signal.publish_threshold,
            setup_identity=frame.setup_identity,
            reasons=tuple(frame.candidate.reasons),
            closed_at=signal.candle_closed_at,
            published_at=signal.published_at,
            evidence_available_at=signal.evidence_available_at,
            reference_price=observation.candle.close,
            explanation=explanation,
            provenance_ids=tuple(item.evidence_id for item in frame.candidate.evidence),
            attribution_labels=(
                tuple(label.value for label in attribution.labels)
                if attribution is not None
                else ()
            ),
            attribution_combination=(
                attribution.combination_key if attribution is not None else None
            ),
        )


def _esc(value: object) -> str:
    return formatting.escape_html(value)


def render_message(
    message: SignalMessage,
    config: DeliveryConfig,
    *,
    chart_present: bool = False,
) -> str:
    """Deterministically render a signal message as HTML-escaped caption text.

    Rendering is a pure function of ``message`` plus the frozen config. It never
    depends on the wall clock, randomness, locale, or environment, and it never
    changes the underlying signal. Chart presence only toggles an explicit
    availability marker; it never alters the signal facts.
    """
    if not isinstance(message, SignalMessage):
        raise AnalysisInputError("render_message requires a SignalMessage")
    if not isinstance(config, DeliveryConfig):
        raise AnalysisInputError("config must be a DeliveryConfig")
    if type(chart_present) is not bool:
        raise AnalysisInputError("chart_present must be a boolean")

    lines: list[str] = []
    token = _header_token(message.status)
    lines.append(f"{_esc(message.symbol)} {_esc(message.timeframe)} \u2014 {_esc(token.value)}")

    if config.show_signal_id:
        lines.append(f"signal id : {_esc(message.signal_id)}")

    if message.reference_price is not None:
        price = formatting.format_decimal(message.reference_price)
        lines.append(f"reference close : {_esc(price)}")

    lines.append(f"setup quality score : {message.score_total} / {message.publish_threshold}")

    if config.show_setup_context:
        context = _esc(message.setup_identity)
        if message.attribution_labels:
            context += " (" + _esc("+".join(message.attribution_labels)) + ")"
        lines.append(f"setup : {context}")

    reasons = "; ".join(_esc(_reason_human(reason)) for reason in message.reasons)
    lines.append(f"reasons : {reasons}")

    if config.show_halal_status:
        lines.append(f"classification : {_esc(message.classification.value)}")
        lines.append(
            f"eligibility : {_esc(message.eligibility_status.value)}; "
            f"bias {_esc(message.bias.value)}"
        )

    if config.show_timestamps:
        lines.append(
            "candle closed : "
            + _esc(formatting.format_timestamp(message.closed_at))
            + " ; published : "
            + _esc(formatting.format_timestamp(message.published_at))
        )

    if message.explanation:
        lines.append("why")
        for raw_line in message.explanation.rstrip("\n").split("\n"):
            lines.append("  " + _esc(raw_line))

    if message.provenance_ids:
        lines.append("provenance : " + "; ".join(_esc(pid) for pid in message.provenance_ids))

    if not chart_present and config.show_chart_unavailable_marker:
        lines.append("chart : not available")

    lines.append("signal facts presented; not trading advice")
    return "\n".join(lines)


def require_buy_signal(frame: SignalSnapshot) -> SignalSnapshot:
    """Return a published signal only when it is an immutable BUY signal.

    Any non-BUY record is rejected rather than silently converted into a BUY
    message. This keeps the broadcast boundary honest: only already-published,
    upstream-accepted BUY signals are eligible for presentation here.
    """
    if not isinstance(frame, SignalSnapshot):
        raise AnalysisInputError("presentation requires a SignalSnapshot")
    if frame.status is not SignalStatus.BUY_SIGNAL:
        raise AnalysisInputError(
            f"only BUY_SIGNAL publications may be broadcast; received {frame.status.value}"
        )
    return frame


def assemble_signal_message(
    message: SignalMessage,
    *,
    chart: ChartAttachment | None = None,
    config: DeliveryConfig | None = None,
) -> RenderedSignalMessage:
    """Build the rendered message from an already-projected ``SignalMessage``.

    The optional ``chart`` must already reference this signal (it is validated
    here); chart failure is handled by the caller and never mutates the message.
    """
    if not isinstance(message, SignalMessage):
        raise AnalysisInputError("assemble requires a SignalMessage")
    if chart is not None and chart.signal_id != message.signal_id:
        raise AnalysisInputError("chart must reference this exact signal")
    settings = config if config is not None else DeliveryConfig()
    caption = render_message(message, settings, chart_present=chart is not None)
    parts = (
        tuple(formatting.split_caption(caption, settings.max_caption_length))
        if settings.split_long_messages
        else (caption,)
    )
    return RenderedSignalMessage(
        signal_id=message.signal_id,
        message_id=message_identity(message.signal_id),
        caption=caption,
        parts=parts,
        chart=chart,
    )


def prepare_signal_message(
    frame: SignalSnapshot,
    *,
    attribution: SetupAttribution | None = None,
    chart: ChartAttachment | None = None,
    config: DeliveryConfig | None = None,
) -> RenderedSignalMessage:
    """Build a fully rendered, deterministic signal message.

    The optional ``chart`` must already reference this signal; if it is supplied
    and valid it is bound to the rendered message. A rendering failure here can
    never mutate the input frame.
    """
    message = SignalMessage.from_signal(frame, attribution)
    return assemble_signal_message(message, chart=chart, config=config)
