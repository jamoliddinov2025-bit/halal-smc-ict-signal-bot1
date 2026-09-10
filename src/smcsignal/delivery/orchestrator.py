"""Phase 24C delivery orchestration and offline integration.

``DeliveryCoordinator`` is the deterministic glue between the approved Phase 24B
presentation core and an offline ``MessageSink``. It proves the offline pipeline

    immutable SignalSnapshot -> 24B projection/render -> coordinator
      (in-memory dedup + retry) -> MessageSink -> DeliveryOutcome

It only *consumes* upstream published objects and the Phase 24B public API; it
never re-runs a detector, never creates a competing signal model, never mutates
a signal, and never reaches a network. Deduplication is explicit and lives in
an in-memory ``DeliveryRegistry``; there is no database and no durable
exactly-once guarantee.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, fields
from decimal import Decimal
from pathlib import Path

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.setup_attribution.models import SetupAttribution
from smcsignal.analysis.signal_engine.models import SignalSnapshot
from smcsignal.analysis.visualization import DrawingModel

from . import chart as chart_mod
from .config import DeliveryConfig
from .identity import delivery_identity
from .message import SignalMessage, assemble_signal_message, require_buy_signal
from .models import (
    ChartAttachment,
    DeliveryState,
    FailureCategory,
    RenderedSignalMessage,
)
from .sink import DeliveryRegistry, MessageSink, deliver_message


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AnalysisInputError(f"{name} must be a nonempty, trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class OrchestrationConfig:
    """Strict, offline orchestration settings.

    Controls only *how the coordinator delivers* within a process: whether
    delivery is enabled, how many attempts a delivery may take, and whether
    in-memory deduplication is active. It deliberately exposes no setting that
    can reach signal generation, SMC/ICT thresholds, indicators, halal rules,
    Phase 23 governance, optimization, or execution.
    """

    enabled: bool = True
    max_attempts: int = 1
    deduplicate: bool = True

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise AnalysisConfigurationError("enabled must be a boolean")
        attempts = self.max_attempts
        if type(attempts) is not int or attempts < 1:
            raise AnalysisConfigurationError("max_attempts must be a positive integer")
        if type(self.deduplicate) is not bool:
            raise AnalysisConfigurationError("deduplicate must be a boolean")


def load_orchestration_config(path: str | Path) -> OrchestrationConfig:
    """Load the exact ``[orchestration]`` table; unknown keys are rejected."""
    try:
        with Path(path).open("rb") as stream:
            table = tomllib.load(stream).get("orchestration")
    except (OSError, ValueError, TypeError) as exc:
        raise AnalysisConfigurationError(f"cannot load orchestration configuration: {exc}") from exc
    names = {field.name for field in fields(OrchestrationConfig)}
    if not isinstance(table, dict) or set(table) != names:
        raise AnalysisConfigurationError(
            "[orchestration] must contain exactly: " + ", ".join(sorted(names))
        )
    return OrchestrationConfig(**table)


@dataclass(frozen=True, slots=True)
class SignalDeliveryContext:
    """Explicit binding of the upstream context the delivery used.

    Every field is read from the already-projected Phase 24B ``SignalMessage``
    (which itself read only immutable upstream facts). Nothing is reconstructed
    or inferred here, and no upstream field is invented.
    """

    signal_id: str
    symbol: str
    timeframe: str
    reference_price: Decimal | None
    explanation_present: bool
    drawing_id: str | None

    def __post_init__(self) -> None:
        _text(self.signal_id, "signal_id")
        _text(self.symbol, "symbol")
        _text(self.timeframe, "timeframe")
        if self.reference_price is not None and (
            not isinstance(self.reference_price, Decimal) or not self.reference_price.is_finite()
        ):
            raise AnalysisInputError("reference_price must be a finite Decimal or None")
        if type(self.explanation_present) is not bool:
            raise AnalysisInputError("explanation_present must be a boolean")
        if self.drawing_id is not None:
            _text(self.drawing_id, "drawing_id")


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Deterministic delivery envelope for one signal to one destination.

    The envelope carries the immutable ``signal_id``, the deterministic
    ``delivery_id``, the rendered text (and optional chart artifact) from Phase
    24B, the bound context, and the delivery result metadata. It never contains
    invented trading facts (no entry, target, stop-loss, probability, or
    confidence) and no random or wall-clock values.
    """

    signal_id: str
    message_id: str
    delivery_id: str
    destination_id: str
    rendered: RenderedSignalMessage
    context: SignalDeliveryContext
    state: DeliveryState
    failure_category: FailureCategory
    attempt_number: int

    def __post_init__(self) -> None:
        _text(self.signal_id, "signal_id")
        _text(self.message_id, "message_id")
        if not self.message_id.startswith("message:"):
            raise AnalysisInputError("message_id must carry the message: prefix")
        _text(self.delivery_id, "delivery_id")
        if not self.delivery_id.startswith("delivery:"):
            raise AnalysisInputError("delivery_id must carry the delivery: prefix")
        _text(self.destination_id, "destination_id")
        if not isinstance(self.rendered, RenderedSignalMessage):
            raise AnalysisInputError("rendered must be a RenderedSignalMessage")
        if not isinstance(self.context, SignalDeliveryContext):
            raise AnalysisInputError("context must be a SignalDeliveryContext")
        if not isinstance(self.state, DeliveryState):
            raise AnalysisInputError("state must be a DeliveryState")
        if not isinstance(self.failure_category, FailureCategory):
            raise AnalysisInputError("failure_category must be a FailureCategory")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise AnalysisInputError("attempt_number must be a positive integer")
        # Consistency guards (never let the envelope contradict its parts).
        if self.rendered.signal_id != self.signal_id:
            raise AnalysisInputError("rendered message must reference this exact signal")
        if self.context.signal_id != self.signal_id:
            raise AnalysisInputError("context must reference this exact signal")
        if self.rendered.message_id != self.message_id:
            raise AnalysisInputError("rendered message_id must equal the envelope message_id")

    @property
    def caption(self) -> str:
        """Deterministic rendered text of this outcome."""
        return self.rendered.caption

    @property
    def parts(self) -> tuple[str, ...]:
        """Caption split at safe boundaries for transport limits."""
        return self.rendered.parts

    @property
    def chart(self) -> ChartAttachment | None:
        """Optional bound chart artifact (None when chart failed or absent)."""
        return self.rendered.chart

    @property
    def chart_present(self) -> bool:
        return self.rendered.chart is not None

    @property
    def chart_artifact_id(self) -> str | None:
        return self.rendered.chart.artifact_id if self.rendered.chart is not None else None


