"""Webhook routes for external chat-platform integrations."""

from __future__ import annotations

import json
import re
from typing import Any
from typing import Mapping
from xml.etree import ElementTree

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.responses import PlainTextResponse

from deepcode.api.platform_inbound_debug import PlatformInboundDebugEvent, PlatformInboundDebugStore
from deepcode.api.models import PlatformEventResponse
from deepcode.api.platform_bridge import process_platform_event
from deepcode.api.platform_delivery import send_platform_callback_if_configured
from deepcode.api.platform_security import (
    build_qq_callback_validation_signature,
    extract_qq_callback_validation_fields,
    resolve_qq_signing_secret,
    validate_platform_request_security,
)
from deepcode.config import apply_chat_bridge_runtime_overrides, get_settings
from deepcode.logging_config import get_logger

router = APIRouter()
logger = get_logger(__name__)

_XML_TAG_PATTERN = re.compile(r"<(\w+)>(.*?)</\1>", flags=re.DOTALL)
_MASKED_HEADER_TOKENS = ("authorization", "token", "secret", "signature", "cookie")
_MAX_DEBUG_TEXT_LENGTH = 4000


def _is_platform_allowed(platform: str, allowed: list[str]) -> bool:
    normalized = str(platform or "").strip().lower()
    return normalized in {item.strip().lower() for item in allowed if item.strip()}


def _strip_cdata(value: str) -> str:
    text = value.strip()
    if text.startswith("<![CDATA[") and text.endswith("]]>"):
        return text[9:-3]
    return text


