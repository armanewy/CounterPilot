from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from behavior_lab.money.contracts import FinancialDecisionContract
from behavior_lab.money.ledger import MoneyLedger


class MoneyStorage:
    """Small filesystem storage facade for contracts and the MoneyLedger."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.contracts_dir = self.root / "contracts"
        self.ledger_path = self.root / "money_ledger.jsonl"
        self.contracts_dir.mkdir(parents=True, exist_ok=True)

    @property
    def ledger(self) -> MoneyLedger:
        return MoneyLedger(str(self.ledger_path))

    def write_contract(self, contract: FinancialDecisionContract) -> Path:
        path = self.contracts_dir / f"{contract.contract_id}.json"
        _write_json(path, {**contract.to_dict(), "contract_hash": contract.contract_hash()})
        return path

    def read_contract(self, contract_id: str) -> FinancialDecisionContract:
        payload = json.loads((self.contracts_dir / f"{contract_id}.json").read_text(encoding="utf-8"))
        payload.pop("contract_hash", None)
        from behavior_lab.money.contracts import Action

        payload["available_actions"] = [Action(**action) for action in payload["available_actions"]]
        return FinancialDecisionContract(**payload)

    def list_contracts(self) -> list[dict[str, Any]]:
        output = []
        for path in sorted(self.contracts_dir.glob("*.json")):
            output.append(json.loads(path.read_text(encoding="utf-8")))
        return output


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, allow_nan=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
