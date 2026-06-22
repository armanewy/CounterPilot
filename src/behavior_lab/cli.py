from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from behavior_lab.bridge import (
    CAMPAIGN_001_ID,
    import_snapshot_file,
    prepare_snapshot_file,
    validate_snapshot_file,
    write_campaign_001_template,
)
from behavior_lab.benchmarks.contracts import validate_manifest_file
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
from behavior_lab.data_sources.registry import default_registry
from behavior_lab.datasets.auctionnet.strategy import compare_strategies
from behavior_lab.datasets.craigslist_bargain.parser import evaluate_parser
from behavior_lab.datasets.criteo_uplift.uplift import simple_uplift_report
from behavior_lab.datasets.nber_best_offer.acquire import fetch_codebook, fetch_full
from behavior_lab.datasets.nber_best_offer.audit import audit as nber_audit
from behavior_lab.datasets.nber_best_offer.audit import benchmark as nber_benchmark
from behavior_lab.datasets.nber_best_offer.full_listing_pass import build_full_listing_restrictions, inspect_full_listing_restrictions
from behavior_lab.datasets.nber_best_offer.inventory import inventory_path
from behavior_lab.datasets.nber_best_offer.normalize import build_sample_dataset, normalize_dataset
from behavior_lab.datasets.nber_best_offer.real_normalize import finalize_full_release_evidence, full_normalization_status, inspect_real_source_schema, normalize_real_dataset
from behavior_lab.datasets.nber_best_offer.replication import replication_check, validate_replication_targets
from behavior_lab.datasets.nber_best_offer.source_inventory import inventory_official_sources, public_summary, run_source_inventory
from behavior_lab.datasets.nber_best_offer.source_schema import inspect_schema
from behavior_lab.datasets.open_bandit.ope import evaluate_policy
from behavior_lab.marginpilot import (
    DEFAULT_DATA_DIR as MARGINPILOT_DEFAULT_DATA_DIR,
    ingest_marginpilot_events,
    marginpilot_audit,
    marginpilot_inbox,
    marginpilot_rule_simulation,
    marginpilot_shadow_recommend,
    marginpilot_utility_report,
    write_marginpilot_templates,
)
from behavior_lab.marginpilot_core import (
    DEFAULT_CORE_DATA_DIR as MARGINPILOT_CORE_DEFAULT_DATA_DIR,
    consent_grant as marginpilot_consent_grant,
    consent_revoke as marginpilot_consent_revoke,
    event_append as marginpilot_event_append,
    research_export as marginpilot_research_export,
    run_local_commerce_fixture as marginpilot_run_local_fixture,
    transaction_create as marginpilot_transaction_create,
    transaction_inspect as marginpilot_transaction_inspect,
)
from behavior_lab.offerlab import (
    ingest_offerlab_snapshots,
    profit_audit,
    profit_audit_report,
    recommend_offer_action,
    write_profit_audit_report,
    write_campaign_002_template,
    load_offerlab_snapshots,
)
from behavior_lab.offerlab_pilot import (
    audit_pilot,
    import_pilot,
    inspect_input as inspect_pilot_input,
    shadow_report_pilot,
    write_template as write_pilot_template,
)
from behavior_lab.offerlab_models import run_sample_research_suite
from behavior_lab.offerlab_models.benchmark_v1 import BenchmarkPaths, run_offerlab_benchmark_v1
from behavior_lab.offerlab_models.benchmark_v2 import BenchmarkV2Paths as BenchmarkV2BuildPaths
from behavior_lab.offerlab_models.benchmark_v2 import build_offerlab_benchmark_v2
from behavior_lab.offerlab_models.benchmark_v2_runner import BenchmarkV2Paths as BenchmarkV2RunnerPaths
from behavior_lab.offerlab_models.benchmark_v2_runner import run_offerlab_benchmark_v2
from behavior_lab.offerlab_models.benchmark_v2_integration import BenchmarkV2IntegrationPaths
from behavior_lab.offerlab_models.benchmark_v2_integration import run_offerlab_benchmark_v2_integration
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


def command_offerlab_report(args: argparse.Namespace) -> None:
    if args.output:
        _print_json(write_profit_audit_report(args.data_dir, args.output))
    else:
        _print_json(profit_audit_report(args.data_dir))


def command_offerlab_recommend(args: argparse.Namespace) -> None:
    snapshots = load_offerlab_snapshots(args.input)
    if len(snapshots) != 1:
        raise SystemExit("offerlab-recommend requires exactly one snapshot")
    config = None
    if args.config:
        config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    _print_json(recommend_offer_action(snapshots[0], data_dir=args.data_dir, config=config))


def command_marginpilot_template(args: argparse.Namespace) -> None:
    _print_json(write_marginpilot_templates(args.output_dir))


def command_marginpilot_ingest(args: argparse.Namespace) -> None:
    _print_json(asdict(ingest_marginpilot_events(args.input, data_dir=args.data_dir)))


def command_marginpilot_inbox(args: argparse.Namespace) -> None:
    _print_json(marginpilot_inbox(args.data_dir, merchant_id=args.merchant_id))


def command_marginpilot_audit(args: argparse.Namespace) -> None:
    _print_json(marginpilot_audit(args.data_dir, merchant_id=args.merchant_id))


def command_marginpilot_utility_report(args: argparse.Namespace) -> None:
    _print_json(marginpilot_utility_report(args.data_dir, merchant_id=args.merchant_id))


