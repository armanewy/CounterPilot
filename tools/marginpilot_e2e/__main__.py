from __future__ import annotations

import argparse
import json

from tools.marginpilot_e2e.runner import run_development_store_e2e


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic MarginPilot Shopify development-store E2E proof.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--report-path")
    parser.add_argument("--json", action="store_true", help="Print the full report JSON.")
    args = parser.parse_args()
    report = run_development_store_e2e(data_dir=args.data_dir, report_path=args.report_path)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps({"transaction_id": report["transaction_id"], "mature_state": report["events"]["mature_state"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
