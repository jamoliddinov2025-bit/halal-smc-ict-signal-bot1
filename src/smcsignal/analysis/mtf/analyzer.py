"""Join already-generated HTF frames onto a primary OTE stream without lookahead."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from hashlib import sha256

from smcsignal.analysis.errors import AnalysisConfigurationError, AnalysisInputError
from smcsignal.analysis.liquidity.time import candle_close_time
from smcsignal.analysis.mtf.calculation import is_eligible, primary_opened_at
from smcsignal.analysis.mtf.config import MTFConfig
from smcsignal.analysis.mtf.evidence import (
    build_relation,
    configuration_artifact,
    freeze_evidence,
    publication_refs,
    snapshot_provenance,
)
from smcsignal.analysis.mtf.models import MTFEvidenceReference, MTFRelation, MTFSnapshot
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.provenance import SeriesProvenance


def _materialize(values: Iterable[OTESnapshot], timeframe: str) -> tuple[OTESnapshot, ...]:
    try:
        frames = tuple(values)
    except TypeError as exc:
        raise AnalysisInputError("HTF frames must be an iterable of OTESnapshot records") from exc
    previous: OTESnapshot | None = None
    for index, frame in enumerate(frames):
        if not isinstance(frame, OTESnapshot):
            raise AnalysisInputError("MTF higher-timeframe input must be OTESnapshot frames")
        observation = frame.upstream.observation
        series = frame.provenance.series
        if observation.reference.candle_index != index:
            raise AnalysisInputError("HTF history must start at observed index zero")
        if series.timeframe != timeframe:
            raise AnalysisInputError("HTF frame timeframe does not match its mapping key")
        closed_at = candle_close_time(observation.reference.opened_at, timeframe)
        if observation.reference.closed_at != closed_at:
            raise AnalysisInputError("HTF closed_at must match the declared timeframe")
        if previous is not None:
            prior = previous.upstream.observation
            if series != previous.provenance.series:
                raise AnalysisInputError("HTF series cannot change during a replay")
            if frame.provenance.available_at < previous.provenance.available_at:
                raise AnalysisInputError("HTF availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError("HTF candles must be chronological and unique")
        previous = frame
    return frames


class MTFAnalyzer:
    """Each output answers which completed HTF context was known at the LTF open."""

    def __init__(
        self,
        config: MTFConfig | None = None,
        *,
        higher: Mapping[str, Iterable[OTESnapshot]],
    ) -> None:
        if config is not None and not isinstance(config, MTFConfig):
            raise AnalysisConfigurationError("config must be MTFConfig")
        self._config = config if config is not None else MTFConfig()
        configured = set(self._config.higher_timeframes)
        if not isinstance(higher, Mapping) or set(higher) != configured:
            raise AnalysisInputError(
                "higher frames must be supplied for exactly the configured HTFs"
            )
        loaded = {
            timeframe: _materialize(higher[timeframe], timeframe)
            for timeframe in self._config.higher_timeframes
        }
        symbol: str | None = None
        for _timeframe, frames in loaded.items():
            if not frames:
                continue
            current = frames[0].provenance.series.symbol
            if symbol is None:
                symbol = current
            elif current != symbol:
                raise AnalysisInputError("MTF cannot mix symbols across higher timeframes")
        self._higher = loaded
        self._symbol = symbol
        self._cursor = dict.fromkeys(self._config.higher_timeframes, 0)
        self._published: dict[str, tuple[MTFEvidenceReference, ...]] = {
            timeframe: () for timeframe in self._config.higher_timeframes
        }
        self._latest: MTFSnapshot | None = None
        self._count = 0
        self._artifact: bytes | None = None

    @property
    def config(self) -> MTFConfig:
        return self._config

    @property
    def latest(self) -> MTFSnapshot | None:
        return self._latest

    @property
    def processed_count(self) -> int:
        return self._count

    @property
    def series(self) -> SeriesProvenance | None:
        return self._latest.provenance.series if self._latest is not None else None

    @property
    def configuration_artifact(self) -> bytes | None:
        return self._artifact

    def update(self, frame: OTESnapshot) -> MTFSnapshot:
        if not isinstance(frame, OTESnapshot):
            raise AnalysisInputError(
                "MTF requires existing OTE frames starting at zero in consecutive unique order"
            )
        if frame.upstream.observation.reference.candle_index != self._count:
            raise AnalysisInputError(
                "MTF requires existing OTE frames starting at zero in consecutive unique order"
            )
        observation = frame.upstream.observation
        series = frame.provenance.series
        if series.timeframe != self._config.primary_timeframe:
            raise AnalysisInputError("primary timeframe does not match MTF configuration")
        if observation.reference.closed_at != candle_close_time(
            observation.reference.opened_at, series.timeframe
        ):
            raise AnalysisInputError("primary closed_at must match the declared primary timeframe")
        if self._symbol is None:
            symbol = series.symbol
        elif series.symbol != self._symbol:
            raise AnalysisInputError("MTF primary symbol does not match HTF symbol")
        else:
            symbol = self._symbol
        previous = self._latest.upstream if self._latest is not None else None
        if previous is not None:
            prior = previous.upstream.observation
            if series != previous.provenance.series:
                raise AnalysisInputError("MTF primary series cannot change during a replay")
            if frame.provenance.available_at < previous.provenance.available_at:
                raise AnalysisInputError("primary availability must not rewind")
            if observation.reference.opened_at <= prior.reference.opened_at:
                raise AnalysisInputError("primary candles must be chronological and unique")
        opened_at = primary_opened_at(frame)
        relations: list[MTFRelation] = []
        next_cursor: dict[str, int] = {}
        next_published: dict[str, tuple[MTFEvidenceReference, ...]] = {}
        for timeframe in self._config.higher_timeframes:
            frames = self._higher[timeframe]
            index = self._cursor[timeframe]
            published = list(self._published[timeframe])
            while index < len(frames) and is_eligible(
                frames[index].provenance.available_at, opened_at
            ):
                published.extend(publication_refs(frames[index], timeframe))
                index += 1
            latest = frames[index - 1] if index else None
            frozen = freeze_evidence(tuple(published), latest, timeframe)
            relations.append(build_relation(timeframe, latest, frozen))
            next_cursor[timeframe] = index
            next_published[timeframe] = tuple(published)
        if self._artifact is None:
            artifact = configuration_artifact(self.config)
        else:
            artifact = self._artifact
        config_hash = sha256(artifact).hexdigest()
        items = tuple(relations)
        meta = snapshot_provenance(frame, items, config_hash)
        result = MTFSnapshot(self.config, frame, items, meta)
        self._cursor, self._published, self._latest, self._artifact, self._symbol = (
            next_cursor,
            next_published,
            result,
            artifact,
            symbol,
        )
        self._count += 1
        return result


def analyze_mtf(
    frames: Iterable[OTESnapshot],
    higher: Mapping[str, Iterable[OTESnapshot]],
    config: MTFConfig | None = None,
) -> tuple[MTFSnapshot, ...]:
    engine = MTFAnalyzer(config, higher=higher)
    try:
        iterator = iter(frames)
    except TypeError as exc:
        raise AnalysisInputError("frames must be an iterable of OTESnapshot records") from exc
    return tuple(engine.update(frame) for frame in iterator)