def command_marginpilot_rule_simulation(args: argparse.Namespace) -> None:
    rule: dict[str, Any] = {}
    if args.rule_file:
        rule = json.loads(Path(args.rule_file).read_text(encoding="utf-8"))
    elif args.rule:
        rule = json.loads(args.rule)
    _print_json(marginpilot_rule_simulation(args.data_dir, merchant_id=args.merchant_id, rule=rule))


def command_marginpilot_shadow_recommend(args: argparse.Namespace) -> None:
    config: dict[str, Any] = {}
    if args.config_file:
        config = json.loads(Path(args.config_file).read_text(encoding="utf-8"))
    elif args.config:
        config = json.loads(args.config)
    _print_json(
        marginpilot_shadow_recommend(
            args.data_dir,
            merchant_id=args.merchant_id,
            offer_id=args.offer_id,
            config=config,
            generated_at=args.generated_at,
            append=not args.no_append,
        )
    )


def command_marginpilot_transaction_create(args: argparse.Namespace) -> None:
    _print_json(marginpilot_transaction_create(data_dir=args.data_dir, input_path=args.input))


def command_marginpilot_event_append(args: argparse.Namespace) -> None:
    _print_json(marginpilot_event_append(data_dir=args.data_dir, input_path=args.input))


def command_marginpilot_transaction_inspect(args: argparse.Namespace) -> None:
    _print_json(
        marginpilot_transaction_inspect(
            data_dir=args.data_dir,
            merchant_namespace=args.merchant_namespace,
            transaction_id=args.transaction_id,
        )
    )


def command_marginpilot_consent_grant(args: argparse.Namespace) -> None:
    _print_json(
        marginpilot_consent_grant(
            data_dir=args.data_dir,
            merchant_id=args.merchant_id,
            store_id=args.store_id,
            purposes=args.purpose,
            consent_version=args.consent_version,
            policy_hash=args.policy_hash,
            granted_at=args.granted_at,
            cross_merchant_training=args.cross_merchant_training,
        )
    )


def command_marginpilot_consent_revoke(args: argparse.Namespace) -> None:
    _print_json(
        marginpilot_consent_revoke(
            data_dir=args.data_dir,
            merchant_id=args.merchant_id,
            store_id=args.store_id,
            purpose=args.purpose,
            revoked_at=args.revoked_at,
        )
    )


def command_marginpilot_research_export(args: argparse.Namespace) -> None:
    _print_json(
        marginpilot_research_export(
            data_dir=args.data_dir,
            merchant_id=args.merchant_id,
            store_id=args.store_id,
            purpose=args.purpose,
            as_of=args.as_of,
        )
    )


def command_marginpilot_run_local_fixture(args: argparse.Namespace) -> None:
    _print_json(marginpilot_run_local_fixture(data_dir=args.data_dir))


def command_offerlab_pilot_template(args: argparse.Namespace) -> None:
    _print_json(write_pilot_template(args.output_dir))


def command_offerlab_pilot_inspect(args: argparse.Namespace) -> None:
    _print_json(inspect_pilot_input(args.input_dir))


def command_offerlab_pilot_import(args: argparse.Namespace) -> None:
    _print_json(asdict(import_pilot(args.input_dir, data_root=args.data_root, pilot_id=args.pilot_id)))


def command_offerlab_pilot_audit(args: argparse.Namespace) -> None:
    _print_json(audit_pilot(args.pilot_id, data_root=args.data_root))


def command_offerlab_pilot_shadow_report(args: argparse.Namespace) -> None:
    _print_json(shadow_report_pilot(args.pilot_id, data_root=args.data_root, output_path=args.output))


def command_offerlab_models_sample(args: argparse.Namespace) -> None:
    _print_json(run_sample_research_suite())


def command_offerlab_models_benchmark_v1(args: argparse.Namespace) -> None:
    raise SystemExit(
        "OfferLab Benchmark v1 is frozen and hidden-spent. "
        "Do not rerun it; create Benchmark v2 with fresh hidden cases instead."
    )


def command_offerlab_models_benchmark_v2_build(args: argparse.Namespace) -> None:
    _print_json(
        build_offerlab_benchmark_v2(
            BenchmarkV2BuildPaths(
                normalized_dir=Path(args.normalized_dir),
                output_dir=Path(args.output_dir),
                protocol_path=Path(args.protocol),
                v1_final_manifest_path=Path(args.v1_final_manifest),
                external_v1_hidden_tokens_path=Path(args.external_v1_hidden_tokens) if args.external_v1_hidden_tokens else None,
            ),
            require_full_release=not args.allow_bounded_test_input,
            partition_rows=args.partition_rows,
        )
    )


def command_offerlab_models_benchmark_v2(args: argparse.Namespace) -> None:
    _print_json(
        run_offerlab_benchmark_v2(
            BenchmarkV2RunnerPaths(
                normalized_dir=Path(args.normalized_dir),
                output_path=Path(args.output),
                doc_path=Path(args.doc),
                model_cards_dir=Path(args.model_cards_dir),
                protocol_path=Path(args.protocol),
            ),
            batch_size=args.batch_size,
            allow_hidden_submission=args.submit_hidden,
        )
    )