def _parse_wechat_xml_payload(raw_body: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    text = raw_body.strip()
    if not text:
        return payload

    try:
        root = ElementTree.fromstring(text)
        for child in list(root):
            if child.tag:
                payload[child.tag] = (child.text or "").strip()
        return payload
    except ElementTree.ParseError:
        # Keep resilient parsing for minor upstream payload formatting issues.
        for match in _XML_TAG_PATTERN.finditer(text):
            payload[match.group(1)] = _strip_cdata(match.group(2))
        return payload


def _header(headers: Mapping[str, str], name: str) -> str:
    return str(headers.get(name) or headers.get(name.lower()) or "").strip()


def _looks_like_qq_napcat_payload(payload: dict[str, Any], headers: Mapping[str, str]) -> bool:
    post_type = str(payload.get("post_type") or "").strip()
    if post_type:
        return True

    message_type = str(payload.get("message_type") or "").strip()
    if message_type and any(key in payload for key in ("raw_message", "message", "group_id", "self_id")):
        return True

    if _header(headers, "X-Self-ID"):
        return True

    return False


def _sanitize_debug_text(value: Any) -> str:
    text = str(value or "")
    if len(text) <= _MAX_DEBUG_TEXT_LENGTH:
        return text
    return text[:_MAX_DEBUG_TEXT_LENGTH] + "...<truncated>"


def _mask_debug_headers(headers: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if any(token in lowered for token in _MASKED_HEADER_TOKENS):
            result[str(key)] = "***"
        else:
            result[str(key)] = _sanitize_debug_text(value)
    return result


def _log_inbound_debug(
    *,
    settings: Any,
    request: Request,
    platform: str,
    raw_body: bytes,
    response_status: int,
    response_body: str,
) -> None:
    if not bool(getattr(settings, "chat_bridge_inbound_debug", False)):
        return

    client_host = ""
    if request.client is not None:
        client_host = f"{request.client.host}:{request.client.port}"

    PlatformInboundDebugStore().write(
        PlatformInboundDebugEvent(
            platform=platform,
            method=str(request.method or "POST"),
            url=str(request.url),
            path=str(request.url.path),
            client=client_host,
            query={str(key): _sanitize_debug_text(value) for key, value in request.query_params.items()},
            headers=_mask_debug_headers(request.headers),
            request_body=_sanitize_debug_text(raw_body.decode("utf-8", errors="ignore")),
            response_status=int(response_status),
            response_body=_sanitize_debug_text(response_body),
        )
    )


async def _process_platform_event_async(platform: str, payload: dict[str, Any], settings: Any) -> None:
    try:
        result = await process_platform_event(
            platform,
            payload,
            settings=settings,
        )

        delivery_result = await send_platform_callback_if_configured(
            platform,
            settings=settings,
            bridge_result=result,
            payload=payload,
        )

        logger.info(
            "Asynchronous NapCat platform event processed",
            platform=platform,
            event_type=result.event_type,
            session_id=result.session_id,
            delivery_sent=bool(delivery_result.get("sent")),
        )

        if bool(getattr(settings, "chat_bridge_inbound_debug", False)):
            try:
                PlatformInboundDebugStore().write(
                    PlatformInboundDebugEvent(
                        platform=platform,
                        method="ASYNC",
                        url=f"async://platforms/{platform}/events",
                        path=f"/api/v1/platforms/{platform}/events",
                        client="background-task",
                        query={},
                        headers={},
                        request_body=_sanitize_debug_text(json.dumps(payload, ensure_ascii=False)),
                        response_status=200,
                        response_body=_sanitize_debug_text(
                            json.dumps(
                                {
                                    "event_type": result.event_type,
                                    "ok": result.ok,
                                    "session_id": result.session_id,
                                    "reply_text": result.reply_text,
                                    "delivery": delivery_result,
                                },
                                ensure_ascii=False,
                            )
                        ),
                    )
                )
            except Exception:
                logger.exception("Failed to write async inbound debug event", platform=platform)
    except Exception as exc:
        logger.exception(
            "Asynchronous NapCat platform event failed",
            platform=platform,
            error=str(exc),
        )
        if bool(getattr(settings, "chat_bridge_inbound_debug", False)):
            try:
                PlatformInboundDebugStore().write(
                    PlatformInboundDebugEvent(
                        platform=platform,
                        method="ASYNC",
                        url=f"async://platforms/{platform}/events",
                        path=f"/api/v1/platforms/{platform}/events",
                        client="background-task",
                        query={},
                        headers={},
                        request_body=_sanitize_debug_text(json.dumps(payload, ensure_ascii=False)),
                        response_status=500,
                        response_body=_sanitize_debug_text(json.dumps({"error": str(exc)}, ensure_ascii=False)),
                    )
                )
            except Exception:
                logger.exception("Failed to write async inbound debug error event", platform=platform)


@router.post("/{platform}/events", response_model=PlatformEventResponse, tags=["Platform Bridge"])
async def platform_event(
    platform: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_deepcode_bridge_token: str | None = Header(default=None),
) -> PlatformEventResponse:
    """Receive webhook events from chat software and route to DeepCode chat runtime."""
    settings = get_settings()
    apply_chat_bridge_runtime_overrides(settings)
    normalized_platform = str(platform or "").strip().lower()
    raw_body = await request.body()
    try:
        if not settings.chat_bridge_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Platform bridge is disabled",
            )

        if not bool(getattr(settings, "chat_bridge_inbound_enabled", True)):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Platform bridge inbound callbacks are disabled",
            )

        if not _is_platform_allowed(normalized_platform, settings.allowed_chat_bridge_platforms):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unsupported platform: {platform}",
            )

        expected_token = str(settings.chat_bridge_verify_token or "").strip()
        if expected_token and str(x_deepcode_bridge_token or "").strip() != expected_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid bridge token",
            )

        payload: dict[str, Any] = {}
        is_wechat_xml = normalized_platform == "wechat" and "xml" in request.headers.get("content-type", "").lower()
        if raw_body.strip() and is_wechat_xml:
            payload = _parse_wechat_xml_payload(raw_body.decode("utf-8", errors="ignore"))
            payload["_bridge_payload_protocol"] = "xml"
        elif raw_body.strip():
            try:
                parsed = json.loads(raw_body)
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid JSON body",
                ) from exc
            if not isinstance(parsed, dict):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="JSON payload must be an object",
                )
            payload = parsed
            if normalized_platform == "wechat":
                payload["_bridge_payload_protocol"] = "json"

        security_result = validate_platform_request_security(
            normalized_platform,
            payload=payload,
            raw_body=raw_body,
            headers=request.headers,
            query=request.query_params,
            settings=settings,
        )
        if not security_result.ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=security_result.reason,
            )

        if normalized_platform == "qq":
            qq_validation = extract_qq_callback_validation_fields(payload)
            if qq_validation is not None:
                plain_token, event_ts = qq_validation
                signing_secret = resolve_qq_signing_secret(settings)
                signature = build_qq_callback_validation_signature(
                    secret=signing_secret,
                    event_ts=event_ts,
                    plain_token=plain_token,
                )
                if not signature:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="QQ callback validation requires signing secret or bot app secret",
                    )

                response_content = {
                    "plain_token": plain_token,
                    "signature": signature,
                }
                _log_inbound_debug(
                    settings=settings,
                    request=request,
                    platform=normalized_platform,
                    raw_body=raw_body,
                    response_status=status.HTTP_200_OK,
                    response_body=json.dumps(response_content, ensure_ascii=False),
                )
                return JSONResponse(content=response_content)

        if normalized_platform == "qq" and _looks_like_qq_napcat_payload(payload, request.headers):
            background_tasks.add_task(_process_platform_event_async, normalized_platform, payload, settings)
            response = PlatformEventResponse(
                ok=True,
                event_type="message",
                platform=normalized_platform,
                platform_event_id=str(payload.get("event_id") or payload.get("id") or payload.get("message_id") or ""),
                message_kind=str(payload.get("message_type") or payload.get("post_type") or "napcat.message"),
                platform_response={
                    "delivery": {
                        "enabled": bool(settings.chat_bridge_callback_delivery_enabled),
                        "attempted": False,
                        "sent": False,
                        "async": True,
                        "mode": "napcat",
                    }
                },
            )
            _log_inbound_debug(
                settings=settings,
                request=request,
                platform=normalized_platform,
                raw_body=raw_body,
                response_status=status.HTTP_200_OK,
                response_body=response.model_dump_json(),
            )
            return response

        result = await process_platform_event(
            normalized_platform,
            payload,
            settings=settings,
        )

        delivery_result = await send_platform_callback_if_configured(
            normalized_platform,
            settings=settings,
            bridge_result=result,
            payload=payload,
        )
        response_payload = result.platform_response if isinstance(result.platform_response, dict) else {}
        response_payload["delivery"] = delivery_result
        result.platform_response = response_payload

        if normalized_platform == "wechat":
            response_payload = result.platform_response if isinstance(result.platform_response, dict) else {}
            reply_xml = response_payload.get("reply_xml")
            if isinstance(reply_xml, str) and reply_xml:
                _log_inbound_debug(
                    settings=settings,
                    request=request,
                    platform=normalized_platform,
                    raw_body=raw_body,
                    response_status=status.HTTP_200_OK,
                    response_body=reply_xml,
                )
                return PlainTextResponse(content=reply_xml, media_type="application/xml")

        response = PlatformEventResponse(
            ok=result.ok,
            event_type=result.event_type,
            platform=result.platform,
            session_id=result.session_id,
            external_user_id=result.external_user_id,
            channel_id=result.channel_id,
            mode=result.mode,
            plan_only=result.plan_only,
            platform_event_id=result.platform_event_id,
            message_kind=result.message_kind,
            reply_text=result.reply_text,
            challenge=result.challenge,
            platform_response=result.platform_response,
        )
        _log_inbound_debug(
            settings=settings,
            request=request,
            platform=normalized_platform,
            raw_body=raw_body,
            response_status=status.HTTP_200_OK,
            response_body=response.model_dump_json(),
        )
        return response
    except HTTPException as exc:
        _log_inbound_debug(
            settings=settings,
            request=request,
            platform=normalized_platform,
            raw_body=raw_body,
            response_status=int(exc.status_code),
            response_body=json.dumps({"detail": exc.detail}, ensure_ascii=False),
        )
        raise