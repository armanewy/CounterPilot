from __future__ import annotations

LISTING_COLUMNS = [
    "listing_id",
    "seller_id",
    "category",
    "condition",
    "listing_price",
    "reference_price",
    "start_time",
    "end_time",
]

TURN_COLUMNS = [
    "thread_id",
    "listing_id",
    "buyer_id",
    "seller_id",
    "turn_index",
    "actor",
    "action",
    "amount",
    "status",
    "event_time",
]

FORBIDDEN_FUTURE_FIELDS = {
    "final_price",
    "agreement",
    "future_rounds",
    "final_status",
    "status",
    "status_id",
    "response_time",
    "ref_price4",
    "excluded_reference_price_ref_price4",
    "buyer_id_if_sold",
    "sold_by_best_offer",
    "bo_ck_yn",
    "item_price",
    "final_sale_price",
    "auto_accept_price",
    "auto_decline_price",
    "accept_price",
    "decline_price",
    "buyer_us_if_sold",
    "later_response_time",
    "total_rounds",
}

TASKS = {
    "seller_next_action": ["accept", "counter", "decline", "expire"],
    "buyer_response_to_counter": ["accept", "counter", "leave", "expire"],
    "agreement": ["0", "1"],
    "final_price_ratio": ["regression"],
    "response_latency": ["regression"],
}

TRANSFORMATION_VERSION = "nber_best_offer_normalization.v1"