def command_offerlab_models_benchmark_v2_integrate(args: argparse.Namespace) -> None:
    _print_json(
        run_offerlab_benchmark_v2_integration(
            BenchmarkV2IntegrationPaths(
                normalized_dir=Path(args.normalized_dir),
                benchmark_dir=Path(args.benchmark_dir),
                output_path=Path(args.output),
                preregistration_path=Path(args.preregistration),
                pre_hidden_output_path=Path(args.pre_hidden_output),
                doc_path=Path(args.doc),
                pre_hidden_doc_path=Path(args.pre_hidden_doc),
                model_cards_dir=Path(args.model_cards_dir),
                protocol_path=Path(args.protocol),
                v1_final_manifest_path=Path(args.v1_final_manifest),
                external_v1_hidden_tokens_path=Path(args.external_v1_hidden_tokens) if args.external_v1_hidden_tokens else None,
            ),
            batch_size=args.batch_size,
            partition_rows=args.partition_rows,
            allow_bounded_test_input=args.allow_bounded_test_input,
            submit_hidden=args.submit_hidden,
        )
    )


def command_data_source_list(args: argparse.Namespace) -> None:
    _print_json({"sources": default_registry().list()})


def command_data_source_inspect(args: argparse.Namespace) -> None:
    _print_json(default_registry().inspect(args.source_id))


def command_data_source_verify(args: argparse.Namespace) -> None:
    _print_json(default_registry().verify_lineage(args.source_id, args.use))


def command_data_source_permissions(args: argparse.Namespace) -> None:
    _print_json(default_registry().permissions(args.source_id))


def command_benchmark_validate_manifest(args: argparse.Namespace) -> None:
    _print_json(validate_manifest_file(args.input))


def command_nber_fetch(args: argparse.Namespace) -> None:
    if args.full:
        _print_json(fetch_full(cache_dir=args.cache_dir, url=args.url, explicit=True).to_dict())
    else:
        _print_json(fetch_codebook(cache_dir=args.cache_dir).to_dict())


def command_nber_inventory(args: argparse.Namespace) -> None:
    _print_json(inventory_path(args.input))


def command_nber_source_inventory(args: argparse.Namespace) -> None:
    raw_dir = args.raw_dir or str(Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "raw" / "nber_best_offer")
    if args.write_report:
        manifest = run_source_inventory(
            raw_dir=raw_dir,
            manifest_path=args.manifest,
            doc_path=args.doc,
            first_sample_rows=args.first_sample_rows,
            reservoir_rows=args.reservoir_rows,
            chronological_rows_per_slice=args.chronological_rows_per_slice,
            timeout_seconds=args.timeout_seconds,
            download=args.download,
        )
        _print_json(public_summary(manifest))
    else:
        _print_json(
            inventory_official_sources(
                raw_dir=raw_dir,
                download=args.download,
                sample_dir=args.sample_dir,
                reservoir_size=args.reservoir_rows,
            )
        )


def command_nber_build_sample(args: argparse.Namespace) -> None:
    _print_json(build_sample_dataset(args.output_dir))


def command_nber_normalize(args: argparse.Namespace) -> None:
    _print_json(normalize_dataset(args.input_dir, args.output_dir))


def command_nber_inspect_schema(args: argparse.Namespace) -> None:
    if args.raw_dir:
        _print_json(inspect_real_source_schema(args.raw_dir))
    else:
        _print_json(inspect_schema(codebook_path=args.codebook))


def command_nber_normalize_real(args: argparse.Namespace) -> None:
    raw_dir = args.raw_dir or str(Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "raw" / "nber_best_offer")
    output_dir = args.output_dir or str(Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "processed" / "nber_best_offer_full")
    _print_json(
        normalize_real_dataset(
            raw_dir,
            output_dir,
            limit_threads=args.limit_threads,
            full=args.full,
            bucket_count=args.bucket_count,
            partition_rows=args.partition_rows,
            seed=args.seed,
            resume=args.resume,
            stop_after_thread_pass=args.stop_after_thread_pass,
        )
    )


def command_nber_full_status(args: argparse.Namespace) -> None:
    output_dir = args.output_dir or str(Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "processed" / "nber_best_offer_full")
    _print_json(full_normalization_status(output_dir))


def command_nber_build_full_listing_restrictions(args: argparse.Namespace) -> None:
    raw_dir = args.raw_dir or str(Path(os.environ.get("OFFERLAB_DATA_ROOT", r"C:\OfferLabData")) / "raw" / "nber_best_offer")
    _print_json(
        build_full_listing_restrictions(
            raw_dir,
            args.output_dir,
            partitions=args.partitions,
            resume=not args.no_resume,
            require_official_sources=args.require_official_sources,
        )
    )


def command_nber_inspect_full_listing_restrictions(args: argparse.Namespace) -> None:
    _print_json(inspect_full_listing_restrictions(args.output_dir))


def command_nber_replication_check(args: argparse.Namespace) -> None:
    if args.normalized_dir:
        _print_json(replication_check(args.normalized_dir, targets_path=args.targets))
    else:
        _print_json(validate_replication_targets(args.targets))


def command_nber_finalize_evidence(args: argparse.Namespace) -> None:
    _print_json(finalize_full_release_evidence(args.normalized_dir, independent_audit_artifact=args.independent_audit_artifact))


def command_nber_benchmark(args: argparse.Namespace) -> None:
    _print_json(nber_benchmark(args.normalized_dir))


def command_nber_audit(args: argparse.Namespace) -> None:
    _print_json(nber_audit(args.normalized_dir, output_path=args.output))


def command_benchmark_suite_permissions(args: argparse.Namespace) -> None:
    registry = default_registry()
    sources = ["nber_ebay_best_offer", "open_bandit_dataset", "criteo_uplift", "auctionnet", "craigslist_bargain"]
    _print_json({source: registry.permissions(source) for source in sources})


