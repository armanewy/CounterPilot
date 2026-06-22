from __future__ import annotations

import base64
import hmac
import hashlib
from typing import Mapping


class ShopifyWebhookError(ValueError):
    pass


def sign_webhook(raw_body: bytes, secret: bytes | str) -> str:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return base64.b64encode(hmac.new(key, raw_body, hashlib.sha256).digest()).decode("ascii")


def verify_webhook_hmac(raw_body: bytes, headers: Mapping[str, str], secret: bytes | str) -> str:
    observed = _header(headers, "X-Shopify-Hmac-Sha256")
    if not observed:
        raise ShopifyWebhookError("missing Shopify webhook HMAC")
    expected = sign_webhook(raw_body, secret)
    if not hmac.compare_digest(expected, observed):
        raise ShopifyWebhookError("invalid Shopify webhook HMAC")
    delivery_id = _header(headers, "X-Shopify-Webhook-Id") or _header(headers, "X-Shopify-Event-Id")
    if not delivery_id:
        raise ShopifyWebhookError("missing Shopify webhook delivery id")
    return delivery_id


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None
