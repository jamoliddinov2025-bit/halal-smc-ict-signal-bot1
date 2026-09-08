"""Integer sqs-v1 awards from already-published nested facts. No ML or look-ahead."""

from __future__ import annotations

from smcsignal.analysis.displacement.models import DisplacementSnapshot
from smcsignal.analysis.fvg.models import FVGSnapshot
from smcsignal.analysis.halal_filter.classification import AssetClassification
from smcsignal.analysis.halal_filter.models import HalalSnapshot
from smcsignal.analysis.liquidity.models import LiquiditySnapshot
from smcsignal.analysis.mtf.calculation import MTFDirection
from smcsignal.analysis.mtf.models import MTFSnapshot
from smcsignal.analysis.order_blocks.models import OrderBlockSnapshot
from smcsignal.analysis.ote.calculation import OTEClassification
from smcsignal.analysis.ote.models import OTESnapshot
from smcsignal.analysis.premium_discount.calculation import PDClassification
from smcsignal.analysis.premium_discount.models import PDSnapshot
from smcsignal.analysis.setup_quality.models import (
    WEIGHTS,
    ScoreBreakdown,
    ScoreComponent,
)

MTF_POINTS: dict[MTFDirection, int] = {
    MTFDirection.BULLISH: WEIGHTS[ScoreComponent.MTF],
    MTFDirection.BEARISH: WEIGHTS[ScoreComponent.MTF],
    MTFDirection.NEUTRAL: 8,
    MTFDirection.MIXED: 5,
    MTFDirection.INSUFFICIENT_CONTEXT: 0,
}
MTF_REASONS: dict[MTFDirection, str] = {
    MTFDirection.BULLISH: "mtf_directional",
    MTFDirection.BEARISH: "mtf_directional",
    MTFDirection.NEUTRAL: "mtf_neutral",
    MTFDirection.MIXED: "mtf_mixed",
    MTFDirection.INSUFFICIENT_CONTEXT: "mtf_insufficient",
}
PD_POINTS: dict[PDClassification, int] = {
    PDClassification.DISCOUNT: WEIGHTS[ScoreComponent.PREMIUM_DISCOUNT],
    PDClassification.EQUILIBRIUM: 5,
    PDClassification.PREMIUM: 3,
    PDClassification.OUTSIDE_RANGE: 1,
    PDClassification.INSUFFICIENT_CONTEXT: 0,
}
PD_REASONS: dict[PDClassification, str] = {
    PDClassification.DISCOUNT: "pd_discount",
    PDClassification.EQUILIBRIUM: "pd_equilibrium",
    PDClassification.PREMIUM: "pd_premium",
    PDClassification.OUTSIDE_RANGE: "pd_outside_range",
    PDClassification.INSUFFICIENT_CONTEXT: "pd_insufficient",
}
OTE_POINTS: dict[OTEClassification, int] = {
    OTEClassification.INSIDE_OTE: WEIGHTS[ScoreComponent.OTE],
    OTEClassification.BELOW_OTE: 3,
    OTEClassification.ABOVE_OTE: 3,
    OTEClassification.INSUFFICIENT_CONTEXT: 0,
}
OTE_REASONS: dict[OTEClassification, str] = {
    OTEClassification.INSIDE_OTE: "ote_inside",
    OTEClassification.BELOW_OTE: "ote_below",
    OTEClassification.ABOVE_OTE: "ote_above",
    OTEClassification.INSUFFICIENT_CONTEXT: "ote_insufficient",
}


def nested_mtf(frame: HalalSnapshot) -> MTFSnapshot:
    return frame.upstream


def nested_ote(frame: HalalSnapshot) -> OTESnapshot:
    return frame.upstream.upstream


def nested_pd(frame: HalalSnapshot) -> PDSnapshot:
    return frame.upstream.upstream.upstream


def nested_order_block(frame: HalalSnapshot) -> OrderBlockSnapshot:
    return nested_pd(frame).upstream


def nested_fvg(frame: HalalSnapshot) -> FVGSnapshot:
    return nested_order_block(frame).upstream


def nested_displacement(frame: HalalSnapshot) -> DisplacementSnapshot:
    return nested_fvg(frame).upstream


def nested_liquidity(frame: HalalSnapshot) -> LiquiditySnapshot:
    return nested_displacement(frame).liquidity


def _presence(items: tuple[object, ...], weight: int, present_reason: str) -> tuple[int, str]:
    if items:
        return weight, present_reason
    return 0, "missing_evidence"


def breakdown_for(frame: HalalSnapshot) -> ScoreBreakdown:
    """Map already-published nested facts to integer points. Do not rerun detectors."""
    if frame.classification is AssetClassification.HARAM:
        reason = "gated_haram"
    elif frame.classification is AssetClassification.UNKNOWN:
        reason = "gated_unknown"
    else:
        reason = None
    if reason is not None:
        zeros = {component.value: 0 for component in ScoreComponent}
        return ScoreBreakdown(**zeros, reasons=tuple(reason for _ in ScoreComponent))

    mtf_label = nested_mtf(frame).direction
    pd_label = nested_pd(frame).classification
    ote_label = nested_ote(frame).classification
    displacement_points, displacement_reason = _presence(
        nested_displacement(frame).events,
        WEIGHTS[ScoreComponent.DISPLACEMENT],
        "displacement_present",
    )
    sweep_points, sweep_reason = _presence(
        nested_liquidity(frame).sweeps,
        WEIGHTS[ScoreComponent.LIQUIDITY_SWEEP],
        "sweep_present",
    )
    fvg_points, fvg_reason = _presence(
        nested_fvg(frame).events,
        WEIGHTS[ScoreComponent.FVG],
        "fvg_present",
    )
    order_block_points, order_block_reason = _presence(
        nested_order_block(frame).events,
        WEIGHTS[ScoreComponent.ORDER_BLOCK],
        "order_block_present",
    )
    return ScoreBreakdown(
        halal=WEIGHTS[ScoreComponent.HALAL],
        mtf=MTF_POINTS[mtf_label],
        mss=0,
        displacement=displacement_points,
        liquidity_sweep=sweep_points,
        fvg=fvg_points,
        order_block=order_block_points,
        breaker_block=0,
        mitigation_block=0,
        premium_discount=PD_POINTS[pd_label],
        ote=OTE_POINTS[ote_label],
        reasons=(
            "halal_status",
            MTF_REASONS[mtf_label],
            "missing_evidence",
            displacement_reason,
            sweep_reason,
            fvg_reason,
            order_block_reason,
            "missing_evidence",
            "missing_evidence",
            PD_REASONS[pd_label],
            OTE_REASONS[ote_label],
        ),
    )
