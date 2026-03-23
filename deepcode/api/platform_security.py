"""Security validation helpers for chat-platform webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, Mapping

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except Exception:  # pragma: no cover - optional dependency fallback
    InvalidSignature = Exception
    Ed25519PrivateKey = None

from deepcode.config import Settings


@dataclass
class PlatformSecurityValidation:
    ok: bool
    reason: str = ""


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _header(headers: Mapping[str, str], name: str) -> str:
    return _safe_text(headers.get(name) or headers.get(name.lower()))


def _validate_feishu_signature(
    *,
    raw_body: bytes,
    headers: Mapping[str, str],
    settings: Settings,
) -> PlatformSecurityValidation:
    secret = _safe_text(settings.chat_bridge_feishu_encrypt_key)
    if not secret:
        return PlatformSecurityValidation(ok=True)

    timestamp = _header(headers, "X-Lark-Request-Timestamp")
    signature = _header(headers, "X-Lark-Signature")
    if not timestamp or not signature:
        return PlatformSecurityValidation(ok=False, reason="Missing Feishu signature headers")

    try:
        ts_value = int(timestamp)
    except ValueError:
        return PlatformSecurityValidation(ok=False, reason="Invalid Feishu timestamp")

    ttl_seconds = max(int(settings.chat_bridge_signature_ttl_seconds), 1)
    if abs(int(time.time()) - ts_value) > ttl_seconds:
        return PlatformSecurityValidation(ok=False, reason="Feishu signature timestamp expired")

    string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg=string_to_sign, digestmod=hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    if not hmac.compare_digest(signature, expected):
        return PlatformSecurityValidation(ok=False, reason="Invalid Feishu signature")

    return PlatformSecurityValidation(ok=True)


def _validate_wechat_signature(
    *,
    payload: dict[str, Any],
    query: Mapping[str, str],
    settings: Settings,
) -> PlatformSecurityValidation:
    token = _safe_text(settings.chat_bridge_wechat_token)
    if not token:
        return PlatformSecurityValidation(ok=True)

    signature = _safe_text(query.get("signature") or payload.get("signature") or payload.get("msg_signature"))
    timestamp = _safe_text(query.get("timestamp") or payload.get("timestamp"))
    nonce = _safe_text(query.get("nonce") or payload.get("nonce"))
    if not signature or not timestamp or not nonce:
        return PlatformSecurityValidation(ok=False, reason="Missing WeChat signature fields")

    expected = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode("utf-8")).hexdigest()
    if not hmac.compare_digest(signature.lower(), expected.lower()):
        return PlatformSecurityValidation(ok=False, reason="Invalid WeChat signature")

    return PlatformSecurityValidation(ok=True)


def _normalize_qq_signature(signature: str) -> str:
    lowered = signature.lower().strip()
    if lowered.startswith("sha256="):
        return lowered[len("sha256=") :].strip()
    return lowered


def _build_qq_seed_bytes(secret: str) -> bytes:
    seed = secret.encode("utf-8")
    if not seed:
        return b""
    while len(seed) < 32:
        seed += seed
    return seed[:32]


def _build_qq_private_key(secret: str):
    if Ed25519PrivateKey is None:
        return None
    seed = _build_qq_seed_bytes(secret)
    if len(seed) != 32:
        return None
    return Ed25519PrivateKey.from_private_bytes(seed)


def _validate_qq_legacy_hmac_signature(
    *,
    raw_body: bytes,
    payload: dict[str, Any],
    headers: Mapping[str, str],
    secret: str,
) -> PlatformSecurityValidation:
    signature = _safe_text(
        _header(headers, "X-Signature")
        or _header(headers, "X-QQ-Signature")
        or payload.get("signature")
    )
    if not signature:
        return PlatformSecurityValidation(ok=False, reason="Missing QQ signature")

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(_normalize_qq_signature(signature), expected.lower()):
        return PlatformSecurityValidation(ok=False, reason="Invalid QQ signature")

    return PlatformSecurityValidation(ok=True)


def _validate_qq_official_ed25519_signature(
    *,
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: str,
) -> PlatformSecurityValidation:
    signature_hex = _safe_text(_header(headers, "X-Signature-Ed25519"))
    timestamp = _safe_text(_header(headers, "X-Signature-Timestamp"))
    if not signature_hex or not timestamp:
        return PlatformSecurityValidation(ok=False, reason="Missing QQ Ed25519 signature headers")

    private_key = _build_qq_private_key(secret)
    if private_key is None:
        return PlatformSecurityValidation(ok=False, reason="QQ Ed25519 unavailable: cryptography is required")

    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return PlatformSecurityValidation(ok=False, reason="Invalid QQ Ed25519 signature encoding")

    try:
        private_key.public_key().verify(signature, timestamp.encode("utf-8") + raw_body)
    except InvalidSignature:
        return PlatformSecurityValidation(ok=False, reason="Invalid QQ Ed25519 signature")

    return PlatformSecurityValidation(ok=True)


def extract_qq_callback_validation_fields(payload: dict[str, Any]) -> tuple[str, str] | None:
    """Extract QQ webhook callback-url verification payload fields when op=13."""
    op = payload.get("op")
    op_text = _safe_text(op)
    if op != 13 and op_text != "13":
        return None

    data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
    plain_token = _safe_text(data.get("plain_token"))
    event_ts = _safe_text(data.get("event_ts"))
    if not plain_token or not event_ts:
        return None
    return plain_token, event_ts


def build_qq_callback_validation_signature(*, secret: str, event_ts: str, plain_token: str) -> str:
    """Build QQ callback-url verification signature for op=13 response body."""
    normalized_secret = _safe_text(secret)
    if not normalized_secret:
        return ""

    private_key = _build_qq_private_key(normalized_secret)
    if private_key is None:
        return ""

    signature = private_key.sign(f"{event_ts}{plain_token}".encode("utf-8"))
    return signature.hex()


def _is_qq_napcat_payload(payload: dict[str, Any], headers: Mapping[str, str]) -> bool:
    post_type = _safe_text(payload.get("post_type"))
    if post_type:
        return True

    message_type = _safe_text(payload.get("message_type"))
    if message_type and any(key in payload for key in ("raw_message", "message", "group_id", "self_id")):
        return True

    if _header(headers, "X-Self-ID"):
        return True

    return False


def _extract_bearer_token(headers: Mapping[str, str]) -> str:
    raw = _header(headers, "Authorization")
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("bearer "):
        return raw[7:].strip()
    return raw.strip()


def _validate_qq_napcat_token(
    *,
    payload: dict[str, Any],
    headers: Mapping[str, str],
    query: Mapping[str, str],
    settings: Settings,
) -> PlatformSecurityValidation:
    expected = _safe_text(settings.chat_bridge_qq_napcat_webhook_token)
    if not expected:
        return PlatformSecurityValidation(ok=True)

    provided = _safe_text(
        _extract_bearer_token(headers)
        or _header(headers, "X-NapCat-Token")
        or _header(headers, "X-OneBot-Token")
        or _header(headers, "X-QQ-Bot-Token")
        or query.get("access_token")
        or query.get("token")
        or query.get("secret")
        or payload.get("access_token")
        or payload.get("token")
    )
    if not provided:
        return PlatformSecurityValidation(ok=False, reason="Missing NapCat webhook token")

    if not hmac.compare_digest(provided, expected):
        return PlatformSecurityValidation(ok=False, reason="Invalid NapCat webhook token")

    return PlatformSecurityValidation(ok=True)


def _validate_qq_signature(
    *,
    raw_body: bytes,
    payload: dict[str, Any],
    headers: Mapping[str, str],
    query: Mapping[str, str],
    settings: Settings,
) -> PlatformSecurityValidation:
    if _is_qq_napcat_payload(payload, headers):
        napcat_result = _validate_qq_napcat_token(payload=payload, headers=headers, query=query, settings=settings)
        if not napcat_result.ok:
            return napcat_result
        return PlatformSecurityValidation(ok=True)

    secret = _safe_text(settings.chat_bridge_qq_signing_secret)
    if not secret:
        return PlatformSecurityValidation(ok=True)

    official_signature = _safe_text(_header(headers, "X-Signature-Ed25519"))
    official_timestamp = _safe_text(_header(headers, "X-Signature-Timestamp"))
    if official_signature or official_timestamp:
        return _validate_qq_official_ed25519_signature(raw_body=raw_body, headers=headers, secret=secret)

    return _validate_qq_legacy_hmac_signature(raw_body=raw_body, payload=payload, headers=headers, secret=secret)


def validate_platform_request_security(
    platform: str,
    *,
    payload: dict[str, Any],
    raw_body: bytes,
    headers: Mapping[str, str],
    query: Mapping[str, str],
    settings: Settings,
) -> PlatformSecurityValidation:
    """Validate platform-specific webhook signature if the platform secret is configured."""
    normalized = _safe_text(platform).lower()

    if normalized == "feishu":
        return _validate_feishu_signature(raw_body=raw_body, headers=headers, settings=settings)
    if normalized == "wechat":
        return _validate_wechat_signature(payload=payload, query=query, settings=settings)
    if normalized == "qq":
        return _validate_qq_signature(
            raw_body=raw_body,
            payload=payload,
            headers=headers,
            query=query,
            settings=settings,
        )

    return PlatformSecurityValidation(ok=True)