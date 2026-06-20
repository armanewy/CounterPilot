from __future__ import annotations

from pathlib import Path
from pprint import pprint
import shutil

from behavior_lab.core import HypothesisSpec
from behavior_lab.gym import WorldGym
from behavior_lab.research_api import ResearchAPI
from behavior_lab.worlds import make_world


run_dir = Path("runs/first-session")
# This is a deterministic example directory. Remove it so the one-shot hidden
# and prospective budgets are fresh each time the example is executed.
if run_dir.exists():
    shutil.rmtree(run_dir)
gym = WorldGym(run_dir, world=make_world("habit", seed=101))
gym.seed(240)

campaign_id = "first-session-v1"
api = ResearchAPI(gym, campaign_id=campaign_id)

print("\nSCHEMA")
pprint(api.inspect_schema())

print("\nTARGET")
pprint(api.describe_target())

print("\nAVAILABLE VARIABLES")
pprint(api.list_variables())

print("\nBASELINE MODEL ZOO")
zoo = api.fit_model_zoo()
for model in zoo:
    print(model.model_id, type(model).__name__, model.complexity, model.origin)

hypothesis = HypothesisSpec.formula(
    hypothesis_id="h_first_manual_v1",
    target_name=gym.target_name,
    terms=[
        "deadline_near",
        "public_commitment",
        "fatigue",
        "recent_context_switches",
        "explicit_first_step * indicator(ambiguity > 0.6)",
    ],
    falsification_conditions=[
        "Does not improve development log loss over the base-rate model",
        "Fails on the one-shot hidden block",
        "Fails after the exact model artifact is frozen",
    ],
)

api.submit_hypothesis(hypothesis)
fit = api.fit_hypothesis(hypothesis.hypothesis_id)
model_id = fit["model_id"]

print("\nFITTED PARAMETERS")
pprint(fit)

print("\nDEVELOPMENT EVALUATION")
pprint(api.evaluate_hypothesis(model_id, split="development"))

print("\nWORST DEVELOPMENT ERRORS")
pprint(api.inspect_residuals(model_id, limit=5))

print("\nPROPOSED DISCRIMINATING EXPERIMENT")
proposal = api.propose_experiment([model_id, zoo[0].model_id, zoo[-1].model_id])
pprint(proposal)

print("\nOFFLINE EXPERIMENT INGESTION")
pprint(api.run_offline_experiment(proposal, trials=12))
print("The new synthetic trials are staging data for this campaign.")

print("\nFREEZE SELECTED CANDIDATE BEFORE LOCKBOX EVALUATION")
freeze = api.freeze_candidate(model_id)
pprint(freeze)

print("\nONE-SHOT HIDDEN EVALUATION OF THE FROZEN ARTIFACT")
pprint(api.evaluate_hypothesis(model_id, split="hidden"))

print("\nCOLLECT GENUINELY NEW OBSERVATIONS")
gym.seed(30)
gym.ensure_split_manifest(campaign_id=campaign_id)
print("Prospective cases:", len(gym.prospective_rows_for_freeze(freeze["payload"]["freeze_id"], campaign_id)))

print("\nONE-SHOT PROSPECTIVE EVALUATION")
pprint(api.submit_frozen_candidate(model_id))

print("\nLEDGER")
print("Records:", len(gym.ledger.scan()))
print("Hash chain valid:", gym.ledger.verify_hash_chain())
