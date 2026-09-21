"""Phase 30: verified declared-run input materialization — an input-resolution boundary.

Phases 27, 28, and 29 make every durable input of a declared historical run
restorable: the declared ``ReplayDataset`` (Phase 27), the declared
``BacktestConfiguration`` (Phase 28), and the ``DeclaredRunBinding`` that
associates their opaque keys with a ledger key while pinning the Phase 27
``content_digest`` and the Phase 28 ``configuration_digest`` captured at save
time (Phase 29). What remained caller convention was the step between
restoring a binding and composing the frozen Phase 26H seam: load the two
stores by the recorded keys and confirm that what came back is still what the
binding declared. Phase 30 makes that step one verified value.

What materialization does
-------------------------

``materialize_declared_inputs`` restores the binding through the supplied
``RunBindingStore``, loads the declared dataset and configuration through the
supplied ``DatasetStore`` and ``ConfigurationStore`` by the recorded keys, and
constructs ``VerifiedRunInputs`` — whose constructor derives each restored
value's canonical identity through the existing Phase 27/28 public canon
(``dataset_bytes`` / ``configuration_bytes``) and compares it with the
``dataset_digest`` / ``configuration_digest`` pinned in the binding. A missing
binding, dataset, or configuration, a binding that fails its own integrity
check, a dataset or configuration that fails restoration, or an identity that
differs from the binding all raise ``AnalysisInputError``; nothing partially
verified is ever returned, and a ``VerifiedRunInputs`` value cannot exist with
inputs that differ from what its binding declares.

Identity (no second canon)
--------------------------

This module computes no digest of its own and never touches the evidence
canon directly. The identity of a restored dataset is the ``content_digest``
field of its canonical Phase 27 document — exactly the string Phase 29
records as ``dataset_digest`` — and the identity of a restored configuration
is the ``configuration_digest`` field of its canonical Phase 28 document —
exactly the string Phase 29 records as ``configuration_digest``. Both
documents are produced by the frozen Phase 27/28 public APIs from the
restored values, so a value replaced under its key, a store that returned the
wrong value, or a binding whose pins name a different value all fail the
comparison.

What this boundary is NOT
-------------------------

- No execution: it creates no run, session, lifecycle, ledger, or report, and
  it never imports or calls the Phase 26H composition seam. The caller passes
  ``VerifiedRunInputs.dataset``, ``.configuration``, and ``.ledger_key`` to
  the frozen Phase 26H API itself.
- No persistence: it writes nothing — no document, snapshot, cache,
  temporary file, or ``os.replace`` — and it modifies no store, dataset,
  configuration, binding, or ledger. Repeated materialization over the same
  binding and store contents yields equal values and the same identities,
  before or after a restart.
- No new format: no Phase 30 document, digest, key scheme, or store. Key
  safety is the existing stores' — every key reaches them unmodified, so the
  closed charset, reserved-name, and traversal rules apply unchanged.
- No composition upward: materialization never imports analytics,
  persistence, series, sessions, runs, delivery, monitoring, data providers,
  or CLI. It is a downstream leaf over Phases 27, 28, and 29.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from smcsignal.analysis.backtest import BacktestConfiguration, ReplayDataset
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.configurations import ConfigurationStore, configuration_bytes
from smcsignal.datasets import DatasetStore, dataset_bytes
from smcsignal.declarations import DeclaredRunBinding, RunBindingStore

#: The identity field of a canonical Phase 27 dataset document — the string a
#: Phase 29 binding records as ``dataset_digest``.
_DATASET_IDENTITY_FIELD = "content_digest"
#: The identity field of a canonical Phase 28 configuration document — the
#: string a Phase 29 binding records as ``configuration_digest``.
_CONFIGURATION_IDENTITY_FIELD = "configuration_digest"


def _document_identity(payload: bytes, field: str, name: str) -> str:
    """Read the identity a canonical Phase 27/28 document embeds; compute nothing.

    ``payload`` is exactly what the frozen public canon produced from the
    restored value. Only the embedded identity field is read back; if the
    document does not carry it, resolution fails closed rather than inventing
    a substitute.
    """

    document = json.loads(payload)
    identity = document.get(field) if isinstance(document, dict) else None
    if not isinstance(identity, str) or not identity:
        raise AnalysisInputError(f"canonical {name} document does not carry a {field} identity")
    return identity


def _dataset_identity(dataset: ReplayDataset) -> str:
    """The canonical Phase 27 identity of one dataset, through ``dataset_bytes``."""

    return _document_identity(dataset_bytes(dataset), _DATASET_IDENTITY_FIELD, "dataset")


def _configuration_identity(configuration: BacktestConfiguration) -> str:
    """The canonical Phase 28 identity of one configuration, through ``configuration_bytes``."""

    return _document_identity(
        configuration_bytes(configuration), _CONFIGURATION_IDENTITY_FIELD, "configuration"
    )


def _verify_dataset(binding: DeclaredRunBinding, dataset: ReplayDataset) -> None:
    identity = _dataset_identity(dataset)
    if identity != binding.dataset_digest:
        raise AnalysisInputError(
            f"declared dataset {binding.dataset_key!r} has identity {identity}, which differs "
            f"from the binding's dataset_digest {binding.dataset_digest}"
        )


def _verify_configuration(
    binding: DeclaredRunBinding, configuration: BacktestConfiguration
) -> None:
    identity = _configuration_identity(configuration)
    if identity != binding.configuration_digest:
        raise AnalysisInputError(
            f"declared configuration {binding.configuration_key!r} has identity {identity}, "
            f"which differs from the binding's configuration_digest "
            f"{binding.configuration_digest}"
        )


@dataclass(frozen=True, slots=True)
class VerifiedRunInputs:
    """Frozen, verified materialization of one declared run's inputs.

    Holds the restored Phase 29 binding together with the restored Phase 27
    dataset and Phase 28 configuration whose canonical identities matched the
    binding's ``dataset_digest`` and ``configuration_digest``. Construction
    *is* the verification: the identities are derived through the existing
    Phase 27/28 public canon and compared here, so a value of this type cannot
    exist with a dataset or configuration that differs from what its binding
    declares.

    It is an input-resolution value only — not a run, session, report,
    ledger, execution result, or runtime object. The caller composes
    ``dataset``, ``configuration``, and ``ledger_key`` into the frozen Phase
    26H seam; this value never does.
    """

    binding: DeclaredRunBinding
    dataset: ReplayDataset
    configuration: BacktestConfiguration

    def __post_init__(self) -> None:
        if not isinstance(self.binding, DeclaredRunBinding):
            raise AnalysisInputError("VerifiedRunInputs requires a DeclaredRunBinding")
        if not isinstance(self.dataset, ReplayDataset):
            raise AnalysisInputError("VerifiedRunInputs requires a ReplayDataset")
        if not isinstance(self.configuration, BacktestConfiguration):
            raise AnalysisInputError("VerifiedRunInputs requires a BacktestConfiguration")
        _verify_dataset(self.binding, self.dataset)
        _verify_configuration(self.binding, self.configuration)

    @property
    def ledger_key(self) -> str:
        """The declared Phase 26D ledger key, exactly as the binding records it."""

        return self.binding.ledger_key


def materialize_declared_inputs(
    binding_store: RunBindingStore,
    binding_key: str,
    dataset_store: DatasetStore,
    configuration_store: ConfigurationStore,
) -> VerifiedRunInputs:
    """Restore one declared run's binding and verify-load its dataset and configuration.

    Steps: restore the binding under ``binding_key`` (the binding store's own
    key-safety and integrity rules apply unchanged), load the dataset under
    ``binding.dataset_key`` and the configuration under
    ``binding.configuration_key`` (each store's own restoration rules apply
    unchanged), then construct ``VerifiedRunInputs`` — which derives each
    restored value's canonical Phase 27/28 identity and compares it with the
    identity the binding pinned. A missing entry, a failed restoration, or an
    identity difference raises ``AnalysisInputError`` and nothing is returned.

    Read-only and deterministic: the three stores are only read, nothing is
    written or cached, and identical binding and store contents yield equal
    values with the same identities — before or after a restart. The result
    is input resolution only; the caller invokes the frozen Phase 26H seam.
    """

    binding = binding_store.load(binding_key)
    if binding is None:
        raise AnalysisInputError(f"declared run binding {binding_key!r} is missing")
    if not isinstance(binding, DeclaredRunBinding):
        raise AnalysisInputError("run binding store must restore a DeclaredRunBinding")
    dataset = dataset_store.load(binding.dataset_key)
    if dataset is None:
        raise AnalysisInputError(
            f"declared dataset {binding.dataset_key!r} is missing for run binding {binding_key!r}"
        )
    configuration = configuration_store.load(binding.configuration_key)
    if configuration is None:
        raise AnalysisInputError(
            f"declared configuration {binding.configuration_key!r} is missing for run binding "
            f"{binding_key!r}"
        )
    return VerifiedRunInputs(binding=binding, dataset=dataset, configuration=configuration)
