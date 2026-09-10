"""Immutable HTF relations and published LTF confluence snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field

from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.models import _metadata
from smcsignal.analysis.liquidity.time import candle_close_time
from smcsignal.analysis.mtf.calculation import (
    KIND_RANK,
    MTFDirection,
    MTFEvidenceKind,
    confluence,
    is_eligible,
    primary_opened_at,
)
from smcsignal.analysis.mtf.config import MTFConfig
from smcsignal.analysis.ote.calculation import OTEClassification
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.premium_discount.calculation import PDClassification
from smcsignal.analysis.provenance import EvidenceProvenance, EvidenceReference
from smcsignal.data.config import SUPPORTED_TIMEFRAMES


@dataclass(frozen=True, slots=True)
class MTFEvidenceReference:
    """Pointer to already-published HTF evidence; original IDs are unchanged."""

    timeframe: str
    kind: MTFEvidenceKind
    source: EvidenceReference
    eligible: bool = True

    def __post_init__(self) -> None:
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            raise AnalysisInputError("MTF evidence requires a supported HTF timeframe")
        if not isinstance(self.kind, MTFEvidenceKind):
            raise AnalysisInputError("MTF evidence kind must be an MTFEvidenceKind")
        if not isinstance(self.source, EvidenceReference):
            raise AnalysisInputError("MTF evidence must reference exact immutable HTF evidence")
        if self.source.series.timeframe != self.timeframe:
            raise AnalysisInputError("MTF evidence timeframe must match the source series")
        if type(self.eligible) is not bool or not self.eligible:
            raise AnalysisInputError("MTF-v1 retains only causally eligible HTF evidence")


@dataclass(frozen=True, slots=True)
class MTFRelation:
    """One independent HTF join for a single primary observation."""

    timeframe: str
    latest: OTESnapshot | None
    direction: MTFDirection
    pd_classification: PDClassification | None
    ote_classification: OTEClassification | None
    evidence: tuple[MTFEvidenceReference, ...]
    insufficient_reason: str | None

    def __post_init__(self) -> None:
        if self.timeframe not in SUPPORTED_TIMEFRAMES:
            raise AnalysisInputError("MTF relation requires a supported HTF timeframe")
        if not isinstance(self.direction, MTFDirection) or self.direction is MTFDirection.MIXED:
            raise AnalysisInputError("a single HTF relation cannot carry a mixed confluence label")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, MTFEvidenceReference) for item in self.evidence
        ):
            raise AnalysisInputError("MTF evidence must be an immutable typed tuple")
        if any(item.timeframe != self.timeframe for item in self.evidence):
            raise AnalysisInputError("relation evidence must belong to this HTF")
        ranks = [
            (item.source.available_at, KIND_RANK[item.kind], item.source.evidence_id)
            for item in self.evidence
        ]
        if ranks != sorted(ranks):
            raise AnalysisInputError("HTF evidence must be ordered by time, kind, then evidence ID")
        ids = [item.source.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise AnalysisInputError("duplicate HTF evidence IDs are forbidden")
        if self.latest is None:
            if (
                self.pd_classification is not None
                or self.ote_classification is not None
                or self.direction is not MTFDirection.INSUFFICIENT_CONTEXT
                or self.insufficient_reason != "no_completed_htf_candle"
            ):
                raise AnalysisInputError("missing HTF context must be explicit, never implied")
            return
        if not isinstance(self.latest, OTESnapshot):
            raise AnalysisInputError("latest HTF context must be an existing OTE snapshot")
        if self.latest.provenance.series.timeframe != self.timeframe:
            raise AnalysisInputError("latest HTF snapshot timeframe does not match the relation")
        if self.pd_classification != self.latest.upstream.classification:
            raise AnalysisInputError("HTF PD classification must copy the latest eligible snapshot")
        if self.ote_classification != self.latest.classification:
            raise AnalysisInputError(
                "HTF OTE classification must copy the latest eligible snapshot"
            )
        if self.direction is MTFDirection.INSUFFICIENT_CONTEXT:
            if self.insufficient_reason != "htf_structure_not_ready":
                raise AnalysisInputError("unready HTF structure must use an explicit reason")
        elif self.insufficient_reason is not None:
            raise AnalysisInputError("directional HTF context cannot carry an insufficient reason")


@dataclass(frozen=True, slots=True)
class MTFSnapshot:
    settings: MTFConfig
    upstream: OTESnapshot
    relations: tuple[MTFRelation, ...]
    provenance: EvidenceProvenance
    direction: MTFDirection = field(init=False)
    evidence: tuple[MTFEvidenceReference, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.settings, MTFConfig) or not isinstance(self.upstream, OTESnapshot):
            raise AnalysisInputError(
                "MTF snapshot requires settings and an existing primary OTE frame"
            )
        primary = self.upstream
        series = primary.provenance.series
        if series.timeframe != self.settings.primary_timeframe:
            raise AnalysisInputError("primary OTE frame timeframe does not match MTF configuration")
        observation = primary.upstream.observation
        if observation.reference.closed_at != candle_close_time(
            observation.reference.opened_at, series.timeframe
        ):
            raise AnalysisInputError("primary closed_at must match the declared primary timeframe")
        opened_at = primary_opened_at(primary)
        if not isinstance(self.relations, tuple) or len(self.relations) != len(
            self.settings.higher_timeframes
        ):
            raise AnalysisInputError("MTF relations must match the configured HTF list exactly")
        collected: list[MTFEvidenceReference] = []
        dependencies = [primary.provenance.as_reference()]
        pairs = zip(self.settings.higher_timeframes, self.relations, strict=True)
        for timeframe, relation in pairs:
            if not isinstance(relation, MTFRelation) or relation.timeframe != timeframe:
                raise AnalysisInputError(
                    "MTF relations must follow configured HTF order and identity"
                )
            latest = relation.latest
            if latest is not None:
                if latest.provenance.series.symbol != series.symbol:
                    raise AnalysisInputError("MTF cannot mix symbols across timeframes")
                if not is_eligible(latest.provenance.available_at, opened_at):
                    raise AnalysisInputError(
                        "latest HTF snapshot is not eligible at the primary open"
                    )
                dependencies.append(latest.provenance.as_reference())
            for item in relation.evidence:
                if not is_eligible(item.source.available_at, opened_at):
                    raise AnalysisInputError("HTF evidence is not eligible at the primary open")
                if item.source.series.symbol != series.symbol:
                    raise AnalysisInputError(
                        "HTF evidence symbol does not match the primary series"
                    )
            collected.extend(relation.evidence)
        _metadata(self.provenance, observation, tuple(dependencies))
        if self.provenance.input_prefix_hash != primary.provenance.input_prefix_hash:
            raise AnalysisInputError("MTF snapshot must reuse the current primary consumed prefix")
        if self.provenance.series != series:
            raise AnalysisInputError("MTF snapshot series must be the primary series")
        labels = tuple(item.direction for item in self.relations)
        object.__setattr__(self, "direction", confluence(labels))
        object.__setattr__(self, "evidence", tuple(collected))
