from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
from typing import Any

from behavior_lab.discovery import DiscoveryLoop
from behavior_lab.evaluation import evaluate_model, paired_compare, pareto_frontier
from behavior_lab.gym import TARGET, WorldGym
from behavior_lab.ledger import ImmutableLedger
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
