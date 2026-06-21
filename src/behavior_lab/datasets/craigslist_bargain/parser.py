from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any


PRICE_RE = re.compile(r"(?:\$|usd\s*)?(\d+(?:\.\d{1,2})?)", re.IGNORECASE)


@dataclass(frozen=True)
class DialogueAct:
    text: str
    act: str
    offer_amount: float | None
    side_conditions: list[str]
    concession_language: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_utterance(text: str) -> DialogueAct:
    lowered = text.lower()
    amount = extract_offer(text)
    if any(token in lowered for token in ["accept", "deal", "sounds good", "you got it"]):
        act = "accept"
    elif any(token in lowered for token in ["no thanks", "reject", "can't do", "too low"]):
        act = "reject"
    elif any(token in lowered for token in ["bye", "nevermind", "walk away", "not interested"]):
        act = "quit"
    elif amount is not None and any(token in lowered for token in ["counter", "meet", "instead", "how about", "could do"]):
        act = "counter"
    elif amount is not None:
        act = "propose"
    else:
        act = "other"
    return DialogueAct(text, act, amount, side_conditions(text), concession_language(text))


def extract_offer(text: str) -> float | None:
    matches = [float(match.group(1)) for match in PRICE_RE.finditer(text)]
    if not matches:
        return None
    return matches[-1]


def side_conditions(text: str) -> list[str]:
    lowered = text.lower()
    conditions = []
    if any(token in lowered for token in ["deliver", "delivery", "ship", "shipping"]):
        conditions.append("delivery_or_shipping")
    if any(token in lowered for token in ["pickup", "pick up", "meet"]):
        conditions.append("pickup_or_meetup")
    if any(token in lowered for token in ["cash", "paypal", "venmo"]):
        conditions.append("payment_method")
    return conditions


def concession_language(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["lower", "come down", "meet you", "split", "best i can", "final offer"])


def reconstruct_price_sequence(dialogue: list[str]) -> list[float]:
    return [act.offer_amount for act in (parse_utterance(text) for text in dialogue) if act.offer_amount is not None]


def evaluate_parser(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "source_id": "craigslist_bargain",
            "evidence_role": "LANGUAGE_EXTRACTION",
            "production_export_allowed": False,
            "rows": 0,
            "offer_accuracy": 0.0,
            "act_accuracy": 0.0,
            "ambiguous_numbers": [],
        }
    offer_correct = 0
    act_correct = 0
    ambiguous = []
    for index, row in enumerate(rows):
        parsed = parse_utterance(str(row["text"]))
        if parsed.offer_amount == row.get("offer_amount"):
            offer_correct += 1
        if parsed.act == row.get("act"):
            act_correct += 1
        if len(PRICE_RE.findall(str(row["text"]))) > 1:
            ambiguous.append({"row": index, "text": row["text"], "parsed_offer": parsed.offer_amount})
    return {
        "source_id": "craigslist_bargain",
        "evidence_role": "LANGUAGE_EXTRACTION",
        "production_export_allowed": False,
        "rows": len(rows),
        "offer_accuracy": offer_correct / len(rows),
        "act_accuracy": act_correct / len(rows),
        "ambiguous_numbers": ambiguous,
    }
