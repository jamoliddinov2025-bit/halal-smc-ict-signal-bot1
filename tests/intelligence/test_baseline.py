"""Phase 20/21 baseline preservation at the Phase 22 boundary."""

from __future__ import annotations

from smcsignal.analysis.intelligence import analyze_report
from tests.intelligence.helpers import intelligence, report

EASY = intelligence(minimum_finalized_for_diagnosis=1, minimum_finalized_for_ranking=1)

# The Phase 21 golden robustness report identity is pinned in the Phase 21 suite
# (tests/robustness/test_reporting.py). Re-asserting it here guards the Phase 22
# boundary: Phase 22 must consume that report without changing it.
GOLDEN_ROBUSTNESS_REPORT_ID = (
    "robustness-report:4b30cc6f26d8a2b0260ae4ba086c4c58254d738282ff42b2e71fc6d18437b818"
)


def test_phase21_golden_robustness_report_is_unchanged() -> None:
    assert report().report_id == GOLDEN_ROBUSTNESS_REPORT_ID


def test_intelligence_consumption_does_not_mutate_the_phase21_report() -> None:
    source = report()
    before = source.report_id
    analyze_report(source, EASY)
    assert source.report_id == before


def test_phase20_validation_population_is_preserved() -> None:
    # The validation population Phase 22 describes is exactly Phase 21's rows,
    # counted once; no Phase 20 backtest fact is regenerated or changed.
    from smcsignal.analysis.backtest import run_backtest
    from tests.backtest.helpers import configuration, dataset

    result = run_backtest([dataset()], configuration())
    assert (
        result.backtest_id
        == "backtest:fdede9aebe06581a609e3c990b81fe0559bf44610798d708bfe0a575bad7638b"
    )
