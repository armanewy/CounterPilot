from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from typing import Any

from behavior_lab.bridge import (
    CAMPAIGN_001_ID,
    import_snapshot_file,
    prepare_snapshot_file,
    validate_snapshot_file,
    write_campaign_001_template,
)
from behavior_lab.campaign001_collector import (
    DEFAULT_DATA_DIR,
    amend_capture,
    finalize_capture,
    invalidate_capture,
    load_script,
    missed_capture,
    resume_capture,
    start_capture,
    status_capture,
)
from behavior_lab.discovery import DiscoveryLoop
from behavior_lab.evaluation import evaluate_model, paired_compare, pareto_frontier
from behavior_lab.gym import TARGET, WorldGym
from behavior_lab.ledger import ImmutableLedger
from behavior_lab.offerlab import (
    ingest_offerlab_snapshots,
    profit_audit,
    recommend_offer_action,
    write_campaign_002_template,
    load_offerlab_snapshots,
)
from behavior_lab.research_api import ResearchAPI
from behavior_lab.runner import BatchConfig, SyntheticBatchRunner
from behavior_lab.stress import LabStressTester
from behavior_lab.worlds import make_world


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return number


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("value may not be negative")
    return number


def command_seed_world(args: argparse.Namespace) -> None:
    world = make_world(args.world, seed=args.seed)
    gym = WorldGym(args.data_dir, world=world)
    added = gym.seed(args.episodes)
    _print_json({"data_dir": str(Path(args.data_dir).resolve()), "world": world.name, "episodes_added": added})


def command_verify_ledger(args: argparse.Namespace) -> None:
    ledger = ImmutableLedger(Path(args.data_dir) / "ledger.jsonl")
    _print_json({"ledger": str(ledger.path.resolve()), "valid": ledger.verify_hash_chain(), "records": len(ledger.scan())})


def command_run_loop(args: argparse.Namespace) -> None:
    world = make_world(args.world, seed=args.seed)
    gym = WorldGym(args.data_dir, world=world)
    if not gym.decision_episodes():
        gym.seed(args.episodes)
    report = DiscoveryLoop(gym).run(
        iterations=args.iterations,
        offline_trials_per_iteration=args.offline_trials,
        prospective_episodes=args.prospective_episodes,
    )
    _print_json(report)


