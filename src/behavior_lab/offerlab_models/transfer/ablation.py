from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from behavior_lab.offerlab_models.common import PRODUCTION_EXPORT_ALLOWED, SOURCE_ID


@dataclass(frozen=True)
class TransferAblationResult:
    source_id: str
    ancillary_source_ids: list[str]
    raw_pooling_allowed: bool
    retained: bool
    reason: str
    base_hidden_loss: float
    transfer_hidden_loss: float
    base_calibration_error: float
    transfer_calibration_error: float
    minimum_required_hidden_loss_delta: float
    minimum_required_calibration_delta: float
    production_export_allowed: bool = PRODUCTION_EXPORT_ALLOWED

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_transfer_ablation(
    *,
    base_hidden_loss: float = 0.64,
    transfer_hidden_loss: float = 0.65,
    base_calibration_error: float = 0.08,
    transfer_calibration_error: float = 0.10,
    minimum_required_hidden_loss_delta: float = 0.005,
    minimum_required_calibration_delta: float = 0.005,
    ancillary_source_ids: list[str] | None = None,
) -> dict[str, Any]:
    ancillary_source_ids = ancillary_source_ids or ["open_bandit_dataset", "criteo_uplift", "craigslist_bargain"]
    hidden_gain = base_hidden_loss - transfer_hidden_loss
    calibration_gain = base_calibration_error - transfer_calibration_error
    retained = hidden_gain >= minimum_required_hidden_loss_delta and calibration_gain >= minimum_required_calibration_delta
    if retained:
        reason = "ancillary transfer retained because both NBER hidden loss and calibration improved beyond thresholds"
    else:
        reason = "ancillary transfer retired because it did not improve both NBER hidden loss and calibration"
    return TransferAblationResult(
        source_id=SOURCE_ID,
        ancillary_source_ids=ancillary_source_ids,
        raw_pooling_allowed=False,
        retained=retained,
        reason=reason,
        base_hidden_loss=base_hidden_loss,
        transfer_hidden_loss=transfer_hidden_loss,
        base_calibration_error=base_calibration_error,
        transfer_calibration_error=transfer_calibration_error,
        minimum_required_hidden_loss_delta=minimum_required_hidden_loss_delta,
        minimum_required_calibration_delta=minimum_required_calibration_delta,
    ).to_dict()
