"""Phase 35B: explicit declared-configuration binding for the persisted live window.

The invariant enforced here is:

    «A persisted live window may only be resumed when it is explicitly bound
    to the exact declared BacktestConfiguration identity that produced that
    window.»

Identity reuse (no second canon)
---------------------------------

The identity is the Phase 28 canonical ``configuration_digest`` — the
``configuration:``-prefixed digest embedded in ``configuration_bytes``
produced through the repository's existing evidence canon (sorted compact
JSON, exact Decimal text, enum values). The embedded digest is never trusted
blindly for a stored artifact: ``ConfigurationStore.load`` already re-decodes
through the frozen constructors and recomputes the digest. For a live
``BacktestConfiguration`` value we recompute the canonical bytes and extract
the embedded digest, which is equivalent to Phase 30's
``_document_identity`` precedent.

Keys
----

- live configuration: ``configuration:live:{symbol}:{timeframe}``
- live window:        ``window:live:{symbol}:{timeframe}``

The window key convention is intentionally identical to the frozen Phase 33
``LiveService`` (``f\"window:live:{symbol}:{timeframe}\"``). Because
``service.py`` is frozen and does not export its private helper, this module
re-derives the same deterministic string and a regression test pins the two
conventions together.

Binding semantics (all verified before ``start_live_service``)
--------------------------------------------------------------

Case A — no window, no config artifact: save declared config, allow start.
Case B — window + matching config: allow restart, do not rewrite.
Case C — window + missing config: FAIL CLOSED (legacy unbound window).
Case D — window + mismatching config: FAIL CLOSED (even if frames identical).
Case E — corrupt config artifact: propagate Phase 28 integrity exception.
Case F — config artifact exists, window absent: verify identity, allow fresh
         start, do not require a window.
Case G — ledger exists + window exists + matching config: allow (26G unchanged).
Case H — ledger exists + window exists + mismatching config: reject before
         the frozen service is entered.
Case I — window exists + ledger missing + matching config: allow (26G fresh).
Case I'— window exists + ledger missing + mismatching config: reject.

Legacy adoption is explicit and opt-in: ``adopt_unbound_window=True`` saves
the supplied declared configuration for an otherwise unbound window. Default
is ``False`` — no silent migration.

Ordering
--------

All verification happens before ``start_live_service``. For a fresh run the
configuration artifact is persisted first, so a crash never leaves a
bound-window-without-config state. No cross-file transaction is introduced;
the configuration artifact remains independently atomic via Phase 28's
``.partial`` + ``os.replace`` discipline.

This module imports only the public frozen seams it needs:

- ``BacktestConfiguration`` (Phase 20 model)
- ``ConfigurationStore`` + ``configuration_bytes`` (Phase 28)
- ``DatasetStore`` (Phase 27)
- ``LiveConfigurationError`` (Phase 33)

It never imports Phase 29 declarations, materialization, composition, runs,
series, persistence, delivery, monitoring, or Telegram.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from smcsignal.analysis.backtest import BacktestConfiguration
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.configurations import ConfigurationStore, configuration_bytes
from smcsignal.datasets import DatasetStore
from smcsignal.live.config import LiveConfigurationError

LIVE_CONFIGURATION_KEY_PREFIX = "configuration:live:"
LIVE_WINDOW_KEY_PREFIX = "window:live:"

Outcome = Literal["declared", "verified", "adopted"]


def _require_symbol_timeframe(symbol: object, timeframe: object) -> tuple[str, str]:
    if not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip():
        raise LiveConfigurationError("symbol must be a nonempty, trimmed string")
    if not isinstance(timeframe, str) or not timeframe.strip() or timeframe != timeframe.strip():
        raise LiveConfigurationError("timeframe must be a nonempty, trimmed string")
    return symbol, timeframe


def live_configuration_key(symbol: str, timeframe: str) -> str:
    """Deterministic Phase 28 key for the live configuration artifact."""

    sym, tf = _require_symbol_timeframe(symbol, timeframe)
    return f"{LIVE_CONFIGURATION_KEY_PREFIX}{sym}:{tf}"


def live_window_key(symbol: str, timeframe: str) -> str:
    """Deterministic Phase 27 key for the live candle window.

    The format is intentionally identical to the frozen Phase 33
    ``LiveService`` convention ``f\"window:live:{symbol}:{timeframe}\"``.
    """

    sym, tf = _require_symbol_timeframe(symbol, timeframe)
    return f"{LIVE_WINDOW_KEY_PREFIX}{sym}:{tf}"


def configuration_identity(configuration: BacktestConfiguration) -> str:
    """Canonical Phase 28 identity of one declared configuration.

    Derived from ``configuration_bytes(configuration)`` — the same bytes
    Phase 28 persists — and the embedded ``configuration_digest``. The digest
    is recomputed from the supplied configuration value, not trusted from a
    stored file. Phase 28's load path already recomputes and verifies the
    digest for a stored artifact.
    """

    if not isinstance(configuration, BacktestConfiguration):
        raise AnalysisInputError("configuration_identity requires a BacktestConfiguration")
    payload = configuration_bytes(configuration)
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        # Should never happen: configuration_bytes always produces canonical JSON.
        raise AnalysisInputError("configuration bytes are not canonical JSON") from exc
    digest_value = document.get("configuration_digest")
    if not isinstance(digest_value, str) or not digest_value:
        raise AnalysisInputError("configuration document missing configuration_digest")
    return digest_value


@dataclass(frozen=True, slots=True)
class LiveConfigurationBinding:
    """Result of a successful live configuration binding."""

    configuration_key: str
    identity: str
    outcome: Outcome


def bind_live_configuration(
    *,
    configuration: BacktestConfiguration,
    symbol: str,
    timeframe: str,
    configuration_store: ConfigurationStore,
    window_store: DatasetStore,
    adopt_unbound_window: bool = False,
) -> LiveConfigurationBinding:
    """Verify and bind the persisted live window to the declared configuration.

    The check happens entirely before ``start_live_service`` is called. No
    window or ledger mutation occurs in this function — only an optional
    Phase 28 ``save`` for the fresh-run and explicit-adoption paths.

    Raises
    ------
    LiveConfigurationError
        When the persisted window exists without a bound configuration artifact
        (and adoption is disabled) or when the bound artifact's identity does
        not equal the declared identity — even if frames or ledger would be
        identical.
    AnalysisInputError (or subclass)
        When the stored configuration artifact is corrupt or fails Phase 28
        integrity checks. The exception is propagated unchanged (fail closed).

    Returns
    -------
    LiveConfigurationBinding
        The bound key, the verified canonical identity, and the outcome:
        ``declared`` (fresh), ``verified`` (restart), or ``adopted`` (legacy).
    """

    if not isinstance(configuration, BacktestConfiguration):
        raise AnalysisInputError("bind_live_configuration requires a BacktestConfiguration")
    if not isinstance(adopt_unbound_window, bool):
        raise AnalysisInputError("adopt_unbound_window must be a boolean")

    # Basic protocol shape checks — keep the error surface close to the
    # existing store contracts without importing their private validators.
    if not hasattr(configuration_store, "load") or not hasattr(configuration_store, "save"):
        raise AnalysisInputError("configuration_store must implement load/save")
    if not hasattr(window_store, "contains"):
        raise AnalysisInputError("window_store must implement contains")

    sym, tf = _require_symbol_timeframe(symbol, timeframe)
    config_key = live_configuration_key(sym, tf)
    win_key = live_window_key(sym, tf)

    declared_identity = configuration_identity(configuration)

    # Phase 28 load already verifies/recomputes the digest; corruption
    # propagates as AnalysisInputError (Case E).
    stored_configuration = configuration_store.load(config_key)
    stored_exists = stored_configuration is not None

    window_exists = bool(window_store.contains(win_key))

    # Case A: no window, no config → declare
    if not stored_exists and not window_exists:
        configuration_store.save(config_key, configuration)
        return LiveConfigurationBinding(
            configuration_key=config_key,
            identity=declared_identity,
            outcome="declared",
        )

    # Case C + adoption: window exists, config missing
    if not stored_exists and window_exists:
        if not adopt_unbound_window:
            raise LiveConfigurationError(
                f"persisted live window {win_key!r} exists without a bound "
                f"configuration artifact {config_key!r}; refusing to resume "
                f"without explicit adoption (adopt_unbound_window=True)"
            )
        configuration_store.save(config_key, configuration)
        return LiveConfigurationBinding(
            configuration_key=config_key,
            identity=declared_identity,
            outcome="adopted",
        )

    # At this point stored_exists is True — we have a verified artifact.
    assert stored_configuration is not None
    stored_identity = configuration_identity(stored_configuration)

    # Case F: config exists, window absent → verify identity, allow fresh
    if stored_exists and not window_exists:
        if stored_identity != declared_identity:
            raise LiveConfigurationError(
                f"configuration artifact {config_key!r} is bound to "
                f"{stored_identity!r} but declared configuration is "
                f"{declared_identity!r}; refusing to start with a different "
                f"declared configuration"
            )
        return LiveConfigurationBinding(
            configuration_key=config_key,
            identity=declared_identity,
            outcome="verified",
        )

    # Case B/D/G/H/I/I': window exists, config exists → must match
    if stored_identity != declared_identity:
        raise LiveConfigurationError(
            f"persisted live window {win_key!r} is bound to configuration "
            f"{stored_identity!r} but declared configuration is "
            f"{declared_identity!r}; refusing to resume under a different "
            f"declared configuration"
        )
    return LiveConfigurationBinding(
        configuration_key=config_key,
        identity=declared_identity,
        outcome="verified",
    )