def command_demo(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    if args.reset and data_dir.exists():
        shutil.rmtree(data_dir)
    world = make_world(args.world, seed=args.seed)
    gym = WorldGym(data_dir, world=world)
    if not gym.decision_episodes():
        gym.seed(args.episodes)

    campaign_id = "demo_initial"
    api = ResearchAPI(gym, campaign_id=campaign_id)
    splits = gym.splits(campaign_id)
    models = api.fit_model_zoo()
    dev_metrics = [evaluate_model(model, splits["development"], split="development", include_details=True) for model in models]
    dev_metrics.sort(key=lambda item: item.log_loss)
    best_model = next(model for model in models if model.model_id == dev_metrics[0].model_id)
    pairwise = paired_compare(models[0], best_model, splits["development"])
    proposal = api.propose_experiment([model.model_id for model in models[:4]])
    experiment = api.run_offline_experiment(proposal, trials=args.offline_trials)

    loop_report = DiscoveryLoop(gym).run(
        iterations=args.iterations,
        offline_trials_per_iteration=args.offline_trials,
        prospective_episodes=args.prospective_episodes,
    )
    gym.ledger.verify_hash_chain()
    _print_json(
        {
            "data_dir": str(data_dir.resolve()),
            "world": world.name,
            "wave_1": {
                "campaign_id": campaign_id,
                "episodes": len(gym.decision_episodes()),
                "splits": {key: len(value) for key, value in splits.items()},
                "best_development": asdict(dev_metrics[0]),
            },
            "wave_2": {
                "models_fit": len(models),
                "pareto_frontier": pareto_frontier(dev_metrics),
                "paired_compare_base_vs_best": pairwise,
            },
            "wave_3": experiment,
            "wave_4": loop_report,
            "ledger_records": len(gym.ledger.scan()),
            "ledger_valid": True,
        }
    )


def command_stress_test(args: argparse.Namespace) -> None:
    tester = LabStressTester()
    data_dir = Path(args.data_dir)
    if args.matrix:
        _print_json(tester.run_world_matrix(data_dir, episodes=args.episodes, seed=args.seed))
    else:
        _print_json(tester.run(data_dir, episodes=args.episodes, seed=args.seed, world=args.world))


def command_batch_stress(args: argparse.Namespace) -> None:
    config = BatchConfig(
        worlds=_parse_csv_strings(args.worlds),
        seeds=_parse_csv_ints(args.seeds),
        episode_counts=_parse_csv_ints(args.episode_counts),
    )
    _print_json(SyntheticBatchRunner(args.data_dir).run(config))


def command_campaign_template(args: argparse.Namespace) -> None:
    template = write_campaign_001_template(args.output)
    _print_json({"output": str(Path(args.output).resolve()), "campaign_id": template["campaign_id"]})


def command_bridge_hash(args: argparse.Namespace) -> None:
    snapshots = prepare_snapshot_file(args.input, args.output)
    _print_json(
        {
            "input": str(Path(args.input).resolve()),
            "output": str(Path(args.output).resolve()),
            "snapshots": len(snapshots),
            "source_hashes": [snapshot["source_hash"] for snapshot in snapshots],
        }
    )


def command_bridge_validate(args: argparse.Namespace) -> None:
    _print_json(validate_snapshot_file(args.input, campaign_id=args.campaign_id))


def command_bridge_import(args: argparse.Namespace) -> None:
    result = import_snapshot_file(args.input, data_dir=args.data_dir, campaign_id=args.campaign_id)
    _print_json(asdict(result))


def command_campaign_001_capture_start(args: argparse.Namespace) -> None:
    phase = "pilot" if args.pilot else None
    _print_json(start_capture(args.data_dir, script=load_script(args.script), collection_phase=phase))


def command_campaign_001_capture_finalize(args: argparse.Namespace) -> None:
    _print_json(finalize_capture(args.episode_id, args.data_dir, script=load_script(args.script)))


def command_campaign_001_capture_resume(args: argparse.Namespace) -> None:
    _print_json(resume_capture(args.data_dir, episode_id=args.episode_id, script=load_script(args.script)))


def command_campaign_001_capture_missed(args: argparse.Namespace) -> None:
    phase = "pilot" if args.pilot else None
    _print_json(missed_capture(args.data_dir, script=load_script(args.script), collection_phase=phase))


def command_campaign_001_capture_status(args: argparse.Namespace) -> None:
    _print_json(status_capture(args.data_dir))


def command_campaign_001_capture_amend(args: argparse.Namespace) -> None:
    value: Any
    try:
        value = json.loads(args.value)
    except json.JSONDecodeError:
        value = args.value
    _print_json(amend_capture(args.episode_id, args.field, value, args.reason, args.data_dir))


def command_campaign_001_capture_invalidate(args: argparse.Namespace) -> None:
    _print_json(invalidate_capture(args.episode_id, args.reason, args.data_dir))


def command_offerlab_template(args: argparse.Namespace) -> None:
    template = write_campaign_002_template(args.output)
    _print_json({"output": str(Path(args.output).resolve()), "campaign_id": template["campaign_id"]})


def command_offerlab_ingest(args: argparse.Namespace) -> None:
    _print_json(asdict(ingest_offerlab_snapshots(args.input, data_dir=args.data_dir)))


def command_offerlab_audit(args: argparse.Namespace) -> None:
    _print_json(profit_audit(args.data_dir))


def command_offerlab_recommend(args: argparse.Namespace) -> None:
    snapshots = load_offerlab_snapshots(args.input)
    if len(snapshots) != 1:
        raise SystemExit("offerlab-recommend requires exactly one snapshot")
    config = None
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    _print_json(recommend_offer_action(snapshots[0], data_dir=args.data_dir, config=config))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Behavior Discovery Lab")
    subparsers = parser.add_subparsers(dest="command", required=True)

    seed = subparsers.add_parser("seed-world", help="Seed a hidden synthetic world into the ledger")
    seed.add_argument("--data-dir", default=".behavior_lab")
    seed.add_argument("--world", default="habit")
    seed.add_argument("--episodes", type=_positive, default=200)
    seed.add_argument("--seed", type=int, default=7)
    seed.set_defaults(func=command_seed_world)

    loop = subparsers.add_parser("run-loop", help="Run the campaign-safe offline discovery loop")
    loop.add_argument("--data-dir", default=".behavior_lab")
    loop.add_argument("--world", default="habit")
    loop.add_argument("--episodes", type=_positive, default=200)
    loop.add_argument("--iterations", type=_positive, default=3)
    loop.add_argument("--offline-trials", type=_positive, default=8)
    loop.add_argument("--prospective-episodes", type=_nonnegative, default=40)
    loop.add_argument("--seed", type=int, default=7)
    loop.set_defaults(func=command_run_loop)

    verify = subparsers.add_parser("verify-ledger", help="Verify the append-only hash chain")
    verify.add_argument("--data-dir", default=".behavior_lab")
    verify.set_defaults(func=command_verify_ledger)

    stress = subparsers.add_parser(
        "stress-test",
        help="Audit chronology, redaction, baselines, formula discovery, and intervention direction",
    )
    stress.add_argument("--data-dir", default=".stress_lab")
    stress.add_argument("--world", default="habit")
    stress.add_argument("--episodes", type=_positive, default=160)
    stress.add_argument("--seed", type=int, default=17)
    stress.add_argument("--matrix", action="store_true", help="Run the audit across all synthetic hidden worlds")
    stress.set_defaults(func=command_stress_test)

    batch = subparsers.add_parser("batch-stress", help="Run locked/idempotent synthetic stress batches")
    batch.add_argument("--data-dir", default="runs/batch")
    batch.add_argument("--worlds", default="habit,two_mode,threshold,nonstationary,confounded")
    batch.add_argument("--seeds", default="11,23,47,89,131")
    batch.add_argument("--episode-counts", default="100,300,1000")
    batch.set_defaults(func=command_batch_stress)

    template = subparsers.add_parser("campaign-001-template", help="Write a raw manual-entry template for Campaign 001")
    template.add_argument("--output", default="campaigns/campaign_001_task_initiation/manual_entry_template.json")
    template.set_defaults(func=command_campaign_template)

    bridge_hash = subparsers.add_parser("bridge-hash", help="Add source_hash values to raw Behavior Lab snapshot exports")
    bridge_hash.add_argument("--input", required=True)
    bridge_hash.add_argument("--output", required=True)
    bridge_hash.set_defaults(func=command_bridge_hash)

    bridge_validate = subparsers.add_parser("bridge-validate", help="Validate immutable Behavior Lab campaign snapshots")
    bridge_validate.add_argument("--input", required=True)
    bridge_validate.add_argument("--campaign-id", default=CAMPAIGN_001_ID)
    bridge_validate.set_defaults(func=command_bridge_validate)

    bridge_import = subparsers.add_parser("bridge-import", help="Import validated Behavior Lab snapshots into an append-only ledger")
    bridge_import.add_argument("--input", required=True)
    bridge_import.add_argument("--data-dir", required=True)
    bridge_import.add_argument("--campaign-id", default=CAMPAIGN_001_ID)
    bridge_import.set_defaults(func=command_bridge_import)

    capture = subparsers.add_parser("campaign-001-capture", help="Local Campaign 001 episode collector")
    capture_subparsers = capture.add_subparsers(dest="capture_command", required=True)

    capture_start = capture_subparsers.add_parser("start", help="Seal a pre-decision Campaign 001 episode")
    capture_start.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    capture_start.add_argument("--script", help="JSON object for deterministic/manual-free capture")
    capture_start.add_argument("--pilot", action="store_true", help="Mark this episode as part of the five-episode pilot")
    capture_start.set_defaults(func=command_campaign_001_capture_start)

    capture_finalize = capture_subparsers.add_parser("finalize", help="Finalize outcomes and import a bridge export")
    capture_finalize.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    capture_finalize.add_argument("--episode-id", required=True)
    capture_finalize.add_argument("--script", help="JSON object containing protected outcomes")
    capture_finalize.set_defaults(func=command_campaign_001_capture_finalize)

    capture_resume = capture_subparsers.add_parser("resume", help="List or finalize resumable local episodes")
    capture_resume.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    capture_resume.add_argument("--episode-id")
    capture_resume.add_argument("--script", help="JSON object containing protected outcomes")
    capture_resume.set_defaults(func=command_campaign_001_capture_resume)

    capture_missed = capture_subparsers.add_parser("missed", help="Record an eligible task missed before capture")
    capture_missed.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    capture_missed.add_argument("--script", help="JSON object describing the missed eligible task")
    capture_missed.add_argument("--pilot", action="store_true", help="Mark this missed episode as part of the pilot")
    capture_missed.set_defaults(func=command_campaign_001_capture_missed)

    capture_status = capture_subparsers.add_parser("status", help="Show operational collector status only")
    capture_status.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    capture_status.set_defaults(func=command_campaign_001_capture_status)

    capture_amend = capture_subparsers.add_parser("amend", help="Append a correction note without changing sealed data")
    capture_amend.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    capture_amend.add_argument("--episode-id", required=True)
    capture_amend.add_argument("--field", required=True)
    capture_amend.add_argument("--value", required=True)
    capture_amend.add_argument("--reason", required=True)
    capture_amend.set_defaults(func=command_campaign_001_capture_amend)

    capture_invalidate = capture_subparsers.add_parser("invalidate", help="Invalidate an unfinished local capture")
    capture_invalidate.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    capture_invalidate.add_argument("--episode-id", required=True)
    capture_invalidate.add_argument("--reason", required=True)
    capture_invalidate.set_defaults(func=command_campaign_001_capture_invalidate)

    offer_template = subparsers.add_parser("offerlab-template", help="Write a Campaign 002 eBay offer snapshot template")
    offer_template.add_argument("--output", default="campaigns/campaign_002_ebay_seller_offers/examples/offer_snapshot_template.json")
    offer_template.set_defaults(func=command_offerlab_template)

    offer_ingest = subparsers.add_parser("offerlab-ingest", help="Ingest normalized read-only eBay offer snapshots")
    offer_ingest.add_argument("--input", required=True)
    offer_ingest.add_argument("--data-dir", default="data/campaign_002_ebay_seller_offers")
    offer_ingest.set_defaults(func=command_offerlab_ingest)

    offer_audit = subparsers.add_parser("offerlab-audit", help="Summarize realized margin from ingested OfferLab history")
    offer_audit.add_argument("--data-dir", default="data/campaign_002_ebay_seller_offers")
    offer_audit.set_defaults(func=command_offerlab_audit)

    offer_recommend = subparsers.add_parser("offerlab-recommend", help="Read-only economic recommendation for one offer snapshot")
    offer_recommend.add_argument("--input", required=True)
    offer_recommend.add_argument("--data-dir", default=None)
    offer_recommend.add_argument("--config", help="Optional JSON economics config with fee, holding cost, and return risk")
    offer_recommend.set_defaults(func=command_offerlab_recommend)

    demo = subparsers.add_parser("demo", help="Run all waves end-to-end with campaign-safe lockboxes")
    demo.add_argument("--data-dir", default=".demo")
    demo.add_argument("--world", default="habit")
    demo.add_argument("--episodes", type=_positive, default=180)
    demo.add_argument("--iterations", type=_positive, default=3)
    demo.add_argument("--offline-trials", type=_positive, default=8)
    demo.add_argument("--prospective-episodes", type=_nonnegative, default=40)
    demo.add_argument("--seed", type=int, default=7)
    demo.add_argument("--reset", action=argparse.BooleanOptionalAction, default=True)
    demo.set_defaults(func=command_demo)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


def _parse_csv_strings(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("CSV list may not be empty")
    return items


def _parse_csv_ints(value: str) -> list[int]:
    items = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("CSV integer list may not be empty")
    return items


if __name__ == "__main__":
    main()
