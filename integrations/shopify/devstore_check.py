from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Mapping
from urllib import request

from behavior_lab.core import stable_hash, utc_now
from behavior_lab.counterpilot_storage import assert_no_pii
from integrations.shopify.token_store import REQUIRED_DEVELOPMENT_SCOPES


DEFAULT_PROOF_ARTIFACT = Path("reports/counterpilot_dev_store_proof.json")
ENVIRONMENT_KEYS = {
    "app_url": "COUNTERPILOT_SHOPIFY_APP_URL",
    "access_token": "COUNTERPILOT_SHOPIFY_ACCESS_TOKEN",
    "merchant_id": "COUNTERPILOT_MERCHANT_ID",
    "scopes": "COUNTERPILOT_SHOPIFY_SCOPES",
    "store_domain": "COUNTERPILOT_SHOPIFY_STORE_DOMAIN",
    "store_id": "COUNTERPILOT_STORE_ID",
    "store_mode": "COUNTERPILOT_SHOPIFY_STORE_MODE",
    "webhook_secret": "COUNTERPILOT_SHOPIFY_WEBHOOK_SECRET",
    "webhook_url": "COUNTERPILOT_SHOPIFY_WEBHOOK_URL",
}
FORBIDDEN_PROOF_KEYS = {
    "access_token",
    "address",
    "buyer_message",
    "customer_email",
    "customer_name",
    "email",
    "phone",
    "raw_buyer_message",
    "refresh_token",
    "shipping_address",
    "token",
}


@dataclass(frozen=True)
class DevStoreCheck:
    name: str
    passed: bool
    detail: str

    def to_payload(self) -> dict[str, Any]:
        return {"check": self.name, "passed": self.passed, "detail": self.detail}


NetworkProbe = Callable[[str], tuple[bool, str]]


def counterpilot_devstore_check(
    *,
    env: Mapping[str, str] | None = None,
    data_dir: str | Path | None = None,
    execute_test_flow: bool = False,
    network_probe: NetworkProbe | None = None,
) -> dict[str, Any]:
    values = dict(env or os.environ)
    configured_data_dir = Path(data_dir or values.get("COUNTERPILOT_DATA_DIR") or r"C:\OfferLabData\counterpilot_devstore")
    checks: list[DevStoreCheck] = []
    for label, key in ENVIRONMENT_KEYS.items():
        value = values.get(key, "")
        checks.append(DevStoreCheck(f"env:{key}", bool(value.strip()), "set" if value.strip() else f"missing {label}"))

    access_token = values.get(ENVIRONMENT_KEYS["access_token"], "")
    store_domain = values.get(ENVIRONMENT_KEYS["store_domain"], "").strip().lower()
    store_mode = values.get(ENVIRONMENT_KEYS["store_mode"], "").strip().lower()
    scopes = _parse_scopes(values.get(ENVIRONMENT_KEYS["scopes"], ""))
    provider_mode = values.get("COUNTERPILOT_SHOPIFY_PROVIDER_MODE", "real").strip().lower()

    checks.extend(
        [
            DevStoreCheck("token_redaction", bool(access_token), "present but never included in output" if access_token else "missing token"),
            DevStoreCheck("store_mode_development", store_mode == "development", "development" if store_mode == "development" else "must be development"),
            DevStoreCheck("provider_mode_real", provider_mode != "fake", "fake provider disabled" if provider_mode != "fake" else "fake provider mode is not allowed for live proof"),
            DevStoreCheck("store_domain_shape", store_domain.endswith(".myshopify.com"), "myshopify domain" if store_domain.endswith(".myshopify.com") else "expected *.myshopify.com"),
            DevStoreCheck(
                "required_scopes",
                set(REQUIRED_DEVELOPMENT_SCOPES).issubset(scopes),
                "all required scopes present" if set(REQUIRED_DEVELOPMENT_SCOPES).issubset(scopes) else f"missing scopes: {sorted(set(REQUIRED_DEVELOPMENT_SCOPES) - scopes)}",
            ),
            DevStoreCheck("app_url_configured", _is_https(values.get(ENVIRONMENT_KEYS["app_url"], "")), "https app URL" if _is_https(values.get(ENVIRONMENT_KEYS["app_url"], "")) else "app URL must be https"),
            DevStoreCheck("webhook_url_configured", _is_https(values.get(ENVIRONMENT_KEYS["webhook_url"], "")), "https webhook URL" if _is_https(values.get(ENVIRONMENT_KEYS["webhook_url"], "")) else "webhook URL must be https"),
            DevStoreCheck("data_dir_writable", _is_writable(configured_data_dir), str(configured_data_dir)),
        ]
    )

    if store_domain:
        probe = network_probe or _default_network_probe
        reachable, detail = probe(store_domain)
        checks.append(DevStoreCheck("dev_store_reachable", reachable, detail))
    else:
        checks.append(DevStoreCheck("dev_store_reachable", False, "missing store domain"))

    checks.append(
        DevStoreCheck(
            "execute_test_flow",
            not execute_test_flow,
            "not requested; no Shopify mutations made" if not execute_test_flow else "not executed by checker; follow the manual proof runbook",
        )
    )

    payload = {
        "schema_version": "counterpilot_devstore_check.v1",
        "store_mode": store_mode or None,
        "store_domain_hash": stable_hash(store_domain) if store_domain else None,
        "merchant_namespace": _namespace(values),
        "data_dir": str(configured_data_dir),
        "checks": [check.to_payload() for check in checks],
        "ok": all(check.passed for check in checks),
        "mutations_performed": False,
        "token_printed": False,
    }
    assert_no_pii(payload)
    return payload