def command_benchmark_suite_run(args: argparse.Namespace) -> None:
    open_bandit_logs = [
        {"action": "a", "propensity": 0.5, "reward": 1.0},
        {"action": "b", "propensity": 0.5, "reward": 0.0},
        {"action": "a", "propensity": 0.5, "reward": 1.0},
        {"action": "b", "propensity": 0.5, "reward": 1.0},
    ]
    open_bandit = evaluate_policy(open_bandit_logs, lambda _row: {"a": 0.5, "b": 0.5})
    criteo = simple_uplift_report(
        [
            {"treatment": 0, "conversion": 0},
            {"treatment": 0, "conversion": 0},
            {"treatment": 1, "conversion": 1},
            {"treatment": 1, "conversion": 0},
        ]
    )
    craigslist = evaluate_parser(
        [
            {"text": "Would you take $80 if I pick up tonight?", "offer_amount": 80.0, "act": "propose"},
            {"text": "I can meet you at $95, final offer.", "offer_amount": 95.0, "act": "counter"},
            {"text": "Deal, I accept.", "offer_amount": None, "act": "accept"},
        ]
    )
    registry = default_registry()
    _print_json(
        {
            "DIRECT_EVIDENCE": {
                "source_id": "nber_ebay_best_offer",
                "status": "run nber-best-offer benchmark on normalized NBER data",
                "production_export_permission": registry.check("nber_ebay_best_offer", "production_export").to_dict(),
            },
            "EVALUATOR_VALIDATION": open_bandit,
            "CAUSAL_VALIDATION": criteo,
            "SIMULATION": compare_strategies(),
            "LANGUAGE_EXTRACTION": craigslist,
            "ARTIFACT_LINEAGE": {
                "production_export": registry.verify_lineage(
                    ["nber_ebay_best_offer", "open_bandit_dataset", "criteo_uplift", "auctionnet", "craigslist_bargain"],
                    "production_export",
                )
            },
        }
    )


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

    data_source = subparsers.add_parser("data-source", help="Inspect registered external dataset permissions")
    data_source_subparsers = data_source.add_subparsers(dest="data_source_command", required=True)

    data_source_list = data_source_subparsers.add_parser("list", help="List registered data sources")
    data_source_list.set_defaults(func=command_data_source_list)

    data_source_inspect = data_source_subparsers.add_parser("inspect", help="Inspect one registered data source")
    data_source_inspect.add_argument("source_id")
    data_source_inspect.set_defaults(func=command_data_source_inspect)

    data_source_verify = data_source_subparsers.add_parser("verify", help="Verify source lineage for a requested use")
    data_source_verify.add_argument("source_id", nargs="+")
    data_source_verify.add_argument("--use", default="production_export")
    data_source_verify.set_defaults(func=command_data_source_verify)

    data_source_permissions = data_source_subparsers.add_parser("permissions", help="Show allowed uses for one source")
    data_source_permissions.add_argument("source_id")
    data_source_permissions.set_defaults(func=command_data_source_permissions)

    benchmark_parser = subparsers.add_parser("benchmark", help="Federated benchmark utilities")
    benchmark_subparsers = benchmark_parser.add_subparsers(dest="benchmark_command", required=True)
    benchmark_manifest = benchmark_subparsers.add_parser("validate-manifest", help="Validate a benchmark manifest JSON file")
    benchmark_manifest.add_argument("--input", required=True)
    benchmark_manifest.set_defaults(func=command_benchmark_validate_manifest)

    nber = subparsers.add_parser("nber-best-offer", help="NBER eBay Best Offer benchmark tools")
    nber_subparsers = nber.add_subparsers(dest="nber_command", required=True)

    nber_fetch = nber_subparsers.add_parser("fetch", help="Record or explicitly download NBER Best Offer data")
    nber_fetch.add_argument("--cache-dir", default=".dataset_cache")
    nber_fetch.add_argument("--codebook", action="store_true", help="Record codebook/source discovery without full download")
    nber_fetch.add_argument("--full", action="store_true", help="Explicitly download a full official source file")
    nber_fetch.add_argument("--url", help="Official NBER file URL for --full")
    nber_fetch.set_defaults(func=command_nber_fetch)

    nber_inventory = nber_subparsers.add_parser("inventory", help="Inventory a CSV or CSV.GZ file")
    nber_inventory.add_argument("--input", required=True)
    nber_inventory.set_defaults(func=command_nber_inventory)

    nber_source_inventory = nber_subparsers.add_parser("source-inventory", help="Inventory official NBER source files without normalization")
    nber_source_inventory.add_argument("--raw-dir", default=None, help="Defaults to OFFERLAB_DATA_ROOT/raw/nber_best_offer")
    nber_source_inventory.add_argument("--download", action="store_true", help="Download missing official files before inventory")
    nber_source_inventory.add_argument("--sample-dir", help="Optional external directory for redacted samples")
    nber_source_inventory.add_argument("--reservoir-rows", type=_nonnegative, default=10_000)
    nber_source_inventory.add_argument("--write-report", action="store_true", help="Write committed metadata report paths; does not download unless --download is supplied")
    nber_source_inventory.add_argument("--manifest", default="datasets/manifests/nber_best_offer_downloads.yaml")
    nber_source_inventory.add_argument("--doc", default="docs/runs/NBER_SOURCE_INVENTORY.md")
    nber_source_inventory.add_argument("--first-sample-rows", type=_positive, default=100)
    nber_source_inventory.add_argument("--chronological-rows-per-slice", type=_positive, default=100)
    nber_source_inventory.add_argument("--timeout-seconds", type=_positive, default=120)
    nber_source_inventory.set_defaults(func=command_nber_source_inventory)

    nber_sample = nber_subparsers.add_parser("build-sample", help="Build a tiny deterministic NBER-format sample")
    nber_sample.add_argument("--output-dir", default="runs/nber_sample/raw")
    nber_sample.set_defaults(func=command_nber_build_sample)

    nber_normalize = nber_subparsers.add_parser("normalize", help="Normalize NBER CSV/CSV.GZ files into partitioned JSONL")
    nber_normalize.add_argument("--input-dir", required=True)
    nber_normalize.add_argument("--output-dir", required=True)
    nber_normalize.set_defaults(func=command_nber_normalize)

    nber_inspect_schema = nber_subparsers.add_parser("inspect-schema", help="Inspect official NBER real-source schema")
    nber_inspect_schema.add_argument("--codebook", help="Optional path to Codebook.xlsx")
    nber_inspect_schema.add_argument("--raw-dir", help="Optional raw directory to validate actual CSV headers")
    nber_inspect_schema.set_defaults(func=command_nber_inspect_schema)

    nber_normalize_real = nber_subparsers.add_parser("normalize-real", help="Normalize official NBER real source with thread-linked listing extraction")
    nber_normalize_real.add_argument("--raw-dir", default=None, help="Defaults to OFFERLAB_DATA_ROOT/raw/nber_best_offer")
    nber_normalize_real.add_argument("--output-dir", default=None, help="Defaults to OFFERLAB_DATA_ROOT/processed/nber_best_offer_full")
    nber_normalize_real.add_argument("--limit-threads", type=_positive)
    nber_normalize_real.add_argument("--full", action="store_true")
    nber_normalize_real.add_argument("--resume", action="store_true", help="Reuse verified partition checkpoints and completed output files")
    nber_normalize_real.add_argument("--bucket-count", type=_positive, default=32)
    nber_normalize_real.add_argument("--partition-rows", type=_positive, default=50_000)
    nber_normalize_real.add_argument("--seed", type=int, default=20240621)
    nber_normalize_real.add_argument("--stop-after-thread-pass", action="store_true", help=argparse.SUPPRESS)
    nber_normalize_real.set_defaults(func=command_nber_normalize_real)

    nber_full_status = nber_subparsers.add_parser("full-status", help="Report full NBER normalization progress, checkpoints, and manifest integrity")
    nber_full_status.add_argument("--output-dir", default=None, help="Defaults to OFFERLAB_DATA_ROOT/processed/nber_best_offer_full")
    nber_full_status.set_defaults(func=command_nber_full_status)

    nber_build_full_listing = nber_subparsers.add_parser(
        "build-full-listing-restrictions",
        help="Build the full source-listing L1/L2 restriction table",
    )
    nber_build_full_listing.add_argument("--raw-dir", default=None, help="Defaults to OFFERLAB_DATA_ROOT/raw/nber_best_offer")
    nber_build_full_listing.add_argument("--output-dir", default=None, help="Defaults to OFFERLAB_DATA_ROOT/processed/nber_best_offer_full/listing_restrictions")
    nber_build_full_listing.add_argument("--partitions", type=_positive, default=128)
    nber_build_full_listing.add_argument("--no-resume", action="store_true", help="Rebuild even when a verified manifest exists")
    nber_build_full_listing.add_argument("--require-official-sources", action="store_true", help="Require the pinned official listing source hash and byte count")
    nber_build_full_listing.set_defaults(func=command_nber_build_full_listing_restrictions)

    nber_inspect_full_listing = nber_subparsers.add_parser(
        "inspect-full-listing-restrictions",
        help="Verify the full source-listing L1/L2 restriction table",
    )
    nber_inspect_full_listing.add_argument("--output-dir", default=None, help="Defaults to OFFERLAB_DATA_ROOT/processed/nber_best_offer_full/listing_restrictions")
    nber_inspect_full_listing.set_defaults(func=command_nber_inspect_full_listing_restrictions)

    nber_replication = nber_subparsers.add_parser("replication-check", help="Validate or run the frozen NBER replication contract")
    nber_replication.add_argument("--normalized-dir", help="Run checks against a normalized real-source manifest")
    nber_replication.add_argument("--targets", help="Optional replication target manifest")
    nber_replication.set_defaults(func=command_nber_replication_check)

    nber_finalize = nber_subparsers.add_parser("finalize-evidence", help="Attach verified replication and independent audit artifacts to a full NBER manifest")
    nber_finalize.add_argument("--normalized-dir", required=True)
    nber_finalize.add_argument("--independent-audit-artifact", required=True)
    nber_finalize.set_defaults(func=command_nber_finalize_evidence)

    nber_bench = nber_subparsers.add_parser("benchmark", help="Run leakage-safe baseline leaderboards")
    nber_bench.add_argument("--normalized-dir", required=True)
    nber_bench.set_defaults(func=command_nber_benchmark)

    nber_audit_parser = nber_subparsers.add_parser("audit", help="Run NBER adversarial audit checks")
    nber_audit_parser.add_argument("--normalized-dir", required=True)
    nber_audit_parser.add_argument("--output")
    nber_audit_parser.set_defaults(func=command_nber_audit)

    suite = subparsers.add_parser("benchmark-suite", help="Run wider-net validation suite smoke checks")
    suite_subparsers = suite.add_subparsers(dest="suite_command", required=True)
    suite_run = suite_subparsers.add_parser("run", help="Run Open Bandit and Criteo smoke benchmarks")
    suite_run.set_defaults(func=command_benchmark_suite_run)
    suite_report = suite_subparsers.add_parser("report", help="Alias for run")
    suite_report.set_defaults(func=command_benchmark_suite_run)
    suite_permissions = suite_subparsers.add_parser("permissions", help="Show cross-dataset production-export permissions")
    suite_permissions.set_defaults(func=command_benchmark_suite_permissions)

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

    offer_report = subparsers.add_parser("offerlab-report", help="Write the read-only OfferLab profit-audit report")
    offer_report.add_argument("--data-dir", default="data/campaign_002_ebay_seller_offers")
    offer_report.add_argument("--output", help="Optional .md or .json report path")
    offer_report.set_defaults(func=command_offerlab_report)

    offer_recommend = subparsers.add_parser("offerlab-recommend", help="Read-only economic recommendation for one offer snapshot")
    offer_recommend.add_argument("--input", required=True)
    offer_recommend.add_argument("--data-dir", default=None)
    offer_recommend.add_argument("--config", help="Optional JSON economics config with fee, holding cost, and return risk")
    offer_recommend.set_defaults(func=command_offerlab_recommend)

    margin_template = subparsers.add_parser("marginpilot-template", help="Write MarginPilot transaction-surface event templates")
    margin_template.add_argument("--output-dir", default="campaigns/marginpilot/examples")
    margin_template.set_defaults(func=command_marginpilot_template)

    margin_ingest = subparsers.add_parser("marginpilot-ingest", help="Ingest local MarginPilot offer, consent, decision, and outcome events")
    margin_ingest.add_argument("--input", required=True)
    margin_ingest.add_argument("--data-dir", default=str(MARGINPILOT_DEFAULT_DATA_DIR))
    margin_ingest.set_defaults(func=command_marginpilot_ingest)

    margin_inbox = subparsers.add_parser("marginpilot-inbox", help="Show open MarginPilot offers with accounting-only action economics")
    margin_inbox.add_argument("--data-dir", default=str(MARGINPILOT_DEFAULT_DATA_DIR))
    margin_inbox.add_argument("--merchant-id")
    margin_inbox.set_defaults(func=command_marginpilot_inbox)

    margin_audit = subparsers.add_parser("marginpilot-audit", help="Audit MarginPilot mature margin, consent, and readiness gates")
    margin_audit.add_argument("--data-dir", default=str(MARGINPILOT_DEFAULT_DATA_DIR))
    margin_audit.add_argument("--merchant-id")
    margin_audit.set_defaults(func=command_marginpilot_audit)

    margin_utility = subparsers.add_parser("marginpilot-utility-report", help="Summarize offer funnel, mature margin, refunds, concessions, and unpaid accepted offers")
    margin_utility.add_argument("--data-dir", default=str(MARGINPILOT_DEFAULT_DATA_DIR))
    margin_utility.add_argument("--merchant-id")
    margin_utility.set_defaults(func=command_marginpilot_utility_report)

    margin_rule = subparsers.add_parser("marginpilot-rule-sim", help="Replay a fixed merchant rule against historical contexts without causal claims")
    margin_rule.add_argument("--data-dir", default=str(MARGINPILOT_DEFAULT_DATA_DIR))
    margin_rule.add_argument("--merchant-id")
    margin_rule.add_argument("--rule", help="JSON rule body")
    margin_rule.add_argument("--rule-file", help="Path to JSON rule body")
    margin_rule.set_defaults(func=command_marginpilot_rule_simulation)

    margin_shadow = subparsers.add_parser("marginpilot-shadow-recommend", help="Record a transparent shadow recommendation for one open offer")
    margin_shadow.add_argument("--data-dir", default=str(MARGINPILOT_DEFAULT_DATA_DIR))
    margin_shadow.add_argument("--merchant-id", required=True)
    margin_shadow.add_argument("--offer-id", required=True)
    margin_shadow.add_argument("--config", help="JSON shadow-policy config")
    margin_shadow.add_argument("--config-file", help="Path to JSON shadow-policy config")
    margin_shadow.add_argument("--generated-at")
    margin_shadow.add_argument("--no-append", action="store_true", help="Preview without appending the shadow recommendation event")
    margin_shadow.set_defaults(func=command_marginpilot_shadow_recommend)

    margin_tx_create = subparsers.add_parser("marginpilot-transaction-create", help="Create a local MarginPilot transaction from an offer_submitted event")
    margin_tx_create.add_argument("--data-dir", default=str(MARGINPILOT_CORE_DEFAULT_DATA_DIR))
    margin_tx_create.add_argument("--input", help="Optional transition event JSON; defaults to the local fixture offer")
    margin_tx_create.set_defaults(func=command_marginpilot_transaction_create)

    margin_event_append = subparsers.add_parser("marginpilot-event-append", help="Append a MarginPilot transaction transition event")
    margin_event_append.add_argument("--data-dir", default=str(MARGINPILOT_CORE_DEFAULT_DATA_DIR))
    margin_event_append.add_argument("--input", required=True)
    margin_event_append.set_defaults(func=command_marginpilot_event_append)

    margin_tx_inspect = subparsers.add_parser("marginpilot-transaction-inspect", help="Inspect a deterministic MarginPilot transaction state")
    margin_tx_inspect.add_argument("--data-dir", default=str(MARGINPILOT_CORE_DEFAULT_DATA_DIR))
    margin_tx_inspect.add_argument("--merchant-namespace", required=True)
    margin_tx_inspect.add_argument("--transaction-id", required=True)
    margin_tx_inspect.set_defaults(func=command_marginpilot_transaction_inspect)

    margin_consent_grant = subparsers.add_parser("marginpilot-consent-grant", help="Grant versioned purpose-specific MarginPilot ML consent")
    margin_consent_grant.add_argument("--data-dir", default=str(MARGINPILOT_CORE_DEFAULT_DATA_DIR))
    margin_consent_grant.add_argument("--merchant-id", required=True)
    margin_consent_grant.add_argument("--store-id", required=True)
    margin_consent_grant.add_argument("--purpose", action="append", help="Allowed purpose; may be repeated")
    margin_consent_grant.add_argument("--consent-version", default="marginpilot-ml-consent-v1")
    margin_consent_grant.add_argument("--policy-hash")
    margin_consent_grant.add_argument("--granted-at")
    margin_consent_grant.add_argument("--cross-merchant-training", action="store_true")
    margin_consent_grant.set_defaults(func=command_marginpilot_consent_grant)

    margin_consent_revoke = subparsers.add_parser("marginpilot-consent-revoke", help="Revoke one MarginPilot consent purpose")
    margin_consent_revoke.add_argument("--data-dir", default=str(MARGINPILOT_CORE_DEFAULT_DATA_DIR))
    margin_consent_revoke.add_argument("--merchant-id", required=True)
    margin_consent_revoke.add_argument("--store-id", required=True)
    margin_consent_revoke.add_argument("--purpose", required=True)
    margin_consent_revoke.add_argument("--revoked-at")
    margin_consent_revoke.set_defaults(func=command_marginpilot_consent_revoke)

    margin_research_export = subparsers.add_parser("marginpilot-research-export", help="Export consent-gated MarginPilot research rows")
    margin_research_export.add_argument("--data-dir", default=str(MARGINPILOT_CORE_DEFAULT_DATA_DIR))
    margin_research_export.add_argument("--merchant-id")
    margin_research_export.add_argument("--store-id")
    margin_research_export.add_argument("--purpose", default="merchant_specific_model_training")
    margin_research_export.add_argument("--as-of")
    margin_research_export.set_defaults(func=command_marginpilot_research_export)

    margin_fixture = subparsers.add_parser("marginpilot-run-local-fixture", help="Run the local MarginPilot commerce-loop fixture")
    margin_fixture.add_argument("--data-dir", default=str(MARGINPILOT_CORE_DEFAULT_DATA_DIR))
    margin_fixture.set_defaults(func=command_marginpilot_run_local_fixture)

    offer_pilot = subparsers.add_parser("offerlab-pilot", help="Local-only seller pilot import and audit kit")
    offer_pilot_subparsers = offer_pilot.add_subparsers(dest="offerlab_pilot_command", required=True)

    offer_pilot_template = offer_pilot_subparsers.add_parser("template", help="Write seller CSV templates and an explicit column manifest")
    offer_pilot_template.add_argument("--output-dir", help="Template output directory; defaults outside the repository")
    offer_pilot_template.set_defaults(func=command_offerlab_pilot_template)

    offer_pilot_inspect = offer_pilot_subparsers.add_parser("inspect", help="Inspect seller pilot files without importing them")
    offer_pilot_inspect.add_argument("input_dir", metavar="INPUT_DIR")
    offer_pilot_inspect.set_defaults(func=command_offerlab_pilot_inspect)

    offer_pilot_import = offer_pilot_subparsers.add_parser("import", help="Import seller pilot files into a local external ledger")
    offer_pilot_import.add_argument("input_dir", metavar="INPUT_DIR")
    offer_pilot_import.add_argument("--data-root", help="External seller pilot ledger root; defaults to C:\\OfferLabData\\seller_pilots")
    offer_pilot_import.add_argument("--pilot-id", help="Override pilot_id from the manifest")
    offer_pilot_import.set_defaults(func=command_offerlab_pilot_import)

    offer_pilot_audit = offer_pilot_subparsers.add_parser("audit", help="Audit the latest imported version for one seller pilot")
    offer_pilot_audit.add_argument("pilot_id", metavar="PILOT_ID")
    offer_pilot_audit.add_argument("--data-root", help="External seller pilot ledger root; defaults to C:\\OfferLabData\\seller_pilots")
    offer_pilot_audit.set_defaults(func=command_offerlab_pilot_audit)
    offer_pilot_shadow = offer_pilot_subparsers.add_parser("shadow-report", help="Build an isolated read-only seller-pilot shadow report")
    offer_pilot_shadow.add_argument("pilot_id", metavar="PILOT_ID")
    offer_pilot_shadow.add_argument("--data-root", help="External seller pilot ledger root; defaults to C:\\OfferLabData\\seller_pilots")
    offer_pilot_shadow.add_argument("--output", help="Optional JSON output path for the shadow report")
    offer_pilot_shadow.set_defaults(func=command_offerlab_pilot_shadow_report)

    offer_models = subparsers.add_parser("offerlab-models", help="Run research-only OfferLab model leaderboards")
    offer_models_subparsers = offer_models.add_subparsers(dest="offerlab_models_command", required=True)
    offer_models_sample = offer_models_subparsers.add_parser("sample", help="Run the deterministic NBER-format sample model suite")
    offer_models_sample.set_defaults(func=command_offerlab_models_sample)
    offer_models_benchmark = offer_models_subparsers.add_parser("benchmark-v1", help="Retired: Benchmark v1 is frozen and hidden-spent")
    offer_models_benchmark.add_argument("--normalized-dir", required=True)
    offer_models_benchmark.add_argument("--output", default="reports/offerlab_benchmark_v1.json")
    offer_models_benchmark.add_argument("--doc", default="docs/runs/OFFERLAB_BENCHMARK_V1_RESULTS.md")
    offer_models_benchmark.add_argument("--model-cards-dir", default="docs/model_cards/offerlab_benchmark_v1")
    offer_models_benchmark.add_argument("--protocol", default="datasets/manifests/offerlab_benchmark_v1.yaml")
    offer_models_benchmark.add_argument("--lockbox-store", required=True, help="External durable JSONL event store for one-shot hidden submissions")
    offer_models_benchmark.add_argument("--row-cap", type=_positive, default=500)
    offer_models_benchmark.add_argument("--seed", type=int, default=20240621)
    offer_models_benchmark.set_defaults(func=command_offerlab_models_benchmark_v1)
    offer_models_benchmark_v2 = offer_models_subparsers.add_parser("benchmark-v2-build", help="Build Benchmark v2 tasks and split manifests without training")
    offer_models_benchmark_v2.add_argument("--normalized-dir", required=True)
    offer_models_benchmark_v2.add_argument("--output-dir", required=True)
    offer_models_benchmark_v2.add_argument("--protocol", default="datasets/manifests/offerlab_benchmark_v2.yaml")
    offer_models_benchmark_v2.add_argument("--v1-final-manifest", default="reports/offerlab_benchmark_v1_final_manifest.json")
    offer_models_benchmark_v2.add_argument("--external-v1-hidden-tokens", required=True)
    offer_models_benchmark_v2.add_argument("--partition-rows", type=_positive, default=50_000)
    offer_models_benchmark_v2.add_argument("--allow-bounded-test-input", action="store_true", help=argparse.SUPPRESS)
    offer_models_benchmark_v2.set_defaults(func=command_offerlab_models_benchmark_v2_build)
    offer_models_benchmark_v2_runner = offer_models_subparsers.add_parser("benchmark-v2", help="Run Benchmark v2 pre-hidden development model runner")
    offer_models_benchmark_v2_runner.add_argument("--normalized-dir", required=True)
    offer_models_benchmark_v2_runner.add_argument("--output", default="reports/offerlab_benchmark_v2_pre_hidden.json")
    offer_models_benchmark_v2_runner.add_argument("--doc", default="docs/runs/OFFERLAB_BENCHMARK_V2_PRE_HIDDEN.md")
    offer_models_benchmark_v2_runner.add_argument("--model-cards-dir", default="docs/model_cards/offerlab_benchmark_v2")
    offer_models_benchmark_v2_runner.add_argument("--protocol", default="datasets/manifests/offerlab_benchmark_v2.yaml")
    offer_models_benchmark_v2_runner.add_argument("--batch-size", type=_positive, default=10_000)
    offer_models_benchmark_v2_runner.add_argument("--submit-hidden", action="store_true")
    offer_models_benchmark_v2_runner.set_defaults(func=command_offerlab_models_benchmark_v2)
    offer_models_benchmark_v2_integrate = offer_models_subparsers.add_parser("benchmark-v2-integrate", help="Run the Benchmark v2 integration gate and preregistration artifact")
    offer_models_benchmark_v2_integrate.add_argument("--normalized-dir", required=True)
    offer_models_benchmark_v2_integrate.add_argument("--benchmark-dir", required=True)
    offer_models_benchmark_v2_integrate.add_argument("--output", default="reports/offerlab_benchmark_v2.json")
    offer_models_benchmark_v2_integrate.add_argument("--preregistration", default="reports/offerlab_benchmark_v2_preregistration.json")
    offer_models_benchmark_v2_integrate.add_argument("--pre-hidden-output", default="reports/offerlab_benchmark_v2_pre_hidden.json")
    offer_models_benchmark_v2_integrate.add_argument("--doc", default="docs/runs/OFFERLAB_BENCHMARK_V2_INTEGRATION.md")
    offer_models_benchmark_v2_integrate.add_argument("--pre-hidden-doc", default="docs/runs/OFFERLAB_BENCHMARK_V2_PRE_HIDDEN.md")
    offer_models_benchmark_v2_integrate.add_argument("--model-cards-dir", default="docs/model_cards/offerlab_benchmark_v2")
    offer_models_benchmark_v2_integrate.add_argument("--protocol", default="datasets/manifests/offerlab_benchmark_v2.yaml")
    offer_models_benchmark_v2_integrate.add_argument("--v1-final-manifest", default="reports/offerlab_benchmark_v1_final_manifest.json")
    offer_models_benchmark_v2_integrate.add_argument("--external-v1-hidden-tokens")
    offer_models_benchmark_v2_integrate.add_argument("--partition-rows", type=_positive, default=50_000)
    offer_models_benchmark_v2_integrate.add_argument("--batch-size", type=_positive, default=10_000)
    offer_models_benchmark_v2_integrate.add_argument("--submit-hidden", action="store_true")
    offer_models_benchmark_v2_integrate.add_argument("--allow-bounded-test-input", action="store_true", help=argparse.SUPPRESS)
    offer_models_benchmark_v2_integrate.set_defaults(func=command_offerlab_models_benchmark_v2_integrate)

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
    raw_args = sys.argv[1:] if argv is None else list(argv)
    if len(raw_args) >= 2 and raw_args[0] == "offerlab-models" and raw_args[1] == "benchmark-v1":
        raise SystemExit(
            "OfferLab Benchmark v1 is frozen and hidden-spent. "
            "Do not rerun it; create Benchmark v2 with fresh hidden cases instead."
        )
    parser = build_parser()
    args = parser.parse_args(raw_args)
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
