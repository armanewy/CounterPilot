from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any


class DataSourceError(ValueError):
    pass


@dataclass(frozen=True)
class DataSource:
    source_id: str
    name: str
    role: str
    license_status: str
    license_url: str | None
    source_url: str
    allowed_uses: dict[str, bool]
    evidence_class: str
    integration_requirements: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PermissionCheck:
    source_id: str
    use: str
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceRegistry:
    def __init__(self, sources: list[DataSource]) -> None:
        by_id: dict[str, DataSource] = {}
        for source in sources:
            if source.source_id in by_id:
                raise DataSourceError(f"Duplicate data source {source.source_id!r}")
            by_id[source.source_id] = source
        self._sources = by_id

    def list(self) -> list[dict[str, Any]]:
        return [source.to_dict() for source in sorted(self._sources.values(), key=lambda item: item.source_id)]

    def get(self, source_id: str) -> DataSource:
        try:
            return self._sources[source_id]
        except KeyError as exc:
            raise DataSourceError(f"Unknown data source {source_id!r}") from exc

    def inspect(self, source_id: str) -> dict[str, Any]:
        return self.get(source_id).to_dict()

    def permissions(self, source_id: str) -> dict[str, Any]:
        source = self.get(source_id)
        return {
            "source_id": source.source_id,
            "license_status": source.license_status,
            "allowed_uses": dict(sorted(source.allowed_uses.items())),
            "production_export_allowed": self.check(source_id, "production_export").allowed,
        }

    def check(self, source_id: str, use: str) -> PermissionCheck:
        source = self.get(source_id)
        allowed = bool(source.allowed_uses.get(use, False))
        commercial_uses = {"commercial_training", "production_inference", "production_export"}
        if allowed and (use not in commercial_uses or source.license_status == "confirmed"):
            return PermissionCheck(source_id, use, True, "use allowed by registered source policy")
        if allowed:
            return PermissionCheck(source_id, use, False, f"license status is {source.license_status!r}, not confirmed")
        return PermissionCheck(source_id, use, False, f"use {use!r} is not allowed for {source_id!r}")

    def verify_lineage(self, source_ids: list[str], requested_use: str) -> dict[str, Any]:
        if not source_ids:
            return {
                "requested_use": requested_use,
                "allowed": False,
                "checks": [],
                "reason": "lineage must contain at least one source dataset",
            }
        checks = [self.check(source_id, requested_use) for source_id in source_ids]
        return {
            "requested_use": requested_use,
            "allowed": all(check.allowed for check in checks),
            "checks": [check.to_dict() for check in checks],
        }

    def verify_manifest_file(self, path: str | Path) -> dict[str, Any]:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        source_ids = payload.get("source_dataset_ids")
        if not isinstance(source_ids, list) or not all(isinstance(item, str) for item in source_ids):
            raise DataSourceError("manifest must contain source_dataset_ids as a list of strings")
        if not source_ids:
            raise DataSourceError("manifest source_dataset_ids may not be empty")
        requested_use = str(payload.get("requested_use", "internal_benchmarking"))
        return self.verify_lineage(source_ids, requested_use)


def default_sources() -> list[DataSource]:
    research_only = {
        "research": True,
        "internal_benchmarking": True,
        "commercial_training": False,
        "production_inference": False,
        "production_export": False,
    }
    simulation_only = {
        "research": True,
        "internal_benchmarking": True,
        "commercial_training": False,
        "production_inference": False,
        "production_export": False,
    }
    authorized_commercial = {
        "research": True,
        "internal_benchmarking": True,
        "commercial_training": True,
        "production_inference": True,
        "production_export": True,
    }
    return [
        DataSource(
            source_id="nber_ebay_best_offer",
            name="NBER Best Offer Sequential Bargaining",
            role="direct_evidence",
            license_status="uncertain",
            license_url="https://www.nber.org/research/data/best-offer-sequential-bargaining",
            source_url="https://www.nber.org/research/data/best-offer-sequential-bargaining",
            allowed_uses=research_only,
            evidence_class="direct eBay bargaining behavior",
            integration_requirements=["explicit download", "dataset citation", "commercial-use legal review"],
            notes=["Use for leakage-safe research benchmarks, not production model export."],
        ),
        DataSource(
            source_id="open_bandit_dataset",
            name="Open Bandit Dataset",
            role="evaluator_validation",
            license_status="confirmed",
            license_url="https://github.com/st-tech/zr-obp",
            source_url="https://zr-obp.readthedocs.io/en/latest/about.html",
            allowed_uses=research_only,
            evidence_class="off-policy evaluation benchmark",
            integration_requirements=["logged propensities", "policy support checks"],
            notes=["Use to validate OPE estimators; do not transfer e-commerce embeddings into OfferLab."],
        ),
        DataSource(
            source_id="criteo_uplift",
            name="Criteo Uplift Prediction Dataset",
            role="causal_validation",
            license_status="confirmed",
            license_url="https://ailab.criteo.com/criteo-uplift-prediction-dataset/",
            source_url="https://ailab.criteo.com/criteo-uplift-prediction-dataset/",
            allowed_uses=research_only,
            evidence_class="randomized uplift benchmark",
            integration_requirements=["noncommercial restriction", "negative controls"],
            notes=["Use to validate heterogeneous treatment-effect machinery only."],
        ),
        DataSource(
            source_id="auctionnet",
            name="AuctionNet",
            role="simulation",
            license_status="confirmed",
            license_url="https://github.com/alimama-tech/AuctionNet",
            source_url="https://github.com/alimama-tech/AuctionNet",
            allowed_uses=simulation_only,
            evidence_class="strategic ad-auction simulation",
            integration_requirements=["optional dependency", "simulation labeling"],
            notes=["Do not use AuctionNet as evidence about real eBay buyers."],
        ),
        DataSource(
            source_id="craigslist_bargain",
            name="CraigslistBargain",
            role="language_extraction",
            license_status="uncertain",
            license_url="https://github.com/stanfordnlp/cocoa/tree/master/craigslistbargain",
            source_url="https://huggingface.co/datasets/stanfordnlp/craigslist_bargains",
            allowed_uses=research_only,
            evidence_class="crowdworker negotiation dialogue",
            integration_requirements=["dialogue-act parser only", "commercial-use legal review"],
            notes=["Useful for text extraction; not acceptance-rate calibration."],
        ),
        DataSource(
            source_id="current_ebay_authorized_data",
            name="Current authorized eBay seller data",
            role="commercial_calibration",
            license_status="confirmed",
            license_url="https://developer.ebay.com/develop/guides-v2/authorization",
            source_url="https://developer.ebay.com/",
            allowed_uses=authorized_commercial,
            evidence_class="authorized seller production data",
            integration_requirements=["OAuth consent", "official APIs only", "seller cost-basis import"],
            notes=["Only source class allowed for production OfferLab models in this registry."],
        ),
    ]


def default_registry() -> SourceRegistry:
    return SourceRegistry(default_sources())
