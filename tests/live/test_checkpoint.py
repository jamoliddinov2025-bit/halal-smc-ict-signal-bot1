"""Phase 35E checkpoint tests: determinism, integrity, atomicity, identity."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smcsignal.analysis.backtest import BacktestConfiguration, ReplayDataset
from smcsignal.analysis.errors import AnalysisInputError
from smcsignal.analysis.liquidity.evidence import canonical_bytes, digest
from smcsignal.analysis.provenance import SeriesProvenance
from smcsignal.live.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    FileCheckpointStore,
    LiveCheckpoint,
    LiveCheckpointError,
    MemoryCheckpointStore,
    checkpoint_bytes,
    live_checkpoint_key,
    load_checkpoint_bytes,
    verify_checkpoint_identity,
)
from smcsignal.live.configuration_binding import configuration_identity
from smcsignal.live.runtime import LiveRuntime
from tests.backtest.helpers import configuration as make_configuration
from tests.backtest.helpers import dataset as make_dataset


def series_for(ds: ReplayDataset) -> SeriesProvenance:
    return SeriesProvenance(ds.symbol, ds.timeframe, ds.venue, ds.provider, ds.dataset_id)


def sample_checkpoint(cfg: BacktestConfiguration | None = None) -> LiveCheckpoint:
    ds = make_dataset()
    pipeline = make_configuration() if cfg is None else cfg
    series = series_for(ds)
    runtime = LiveRuntime(pipeline, series=series, higher_candles=ds.higher_candles)
    runtime.warm_up(ds.candles)
    state = runtime.export_checkpoint_state()
    return LiveCheckpoint(
        configuration_identity=configuration_identity(pipeline),
        symbol=series.symbol,
        primary_timeframe=series.timeframe,
        higher_timeframes=tuple(runtime.mtf_config.higher_timeframes),
        series=series,
        last_primary=state.last_primary,
        last_higher=dict(state.last_higher),
        frame_count=state.frame_count,
        retention_local_bound=20,
        primary_candles=state.primary_candles,
        higher_candles=dict(state.higher_candles),
        published_setups=state.published_setups,
        latest_signal_id=state.latest_signal_id,
    )


def test_checkpoint_roundtrip_is_byte_identical_and_digest_matches() -> None:
    cp = sample_checkpoint()
    payload = checkpoint_bytes(cp)
    restored = load_checkpoint_bytes(payload)
    assert restored == cp
    assert checkpoint_bytes(restored) == payload
    # Same logical state → identical canonical bytes and digest.
    again = sample_checkpoint()
    assert checkpoint_bytes(again) == payload


def test_corrupted_bytes_are_rejected() -> None:
    cp = sample_checkpoint()
    payload = bytearray(checkpoint_bytes(cp))
    # Flip one byte in the middle of the JSON body.
    payload[len(payload) // 2] ^= 0x01
    with pytest.raises(LiveCheckpointError):
        load_checkpoint_bytes(bytes(payload))


def test_truncated_or_partial_bytes_are_rejected() -> None:
    cp = sample_checkpoint()
    payload = checkpoint_bytes(cp)
    with pytest.raises(LiveCheckpointError):
        load_checkpoint_bytes(payload[: len(payload) // 2])
    with pytest.raises(LiveCheckpointError):
        load_checkpoint_bytes(b"")


def test_tampered_digest_without_content_change_is_rejected() -> None:
    cp = sample_checkpoint()
    document = json.loads(checkpoint_bytes(cp))
    document["content_digest"] = "checkpoint:" + "0" * 64
    with pytest.raises(LiveCheckpointError, match="digest"):
        load_checkpoint_bytes(canonical_bytes(document))


def test_unsupported_schema_version_is_rejected() -> None:
    cp = sample_checkpoint()
    document = json.loads(checkpoint_bytes(cp))
    document["schema_version"] = CHECKPOINT_SCHEMA_VERSION + 1
    # Re-embed a plausible digest so we fail on schema, not digest.
    content = {k: v for k, v in document.items() if k != "content_digest"}
    document["content_digest"] = "checkpoint:" + digest(content)
    with pytest.raises(LiveCheckpointError, match="schema_version"):
        load_checkpoint_bytes(canonical_bytes(document))


def test_every_unsupported_schema_version_shape_is_rejected() -> None:
    """C1 at the format boundary: older, newer, and non-integer versions fail."""

    cp = sample_checkpoint()
    for bad_version in (CHECKPOINT_SCHEMA_VERSION - 1, CHECKPOINT_SCHEMA_VERSION + 1, 0, 99, "1"):
        document = json.loads(checkpoint_bytes(cp))
        document["schema_version"] = bad_version
        # Re-embed a valid digest so the failure can only be the schema gate.
        content = {k: v for k, v in document.items() if k != "content_digest"}
        document["content_digest"] = "checkpoint:" + digest(content)
        with pytest.raises(LiveCheckpointError, match="schema_version"):
            load_checkpoint_bytes(canonical_bytes(document))


def test_live_checkpoint_constructor_refuses_any_other_schema_version() -> None:
    """C1 at the model boundary: the dataclass itself is version-locked."""

    cp = sample_checkpoint()
    for bad_version in (CHECKPOINT_SCHEMA_VERSION - 1, CHECKPOINT_SCHEMA_VERSION + 1, 0, "1"):
        with pytest.raises(LiveCheckpointError, match="schema_version"):
            LiveCheckpoint(
                configuration_identity=cp.configuration_identity,
                symbol=cp.symbol,
                primary_timeframe=cp.primary_timeframe,
                higher_timeframes=cp.higher_timeframes,
                series=cp.series,
                last_primary=cp.last_primary,
                last_higher=dict(cp.last_higher),
                frame_count=cp.frame_count,
                retention_local_bound=cp.retention_local_bound,
                primary_candles=cp.primary_candles,
                higher_candles=dict(cp.higher_candles),
                published_setups=cp.published_setups,
                latest_signal_id=cp.latest_signal_id,
                schema_version=bad_version,  # type: ignore[arg-type]
            )


def test_identity_mismatch_fails_closed() -> None:
    cp = sample_checkpoint()
    other = LiveCheckpoint(
        configuration_identity="configuration:deadbeef",
        symbol=cp.symbol,
        primary_timeframe=cp.primary_timeframe,
        higher_timeframes=cp.higher_timeframes,
        series=cp.series,
        last_primary=cp.last_primary,
        last_higher=dict(cp.last_higher),
        frame_count=cp.frame_count,
        retention_local_bound=cp.retention_local_bound,
        primary_candles=cp.primary_candles,
        higher_candles=dict(cp.higher_candles),
        published_setups=cp.published_setups,
        latest_signal_id=cp.latest_signal_id,
    )
    with pytest.raises(LiveCheckpointError, match="configuration_identity"):
        verify_checkpoint_identity(other, cp)
    # Matching identity passes.
    verify_checkpoint_identity(cp, sample_checkpoint())


def test_wrong_symbol_or_timeframe_in_series_is_rejected_at_construction() -> None:
    cp = sample_checkpoint()
    bad_series = SeriesProvenance(
        "ETHUSDT", cp.primary_timeframe, cp.series.venue, cp.series.provider, cp.series.dataset_id
    )
    with pytest.raises(LiveCheckpointError):
        LiveCheckpoint(
            configuration_identity=cp.configuration_identity,
            symbol=cp.symbol,  # disagrees with bad_series
            primary_timeframe=cp.primary_timeframe,
            higher_timeframes=cp.higher_timeframes,
            series=bad_series,
            last_primary=cp.last_primary,
            last_higher=dict(cp.last_higher),
            frame_count=cp.frame_count,
            retention_local_bound=cp.retention_local_bound,
            primary_candles=cp.primary_candles,
            higher_candles=dict(cp.higher_candles),
            published_setups=cp.published_setups,
            latest_signal_id=cp.latest_signal_id,
        )


def test_memory_store_save_load_contains() -> None:
    store = MemoryCheckpointStore()
    key = live_checkpoint_key("BTCUSDT", "15m")
    assert store.contains(key) is False
    assert store.load(key) is None
    cp = sample_checkpoint()
    store.save(key, cp)
    assert store.contains(key) is True
    restored = store.load(key)
    assert restored == cp


def test_file_store_atomic_write_and_ignores_stale_partial(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    key = live_checkpoint_key("BTCUSDT", "15m")
    cp = sample_checkpoint()
    store.save(key, cp)
    # Exact destination exists; no leftover partial after a successful write.
    files = list(tmp_path.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".checkpoint.json")
    assert not any(p.name.endswith(".partial") for p in files)
    assert store.load(key) == cp

    # A stale .partial next to the checkpoint is never read as a checkpoint.
    partial = files[0].with_name(files[0].name + ".partial")
    partial.write_bytes(b'{"garbage": true}')
    assert store.load(key) == cp  # still the valid checkpoint

    # Replacing atomically still works with the stale partial present.
    store.save(key, cp)
    assert store.load(key) == cp


def test_file_store_failure_before_replace_leaves_previous_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FileCheckpointStore(tmp_path)
    key = live_checkpoint_key("BTCUSDT", "15m")
    cp = sample_checkpoint()
    store.save(key, cp)
    original = checkpoint_bytes(cp)

    import smcsignal.live.checkpoint as checkpoint_module

    def boom(src: str, dst: str) -> None:
        raise OSError("simulated crash before replace")

    monkeypatch.setattr(checkpoint_module.os, "replace", boom)
    other = sample_checkpoint()  # identical logical content is fine; save should fail
    with pytest.raises(OSError):
        store.save(key, other)
    # Destination unchanged; partial cleaned up.
    destination = tmp_path / (
        checkpoint_module._validated_key(key) + checkpoint_module._FILE_SUFFIX
    )
    assert destination.read_bytes() == original
    assert not any(p.name.endswith(".partial") for p in tmp_path.iterdir())


def test_corrupt_checkpoint_file_fails_closed(tmp_path: Path) -> None:
    store = FileCheckpointStore(tmp_path)
    key = live_checkpoint_key("BTCUSDT", "15m")
    cp = sample_checkpoint()
    store.save(key, cp)
    files = list(tmp_path.glob("*.checkpoint.json"))
    assert len(files) == 1
    files[0].write_bytes(b"not-json")
    with pytest.raises(LiveCheckpointError):
        store.load(key)


def test_key_safety_rejects_traversal_and_reserved() -> None:
    store = MemoryCheckpointStore()
    with pytest.raises(AnalysisInputError):
        store.save("../evil", sample_checkpoint())
    with pytest.raises(LiveCheckpointError):
        live_checkpoint_key("", "15m")
    with pytest.raises(LiveCheckpointError):
        live_checkpoint_key("BTCUSDT", " 15m")
