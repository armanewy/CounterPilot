from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse


class BoundaryViolation(ValueError):
    """Raised when data crosses a MarginPilot storage boundary unsafely."""


class PIIScanError(BoundaryViolation):
    """Raised when a research-facing payload contains direct identifiers."""


@dataclass(frozen=True)
class PIIFinding:
    kind: str
    path: str


DIRECT_PII_KEYS = {
    "address",
    "billing_address",
    "buyer_email",
    "buyer_handle",
    "buyer_id",
    "buyer_name",
    "contact_email",
    "contact_id",
    "customer_email",
    "customer_gid",
    "customer_id",
    "customer_name",
    "email",
    "first_name",
    "full_name",
    "ip",
    "ip_address",
    "last_name",
    "name",
    "phone",
    "phone_number",
    "postal_address",
    "shipping_address",
    "shopify_customer_gid",
    "shopify_customer_id",
    "street_address",
    "user_id",
}
CONTEXT_TOKENS = {"buyer", "client", "contact", "customer", "person", "shopper", "user"}
DIRECT_IDENTIFIER_TOKENS = {"account", "gid", "handle", "id", "ids", "name"}
CONTACT_TOKENS = {"address", "email", "ip", "phone"}
TEXT_TOKENS = {"comment", "memo", "message", "note", "notes"}
OPERATIONAL_REFERENCE_TOKENS = {"checkout", "fulfillment", "payment", "shopify"}
PSEUDONYM_TOKENS = {"pseudo", "pseudonym", "pseudonymous", "hashed", "tokenized"}
HASH_TOKENS = {"hash", "hashes"}

VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "email address",
        re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    ),
    (
        "phone number",
        re.compile(r"(?<!\d)(?:\+?1[-.\s]?)?(?:\(\d{3}\)\s*|\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}(?!\d)"),
    ),
    (
        "ip address",
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ),
    (
        "postal address",
        re.compile(
            r"\b\d{1,6}\s+[A-Z0-9.'-]+(?:\s+[A-Z0-9.'-]+){0,5}\s+"
            r"(?:street|st|avenue|ave|road|rd|drive|dr|lane|ln|boulevard|blvd|way|court|ct|place|pl)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "shopify operational identifier",
        re.compile(r"\bgid://shopify/(?:customer|checkout|order|fulfillment|payment|cart)/", re.IGNORECASE),
    ),
)


class PIIScanner:
    """Rejects direct identifiers before data enters research-facing surfaces.

    Error messages intentionally avoid echoing the offending key or value. A
    field name can itself contain PII, and scanner failures can be written into
    logs or reports.
    """

    def scan(self, value: Any, *, label: str = "payload") -> None:
        finding = self.find(value)
        if finding is not None:
            raise PIIScanError(f"PII detected in {label} at {finding.path}: {finding.kind}")

    def find(self, value: Any) -> PIIFinding | None:
        return self._find(value, path=(), context_tokens=frozenset())

    def _find(self, value: Any, *, path: tuple[str, ...], context_tokens: frozenset[str]) -> PIIFinding | None:
        if isinstance(value, BaseException):
            return self._find_text(str(value), path, context_tokens=context_tokens)
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                key_tokens = _key_tokens(key_text)
                child_path = path + ("<key>",)
                if _is_pii_key(key_text, context_tokens=context_tokens):
                    return PIIFinding("field name resembles a direct identifier", _safe_path(child_path))
                if _is_pii_text(key_text) or _is_sensitive_url(key_text):
                    return PIIFinding("field name contains a direct identifier", _safe_path(child_path))
                child_context = frozenset(set(context_tokens) | key_tokens)
                finding = self._find(item, path=child_path, context_tokens=child_context)
                if finding is not None:
                    return finding
            return None
        if isinstance(value, (list, tuple, set, frozenset)):
            for index, item in enumerate(value):
                finding = self._find(item, path=path + (f"[{index}]",), context_tokens=context_tokens)
                if finding is not None:
                    return finding
            return None
        if isinstance(value, str):
            return self._find_text(value, path, context_tokens=context_tokens)
        return None

    def _find_text(self, text: str, path: tuple[str, ...], *, context_tokens: frozenset[str]) -> PIIFinding | None:
        if _is_sensitive_url(text):
            return PIIFinding("url is not allowed in research-facing payloads", _safe_path(path))
        if context_tokens & (PSEUDONYM_TOKENS | HASH_TOKENS) and _looks_like_internal_token(text):
            return None
        for kind, pattern in VALUE_PATTERNS:
            if pattern.search(text):
                return PIIFinding(kind, _safe_path(path))
        return None


def assert_no_pii(value: Any, *, label: str = "payload") -> None:
    PIIScanner().scan(value, label=label)


def _safe_path(path: Iterable[str]) -> str:
    rendered = "$"
    for part in path:
        if part.startswith("["):
            rendered += part
        else:
            rendered += f".{part}"
    return rendered


def _is_pii_key(key: str, *, context_tokens: frozenset[str]) -> bool:
    lower = key.lower()
    tokens = _key_tokens(lower)
    if tokens & PSEUDONYM_TOKENS:
        return False
    if lower in DIRECT_PII_KEYS:
        return True
    if tokens & CONTACT_TOKENS:
        return True
    if tokens & OPERATIONAL_REFERENCE_TOKENS and tokens & {"gid", "id", "ids", "url"}:
        return True
    all_context = set(context_tokens) | tokens
    if all_context & CONTEXT_TOKENS and tokens & DIRECT_IDENTIFIER_TOKENS:
        return True
    if all_context & CONTEXT_TOKENS and tokens & TEXT_TOKENS:
        return True
    if tokens & CONTEXT_TOKENS and tokens & (DIRECT_IDENTIFIER_TOKENS | CONTACT_TOKENS | TEXT_TOKENS):
        return True
    return False


def _key_tokens(text: str) -> set[str]:
    for separator in [".", "-", ":", "/", "\\", " "]:
        text = text.replace(separator, "_")
    return {token for token in text.split("_") if token}


def _is_pii_text(text: str) -> bool:
    return any(pattern.search(text) for _, pattern in VALUE_PATTERNS)


def _is_sensitive_url(text: str) -> bool:
    stripped = text.strip()
    parsed = urlparse(stripped)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return True
    if parsed.scheme.lower() == "gid" and parsed.netloc.lower() == "shopify":
        return True
    if not parsed.query:
        return False
    query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    return bool(query_keys & DIRECT_PII_KEYS)


def _looks_like_internal_token(text: str) -> bool:
    stripped = text.strip().lower()
    if re.fullmatch(r"mp_[a-z0-9_]+_[a-f0-9]{16,64}", stripped):
        return True
    return bool(re.fullmatch(r"[a-f0-9]{24,128}", stripped))
