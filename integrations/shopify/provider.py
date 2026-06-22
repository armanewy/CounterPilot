from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Protocol
from urllib import request


SHOPIFY_GRAPHQL_VERSION = "2026-04"


class ShopifyProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class DraftOrderRequest:
    store_domain: str
    transaction_id: str
    line_items: list[dict[str, Any]]
    currency: str
    negotiated_amount_minor: int
    shipping_cost_minor: int
    discount_minor: int
    reserve_inventory: bool = False


@dataclass(frozen=True)
class DraftOrderResult:
    draft_order_id: str
    invoice_url: str
    resource_ids: dict[str, str]


class ShopifyProvider(Protocol):
    def create_discounted_draft_order(self, payload: DraftOrderRequest) -> DraftOrderResult:
        ...


class GraphQLShopifyProvider:
    """Minimal live GraphQL provider.

    Tests use `DeterministicFakeShopifyProvider`; this class is for development
    stores with explicit credentials supplied by the caller. It does not log or
    persist tokens.
    """

    def __init__(self, *, access_token: str, api_version: str = SHOPIFY_GRAPHQL_VERSION):
        if not access_token.strip():
            raise ShopifyProviderError("access token is required")
        self._access_token = access_token
        self.api_version = api_version

    def create_discounted_draft_order(self, payload: DraftOrderRequest) -> DraftOrderResult:
        query = """
        mutation MarginPilotDraftOrderCreate($input: DraftOrderInput!) {
          draftOrderCreate(input: $input) {
            draftOrder {
              id
              invoiceUrl
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        variables = {"input": _draft_order_input(payload)}
        response = self._post_graphql(payload.store_domain, {"query": query, "variables": variables})
        result = response.get("data", {}).get("draftOrderCreate", {})
        errors = result.get("userErrors") or []
        if errors:
            raise ShopifyProviderError(f"draftOrderCreate returned user errors: {errors}")
        draft = result.get("draftOrder") or {}
        draft_id = str(draft.get("id") or "")
        invoice_url = str(draft.get("invoiceUrl") or "")
        if not draft_id or not invoice_url:
            raise ShopifyProviderError("draftOrderCreate did not return draft order id and invoice URL")
        return DraftOrderResult(
            draft_order_id=draft_id,
            invoice_url=invoice_url,
            resource_ids={"draft_order_gid": draft_id},
        )

    def _post_graphql(self, store_domain: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://{store_domain}/admin/api/{self.api_version}/graphql.json"
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Shopify-Access-Token": self._access_token,
            },
            method="POST",
        )
        with request.urlopen(req, timeout=30) as response:  # nosec: live provider only
            return json.loads(response.read().decode("utf-8"))


def _draft_order_input(payload: DraftOrderRequest) -> dict[str, Any]:
    line_items = []
    for item in payload.line_items:
        line_items.append(
            {
                "variantId": item["variant_gid"],
                "quantity": int(item.get("quantity", 1)),
                "appliedDiscount": {
                    "description": "MarginPilot negotiated price",
                    "value": round(payload.discount_minor / 100, 2),
                    "valueType": "FIXED_AMOUNT",
                },
            }
        )
    body: dict[str, Any] = {
        "lineItems": line_items,
        "note": f"MarginPilot transaction {payload.transaction_id}",
        "tags": ["marginpilot", payload.transaction_id],
    }
    if payload.reserve_inventory:
        body["reserveInventoryUntil"] = None
    return body