def write_redacted_devstore_proof_artifact(
    proof: dict[str, Any],
    *,
    output_path: str | Path = DEFAULT_PROOF_ARTIFACT,
    git_commit: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    _reject_forbidden_proof_fields(proof)
    report = proof.get("report") if isinstance(proof.get("report"), dict) else {}
    research_export = proof.get("research_export") if isinstance(proof.get("research_export"), dict) else {}
    artifact = {
        "schema_version": "counterpilot_dev_store_proof.v1",
        "app_version": str(proof.get("app_version") or "development"),
        "git_commit": git_commit or _git_commit(),
        "store_mode": "development",
        "timestamp": timestamp or utc_now(),
        "transaction_id": str(proof.get("transaction_id") or ""),
        "event_ids": [str(item) for item in proof.get("event_ids", [])],
        "state_transition_sequence": [str(item) for item in proof.get("state_transition_sequence", [])],
        "shopify_resource_hashes": _hash_resource_ids(proof.get("shopify_resource_ids", {})),
        "final_mature_margin_components": proof.get("final_mature_margin_components", {}),
        "report_hash": stable_hash(report),
        "research_export_hash": stable_hash(research_export),
        "pii_scan": proof.get("pii_scan", {"passed": True}),
        "manual_steps_completed": proof.get("manual_steps_completed", {}),
        "skipped_steps": proof.get("skipped_steps", []),
        "production_evidence": False,
    }
    assert_no_pii(artifact)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return artifact


def _parse_scopes(value: str) -> set[str]:
    return {scope.strip() for scope in value.replace(" ", ",").split(",") if scope.strip()}


def _is_https(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("https://") and len(text) > len("https://")


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".counterpilot_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _default_network_probe(store_domain: str) -> tuple[bool, str]:
    try:
        req = request.Request(f"https://{store_domain}", method="GET")
        with request.urlopen(req, timeout=10) as response:  # nosec: explicit dev-store reachability check
            status = getattr(response, "status", 0)
        return 200 <= int(status) < 500, f"HTTP {status}"
    except Exception as exc:  # pragma: no cover - live network path
        return False, f"unreachable: {exc.__class__.__name__}"


def _namespace(values: Mapping[str, str]) -> str | None:
    merchant_id = values.get(ENVIRONMENT_KEYS["merchant_id"], "").strip()
    store_id = values.get(ENVIRONMENT_KEYS["store_id"], "").strip()
    if not merchant_id or not store_id:
        return None
    return f"{merchant_id}:{store_id}"


def _hash_resource_ids(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): stable_hash(str(item)) for key, item in sorted(value.items())}


def _reject_forbidden_proof_fields(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            if key_text.lower() in FORBIDDEN_PROOF_KEYS:
                raise ValueError(f"proof artifact input contains forbidden field: {path + key_text}")
            _reject_forbidden_proof_fields(item, path=f"{path}{key_text}.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden_proof_fields(item, path=f"{path}{index}.")


def _git_commit() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], check=True, capture_output=True, text=True)
        return result.stdout.strip()
    except Exception:
        return "unknown"
