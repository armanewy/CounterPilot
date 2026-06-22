from __future__ import annotations

from dataclasses import asdict

from integrations.shopify.provider import DraftOrderRequest, DraftOrderResult


class DeterministicFakeShopifyProvider:
    def __init__(self, *, store_domain: str = "marginpilot-dev-store.myshopify.com"):
        self.store_domain = store_domain
        self.created_draft_orders: list[dict] = []

    def create_discounted_draft_order(self, payload: DraftOrderRequest) -> DraftOrderResult:
        sequence = len(self.created_draft_orders) + 1
        draft_order_id = f"gid://shopify/DraftOrder/{1000 + sequence}"
        invoice_url = f"https://{self.store_domain}/invoices/marginpilot-{sequence}"
        result = DraftOrderResult(
            draft_order_id=draft_order_id,
            invoice_url=invoice_url,
            resource_ids={
                "draft_order_gid": draft_order_id,
                "checkout_gid": f"gid://shopify/Checkout/{2000 + sequence}",
            },
        )
        self.created_draft_orders.append({"request": asdict(payload), "result": asdict(result)})
        return result