@dataclass(frozen=True, slots=True)
class DeliveryBatchResult:
    """Order-preserving, deterministic result of a multi-signal delivery run.

    Outcomes appear in the same order as the input frames. Each outcome is
    independent: no signal influences another's content, identity, eligibility,
    or result. No cross-signal optimization or ranking is ever performed.
    """

    outcomes: tuple[DeliveryOutcome, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcomes, tuple):
            raise AnalysisInputError("outcomes must be a tuple")
        if not all(isinstance(o, DeliveryOutcome) for o in self.outcomes):
            raise AnalysisInputError("outcomes must contain DeliveryOutcome values")

    def __len__(self) -> int:
        return len(self.outcomes)

    def count(self, state: DeliveryState) -> int:
        return sum(1 for outcome in self.outcomes if outcome.state is state)

    def delivered(self) -> int:
        return self.count(DeliveryState.DELIVERED) + self.count(DeliveryState.SENT)

    def skipped_duplicates(self) -> int:
        return self.count(DeliveryState.SKIPPED_DUPLICATE)

    def not_attempted(self) -> int:
        return self.count(DeliveryState.NOT_ATTEMPTED)


class DeliveryCoordinator:
    """Offline orchestrator over the Phase 24B presentation core and a sink.

    ``registry`` is the in-memory dedup boundary shared across calls; pass your
    own to deduplicate across coordinators, or omit one to get a fresh registry.
    The coordinator is the only orchestration object that touches the sink;
    formatting and presentation never do.
    """

    def __init__(
        self,
        sink: MessageSink | None,
        *,
        registry: DeliveryRegistry | None = None,
        config: OrchestrationConfig | None = None,
        render_config: DeliveryConfig | None = None,
    ) -> None:
        # ``MessageSink`` is a structural ``Protocol`` (not runtime-checkable),
        # so a runtime ``isinstance`` is impossible; we accept None or any duck
        # type with a ``deliver`` method and let ``deliver_message`` validate.
        self._sink: MessageSink | None = sink
        self._registry = registry if registry is not None else DeliveryRegistry()
        self._config = config if config is not None else OrchestrationConfig()
        self._render_config = render_config if render_config is not None else DeliveryConfig()

    @property
    def config(self) -> OrchestrationConfig:
        return self._config

    @property
    def sink(self) -> MessageSink | None:
        return self._sink

    @property
    def registry(self) -> DeliveryRegistry:
        return self._registry

    def deliver(
        self,
        frame: SignalSnapshot,
        destination_id: str,
        *,
        drawing: DrawingModel | None = None,
        attribution: SetupAttribution | None = None,
        config: DeliveryConfig | None = None,
    ) -> DeliveryOutcome:
        """Deliver one published BUY signal snapshot to one destination offline.

        Only an immutable ``SignalSnapshot`` whose status is ``BUY_SIGNAL`` may
        enter the delivery path; anything else raises so it is never presented
        or broadcast as a buy.
        """
        if not isinstance(frame, SignalSnapshot):
            raise AnalysisInputError("delivery requires a SignalSnapshot")
        _text(destination_id, "destination_id")
        snapshot = require_buy_signal(frame)  # honest BUY boundary
        message = SignalMessage.from_signal(snapshot, attribution)
        chart_attachment = _try_attach_chart(message, drawing)
        settings = config if config is not None else self._render_config
        rendered = assemble_signal_message(message, chart=chart_attachment, config=settings)
        message_id = rendered.message_id
        delivery_id = delivery_identity(message_id, destination_id)
        context = SignalDeliveryContext(
            signal_id=message.signal_id,
            symbol=message.symbol,
            timeframe=message.timeframe,
            reference_price=message.reference_price,
            explanation_present=bool(message.explanation),
            drawing_id=chart_attachment.drawing_id if chart_attachment is not None else None,
        )
        if not self._config.enabled:
            # Master switch off: no sink call; deterministic NOT_ATTEMPTED.
            return DeliveryOutcome(
                signal_id=message.signal_id,
                message_id=message_id,
                delivery_id=delivery_id,
                destination_id=destination_id,
                rendered=rendered,
                context=context,
                state=DeliveryState.NOT_ATTEMPTED,
                failure_category=FailureCategory.CONFIG,
                attempt_number=1,
            )
        registry = self._registry if self._config.deduplicate else None
        receipt = deliver_message(
            rendered,
            destination_id,
            self._sink,
            registry=registry,
            max_attempts=self._config.max_attempts,
        )
        return DeliveryOutcome(
            signal_id=message.signal_id,
            message_id=message_id,
            delivery_id=receipt.delivery_id,
            destination_id=destination_id,
            rendered=rendered,
            context=context,
            state=receipt.state,
            failure_category=receipt.failure_category,
            attempt_number=receipt.attempt_number,
        )

    def deliver_many(
        self,
        frames: Sequence[SignalSnapshot],
        destination_id: str,
        *,
        drawing: DrawingModel | None = None,
        config: DeliveryConfig | None = None,
    ) -> DeliveryBatchResult:
        """Deterministically deliver a sequence of published signals.

        Each frame is processed independently through the same in-memory dedup
        registry and sink; no signal affects another's content, eligibility,
        identity, or result, and there is no cross-signal optimization or
        ranking. Input ordering is preserved in the returned outcomes.
        """
        if not isinstance(frames, (list, tuple)) or not all(
            isinstance(f, SignalSnapshot) for f in frames
        ):
            raise AnalysisInputError("deliver_many requires a sequence of SignalSnapshot")
        outcomes: list[DeliveryOutcome] = []
        for frame in frames:
            outcomes.append(self.deliver(frame, destination_id, drawing=drawing, config=config))
        return DeliveryBatchResult(outcomes=tuple(outcomes))


def _try_attach_chart(
    message: SignalMessage, drawing: DrawingModel | None
) -> ChartAttachment | None:
    """Best-effort chart attachment; never raises and never mutates the message.

    A drawing whose series does not match the signal (or that fails to render)
    is dropped so the text message still delivers.
    """
    if drawing is None:
        return None
    try:
        return chart_mod.svg_attachment(
            drawing,
            signal_id=message.signal_id,
            symbol=message.symbol,
            timeframe=message.timeframe,
        )
    except AnalysisInputError:
        return None
