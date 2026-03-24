"""Outbound delivery helpers for platform official message APIs."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from deepcode.api.platform_bridge import PlatformBridgeResult
from deepcode.config import Settings

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_QQ_BOT_ACCESS_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_NAPCAT_DIRECT_SEGMENT_TYPES = {
    "text",
    "markdown",
    "face",
    "image",
    "record",
    "video",
    "at",
    "rps",
    "dice",
    "shake",
    "poke",
    "contact",
    "music",
    "reply",
    "forward",
    "node",
    "json",
    "mface",
    "file",
}
_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
}
_AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".ogg",
    ".amr",
    ".silk",
    ".flac",
    ".m4a",
    ".aac",
    ".opus",
    ".wma",
}
_VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
}
_MARKDOWN_LINK_PATTERN = re.compile(r"(!)?\[([^\]]*)\]\(([^)]+)\)")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _json_or_text(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {"text": response.text}
    return payload if isinstance(payload, dict) else {"value": payload}


def _extract_json_candidate(text: str) -> str:
    stripped = _safe_text(text)
    if not stripped.startswith("```"):
        return stripped

    parts = stripped.split("```")
    if len(parts) < 3:
        return stripped

    block = parts[1].strip()
    if "\n" not in block:
        return block

    first_line, rest = block.split("\n", 1)
    if first_line.strip().lower() in {"json", "json5"}:
        return rest.strip()
    return block


def _normalize_napcat_segment(segment: Any) -> dict[str, Any] | None:
    if not isinstance(segment, dict):
        return None

    seg_type = _safe_text(segment.get("type")).lower()
    if not seg_type:
        return None

    data = segment.get("data") if isinstance(segment.get("data"), dict) else {}

    if seg_type == "markdown":
        markdown_text = _safe_text(data.get("content") or data.get("text") or data.get("markdown"))
        if not markdown_text:
            return None
        return {
            "type": "markdown",
            "data": {"content": markdown_text},
        }

    if seg_type not in _NAPCAT_DIRECT_SEGMENT_TYPES:
        return None

    normalized_data: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized_data[str(key)] = value

    if seg_type == "text" and not _safe_text(normalized_data.get("text")):
        return None

    return {
        "type": seg_type,
        "data": normalized_data,
    }


def _parse_napcat_segments(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        rows = []
        for item in value:
            normalized = _normalize_napcat_segment(item)
            if normalized is not None:
                rows.append(normalized)
        return rows

    if isinstance(value, dict):
        if "type" in value:
            normalized = _normalize_napcat_segment(value)
            return [normalized] if normalized is not None else []
        nested = value.get("message")
        return _parse_napcat_segments(nested)

    if isinstance(value, str):
        candidate = _extract_json_candidate(value)
        if not candidate or candidate[0] not in "[{":
            return []
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return _parse_napcat_segments(parsed)

    return []


def _looks_like_image_target(target: str) -> bool:
    text = _safe_text(target)
    if not text:
        return False
    lowered = text.lower().split("?", 1)[0].split("#", 1)[0]
    return any(lowered.endswith(ext) for ext in _IMAGE_EXTENSIONS)


def _looks_like_audio_target(target: str) -> bool:
    text = _safe_text(target)
    if not text:
        return False
    lowered = text.lower().split("?", 1)[0].split("#", 1)[0]
    return any(lowered.endswith(ext) for ext in _AUDIO_EXTENSIONS)


def _looks_like_video_target(target: str) -> bool:
    text = _safe_text(target)
    if not text:
        return False
    lowered = text.lower().split("?", 1)[0].split("#", 1)[0]
    return any(lowered.endswith(ext) for ext in _VIDEO_EXTENSIONS)


def _normalize_napcat_media_file_value(target: str) -> str:
    text = _safe_text(target)
    if not text:
        return ""

    lowered = text.lower()
    if lowered.startswith("base64://"):
        return text

    if lowered.startswith("data:") and ";base64," in lowered:
        parts = text.split(",", 1)
        if len(parts) == 2 and parts[1]:
            return f"base64://{parts[1]}"

    return text


def _looks_like_image_data_target(target: str) -> bool:
    lowered = _safe_text(target).lower()
    return lowered.startswith("data:image/") or lowered.startswith("base64://")


def _looks_like_audio_data_target(target: str) -> bool:
    lowered = _safe_text(target).lower()
    return lowered.startswith("data:audio/")


def _looks_like_video_data_target(target: str) -> bool:
    lowered = _safe_text(target).lower()
    return lowered.startswith("data:video/")


def _markdown_text_to_napcat_segments(text: str) -> list[dict[str, Any]]:
    raw = _safe_text(text)
    if not raw:
        return []

    rows: list[dict[str, Any]] = []
    cursor = 0
    matched = False

    for item in _MARKDOWN_LINK_PATTERN.finditer(raw):
        matched = True
        start, end = item.span()
        prefix = raw[cursor:start]
        if prefix:
            rows.append({"type": "text", "data": {"text": prefix}})

        is_image = bool(item.group(1))
        label = _safe_text(item.group(2))
        target = _safe_text(item.group(3)).strip("<>")

        if not target:
            cursor = end
            continue

        media_file_value = _normalize_napcat_media_file_value(target)

        if _looks_like_audio_target(target) or _looks_like_audio_data_target(target):
            rows.append({"type": "record", "data": {"file": media_file_value}})
        elif _looks_like_video_target(target) or _looks_like_video_data_target(target):
            rows.append({"type": "video", "data": {"file": media_file_value}})
        elif is_image or _looks_like_image_target(target) or _looks_like_image_data_target(target):
            rows.append({"type": "image", "data": {"file": media_file_value}})
        else:
            file_name = label or Path(target.replace("\\", "/")).name or "attachment"
            rows.append({"type": "file", "data": {"name": file_name, "file": target}})

        cursor = end

    if not matched:
        return []

    suffix = raw[cursor:]
    if suffix:
        rows.append({"type": "text", "data": {"text": suffix}})

    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalize_napcat_segment(row)
        if normalized is not None:
            normalized_rows.append(normalized)
    return normalized_rows


def _resolve_napcat_outbound_message(bridge_result: PlatformBridgeResult) -> tuple[str | list[dict[str, Any]], str]:
    response_payload = bridge_result.platform_response if isinstance(bridge_result.platform_response, dict) else {}
    reply_payload = response_payload.get("reply") if isinstance(response_payload.get("reply"), dict) else {}

    for candidate in (
        reply_payload.get("message"),
        response_payload.get("reply_message"),
        bridge_result.reply_text,
    ):
        segments = _parse_napcat_segments(candidate)
        if segments:
            return segments, "segments"

    markdown_segments = _markdown_text_to_napcat_segments(bridge_result.reply_text)
    if markdown_segments:
        return markdown_segments, "segments(markdown)"

    return bridge_result.reply_text, "text"


def _cache_get_token(cache_key: str) -> str:
    row = _TOKEN_CACHE.get(cache_key)
    if row is None:
        return ""
    token, expires_at = row
    if time.time() >= max(expires_at - 30, 0):
        _TOKEN_CACHE.pop(cache_key, None)
        return ""
    return token


def _cache_set_token(cache_key: str, token: str, expires_in: int) -> None:
    _TOKEN_CACHE[cache_key] = (token, time.time() + max(int(expires_in), 60))


async def _fetch_feishu_tenant_access_token(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
) -> tuple[str, str]:
    app_id = _safe_text(settings.chat_bridge_feishu_app_id)
    app_secret = _safe_text(settings.chat_bridge_feishu_app_secret)
    if not app_id or not app_secret:
        return "", "Missing Feishu app credentials"

    cache_key = f"feishu:{app_id}"
    cached = _cache_get_token(cache_key)
    if cached:
        return cached, ""

    base_url = _safe_text(settings.chat_bridge_feishu_api_base_url) or "https://open.feishu.cn"
    response = await client.post(
        f"{base_url.rstrip('/')}/open-apis/auth/v3/tenant_access_token/internal",
        json={
            "app_id": app_id,
            "app_secret": app_secret,
        },
    )
    body = _json_or_text(response)
    if response.status_code >= 400:
        return "", f"Feishu token request failed: HTTP {response.status_code}"

    if int(body.get("code", -1)) != 0:
        message = _safe_text(body.get("msg") or body.get("message") or "unknown error")
        return "", f"Feishu token request failed: {message}"

    token = _safe_text(body.get("tenant_access_token"))
    if not token:
        return "", "Feishu token missing in response"

    expires_in = int(body.get("expire") or body.get("expires_in") or 7200)
    _cache_set_token(cache_key, token, expires_in)
    return token, ""


async def _fetch_wechat_work_access_token(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
) -> tuple[str, str]:
    corp_id = _safe_text(settings.chat_bridge_wechat_work_corp_id)
    corp_secret = _safe_text(settings.chat_bridge_wechat_work_corp_secret)
    if not corp_id or not corp_secret:
        return "", "Missing WeChat Work credentials"

    cache_key = f"wechat-work:{corp_id}"
    cached = _cache_get_token(cache_key)
    if cached:
        return cached, ""

    base_url = _safe_text(settings.chat_bridge_wechat_work_api_base_url) or "https://qyapi.weixin.qq.com"
    response = await client.get(
        f"{base_url.rstrip('/')}/cgi-bin/gettoken",
        params={
            "corpid": corp_id,
            "corpsecret": corp_secret,
        },
    )
    body = _json_or_text(response)
    if response.status_code >= 400:
        return "", f"WeChat Work token request failed: HTTP {response.status_code}"

    errcode = int(body.get("errcode", -1))
    if errcode != 0:
        message = _safe_text(body.get("errmsg") or "unknown error")
        return "", f"WeChat Work token request failed: {message}"

    token = _safe_text(body.get("access_token"))
    if not token:
        return "", "WeChat Work access_token missing in response"

    expires_in = int(body.get("expires_in") or 7200)
    _cache_set_token(cache_key, token, expires_in)
    return token, ""


async def _fetch_wechat_official_access_token(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
) -> tuple[str, str]:
    app_id = _safe_text(settings.chat_bridge_wechat_official_app_id)
    app_secret = _safe_text(settings.chat_bridge_wechat_official_app_secret)
    if not app_id or not app_secret:
        return "", "Missing WeChat Official credentials"

    cache_key = f"wechat-official:{app_id}"
    cached = _cache_get_token(cache_key)
    if cached:
        return cached, ""

    base_url = _safe_text(settings.chat_bridge_wechat_official_api_base_url) or "https://api.weixin.qq.com"
    response = await client.get(
        f"{base_url.rstrip('/')}/cgi-bin/token",
        params={
            "grant_type": "client_credential",
            "appid": app_id,
            "secret": app_secret,
        },
    )
    body = _json_or_text(response)
    if response.status_code >= 400:
        return "", f"WeChat Official token request failed: HTTP {response.status_code}"

    errcode = int(body.get("errcode", 0) or 0)
    if errcode != 0:
        message = _safe_text(body.get("errmsg") or "unknown error")
        return "", f"WeChat Official token request failed: {message}"

    token = _safe_text(body.get("access_token"))
    if not token:
        return "", "WeChat Official access_token missing in response"

    expires_in = int(body.get("expires_in") or 7200)
    _cache_set_token(cache_key, token, expires_in)
    return token, ""


async def _deliver_feishu_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
) -> dict[str, Any]:
    response_payload = bridge_result.platform_response if isinstance(bridge_result.platform_response, dict) else {}
    receive_id = _safe_text(response_payload.get("receive_id"))
    receive_id_type = _safe_text(response_payload.get("receive_id_type") or "chat_id")
    content_raw = response_payload.get("content")

    if not receive_id:
        return {
            "platform": "feishu",
            "attempted": False,
            "sent": False,
            "reason": "Missing receive_id in platform_response",
        }

    token, token_error = await _fetch_feishu_tenant_access_token(client, settings=settings)
    if token_error:
        return {
            "platform": "feishu",
            "attempted": False,
            "sent": False,
            "reason": token_error,
        }

    content_json = json.dumps(content_raw if isinstance(content_raw, dict) else {"text": bridge_result.reply_text}, ensure_ascii=False)
    base_url = _safe_text(settings.chat_bridge_feishu_api_base_url) or "https://open.feishu.cn"
    response = await client.post(
        f"{base_url.rstrip('/')}/open-apis/im/v1/messages",
        params={"receive_id_type": receive_id_type or "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "receive_id": receive_id,
            "msg_type": _safe_text(response_payload.get("msg_type") or "text"),
            "content": content_json,
        },
    )
    body = _json_or_text(response)
    code = int(body.get("code", -1)) if isinstance(body.get("code"), int) or str(body.get("code", "")).isdigit() else -1
    sent = response.status_code < 400 and code in {0, -1}

    return {
        "platform": "feishu",
        "attempted": True,
        "sent": bool(sent),
        "status_code": response.status_code,
        "response": {
            "code": body.get("code"),
            "msg": body.get("msg") or body.get("message"),
        },
    }


async def _deliver_wechat_work_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
) -> dict[str, Any]:
    token, token_error = await _fetch_wechat_work_access_token(client, settings=settings)
    if token_error:
        return {
            "platform": "wechat",
            "mode": "work",
            "attempted": False,
            "sent": False,
            "reason": token_error,
        }

    agent_id = _safe_text(settings.chat_bridge_wechat_work_agent_id)
    if not agent_id:
        return {
            "platform": "wechat",
            "mode": "work",
            "attempted": False,
            "sent": False,
            "reason": "Missing WeChat Work agent_id",
        }

    base_url = _safe_text(settings.chat_bridge_wechat_work_api_base_url) or "https://qyapi.weixin.qq.com"
    response = await client.post(
        f"{base_url.rstrip('/')}/cgi-bin/message/send",
        params={"access_token": token},
        json={
            "touser": bridge_result.external_user_id,
            "msgtype": "text",
            "agentid": int(agent_id),
            "text": {"content": bridge_result.reply_text},
            "safe": 0,
        },
    )
    body = _json_or_text(response)
    errcode = int(body.get("errcode", -1)) if str(body.get("errcode", "")).lstrip("-").isdigit() else -1
    sent = response.status_code < 400 and errcode == 0

    return {
        "platform": "wechat",
        "mode": "work",
        "attempted": True,
        "sent": bool(sent),
        "status_code": response.status_code,
        "response": {
            "errcode": body.get("errcode"),
            "errmsg": body.get("errmsg"),
        },
    }


async def _deliver_wechat_official_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
) -> dict[str, Any]:
    token, token_error = await _fetch_wechat_official_access_token(client, settings=settings)
    if token_error:
        return {
            "platform": "wechat",
            "mode": "official",
            "attempted": False,
            "sent": False,
            "reason": token_error,
        }

    base_url = _safe_text(settings.chat_bridge_wechat_official_api_base_url) or "https://api.weixin.qq.com"
    response = await client.post(
        f"{base_url.rstrip('/')}/cgi-bin/message/custom/send",
        params={"access_token": token},
        json={
            "touser": bridge_result.external_user_id,
            "msgtype": "text",
            "text": {"content": bridge_result.reply_text},
        },
    )
    body = _json_or_text(response)
    errcode = int(body.get("errcode", -1)) if str(body.get("errcode", "")).lstrip("-").isdigit() else -1
    sent = response.status_code < 400 and errcode == 0

    return {
        "platform": "wechat",
        "mode": "official",
        "attempted": True,
        "sent": bool(sent),
        "status_code": response.status_code,
        "response": {
            "errcode": body.get("errcode"),
            "errmsg": body.get("errmsg"),
        },
    }


async def _deliver_wechat_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
) -> dict[str, Any]:
    mode = _safe_text(settings.chat_bridge_wechat_delivery_mode).lower() or "auto"
    if mode not in {"auto", "work", "official"}:
        mode = "auto"

    if mode == "work":
        return await _deliver_wechat_work_reply(client, settings=settings, bridge_result=bridge_result)
    if mode == "official":
        return await _deliver_wechat_official_reply(client, settings=settings, bridge_result=bridge_result)

    has_work = bool(_safe_text(settings.chat_bridge_wechat_work_corp_id) and _safe_text(settings.chat_bridge_wechat_work_corp_secret))
    has_official = bool(_safe_text(settings.chat_bridge_wechat_official_app_id) and _safe_text(settings.chat_bridge_wechat_official_app_secret))

    if has_work:
        return await _deliver_wechat_work_reply(client, settings=settings, bridge_result=bridge_result)
    if has_official:
        return await _deliver_wechat_official_reply(client, settings=settings, bridge_result=bridge_result)

    return {
        "platform": "wechat",
        "mode": "auto",
        "attempted": False,
        "sent": False,
        "reason": "Missing WeChat delivery credentials",
    }


def _qq_send_endpoint(bridge_result: PlatformBridgeResult) -> str:
    response_payload = bridge_result.platform_response if isinstance(bridge_result.platform_response, dict) else {}
    event_type = _safe_text(response_payload.get("event_type") or bridge_result.message_kind).lower()
    channel_id = _safe_text(response_payload.get("channel_id") or bridge_result.channel_id)
    user_id = _safe_text(response_payload.get("user_id") or bridge_result.external_user_id)

    if "group" in event_type and channel_id:
        return f"/v2/groups/{channel_id}/messages"
    if ("c2c" in event_type or "direct" in event_type) and user_id:
        return f"/v2/users/{user_id}/messages"
    if channel_id:
        return f"/channels/{channel_id}/messages"
    if user_id:
        return f"/v2/users/{user_id}/messages"
    return ""


def _has_qq_official_credentials(settings: Settings) -> bool:
    return bool(_safe_text(settings.chat_bridge_qq_bot_app_id) and _resolve_qq_bot_app_secret(settings))


def _resolve_qq_bot_app_secret(settings: Settings) -> str:
    # Backward compatibility: old config key chat_bridge_qq_bot_token is treated as app_secret.
    return _safe_text(settings.chat_bridge_qq_bot_app_secret) or _safe_text(settings.chat_bridge_qq_bot_token)


async def _fetch_qq_bot_access_token(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
) -> tuple[str, str]:
    app_id = _safe_text(settings.chat_bridge_qq_bot_app_id)
    app_secret = _resolve_qq_bot_app_secret(settings)
    if not app_id or not app_secret:
        return "", "Missing QQ bot app_id/app_secret"

    cache_key = f"qq-official:{app_id}"
    cached = _cache_get_token(cache_key)
    if cached:
        return cached, ""

    response = await client.post(
        _QQ_BOT_ACCESS_TOKEN_URL,
        json={
            "appId": app_id,
            "clientSecret": app_secret,
        },
    )
    body = _json_or_text(response)
    if response.status_code >= 400:
        return "", f"QQ token request failed: HTTP {response.status_code}"

    token = _safe_text(body.get("access_token") or body.get("accessToken"))
    if not token:
        message = _safe_text(body.get("message") or body.get("msg") or "unknown error")
        return "", f"QQ token request failed: {message}"

    expires_in_raw = body.get("expires_in") or body.get("expiresIn") or 7200
    try:
        expires_in = max(int(expires_in_raw), 60)
    except (TypeError, ValueError):
        expires_in = 7200
    _cache_set_token(cache_key, token, expires_in)
    return token, ""


def _looks_like_napcat_event(bridge_result: PlatformBridgeResult, payload: dict[str, Any]) -> bool:
    if _safe_text(payload.get("post_type")):
        return True

    response_payload = bridge_result.platform_response if isinstance(bridge_result.platform_response, dict) else {}
    delivery_mode = _safe_text(response_payload.get("delivery_mode")).lower()
    if delivery_mode == "napcat":
        return True

    message_kind = _safe_text(bridge_result.message_kind).lower()
    return message_kind.startswith("napcat.")


def _coerce_numeric_id(value: str) -> int | str:
    text = _safe_text(value)
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            return text
    return text


def _resolve_napcat_target(bridge_result: PlatformBridgeResult, payload: dict[str, Any]) -> tuple[str, str]:
    response_payload = bridge_result.platform_response if isinstance(bridge_result.platform_response, dict) else {}

    message_type = _safe_text(
        response_payload.get("message_type")
        or payload.get("message_type")
        or (payload.get("d", {}).get("message_type") if isinstance(payload.get("d"), dict) else "")
    ).lower()

    if not message_type:
        kind = _safe_text(bridge_result.message_kind).lower()
        if "group" in kind:
            message_type = "group"
        else:
            message_type = "private"

    group_id = _safe_text(
        response_payload.get("group_id")
        or payload.get("group_id")
        or bridge_result.channel_id
    )
    user_id = _safe_text(
        response_payload.get("user_id")
        or payload.get("user_id")
        or bridge_result.external_user_id
    )

    if message_type == "group" and group_id:
        return "group", group_id
    if user_id:
        return "private", user_id
    if group_id:
        return "group", group_id
    return "", ""


async def _deliver_qq_official_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
    payload: dict[str, Any],
) -> dict[str, Any]:
    app_id = _safe_text(settings.chat_bridge_qq_bot_app_id)
    app_secret = _resolve_qq_bot_app_secret(settings)
    if not app_id or not app_secret:
        return {
            "platform": "qq",
            "attempted": False,
            "sent": False,
            "reason": "Missing QQ bot app_id/app_secret",
        }

    access_token, token_error = await _fetch_qq_bot_access_token(client, settings=settings)
    if token_error:
        return {
            "platform": "qq",
            "mode": "official",
            "attempted": False,
            "sent": False,
            "reason": token_error,
        }

    endpoint = _qq_send_endpoint(bridge_result)
    if not endpoint:
        return {
            "platform": "qq",
            "attempted": False,
            "sent": False,
            "reason": "Unable to resolve QQ send endpoint for current event",
        }

    base_url = _safe_text(settings.chat_bridge_qq_api_base_url) or "https://api.sgroup.qq.com"
    message_id = _safe_text(
        payload.get("message_id")
        or payload.get("id")
        or (payload.get("d", {}).get("id") if isinstance(payload.get("d"), dict) else "")
    )

    request_body: dict[str, Any] = {
        "content": bridge_result.reply_text,
    }
    if message_id:
        request_body["msg_id"] = message_id

    response = await client.post(
        f"{base_url.rstrip('/')}{endpoint}",
        headers={
            "Authorization": f"QQBot {access_token}",
            "X-Union-Appid": app_id,
        },
        json=request_body,
    )
    body = _json_or_text(response)
    code_raw = body.get("code")
    code = int(code_raw) if str(code_raw or "").lstrip("-").isdigit() else 0
    sent = response.status_code < 400 and code in {0}

    return {
        "platform": "qq",
        "mode": "official",
        "attempted": True,
        "sent": bool(sent),
        "status_code": response.status_code,
        "endpoint": endpoint,
        "response": {
            "code": body.get("code"),
            "message": body.get("message") or body.get("msg"),
        },
    }


async def _deliver_qq_napcat_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
    payload: dict[str, Any],
) -> dict[str, Any]:
    base_url = _safe_text(settings.chat_bridge_qq_napcat_api_base_url)
    if not base_url:
        return {
            "platform": "qq",
            "mode": "napcat",
            "attempted": False,
            "sent": False,
            "reason": "Missing NapCat API base URL",
        }

    message_type, target_id = _resolve_napcat_target(bridge_result, payload)
    if not message_type or not target_id:
        return {
            "platform": "qq",
            "mode": "napcat",
            "attempted": False,
            "sent": False,
            "reason": "Unable to resolve NapCat target user/group",
        }

    message_payload, payload_mode = _resolve_napcat_outbound_message(bridge_result)
    request_body: dict[str, Any] = {
        "message_type": message_type,
        "message": message_payload,
    }
    if message_type == "group":
        request_body["group_id"] = _coerce_numeric_id(target_id)
    else:
        request_body["user_id"] = _coerce_numeric_id(target_id)

    headers: dict[str, str] = {}
    access_token = _safe_text(settings.chat_bridge_qq_napcat_access_token)
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    response = await client.post(
        f"{base_url.rstrip('/')}/send_msg",
        headers=headers,
        json=request_body,
    )
    body = _json_or_text(response)

    retcode_raw = body.get("retcode")
    retcode = int(retcode_raw) if str(retcode_raw or "").lstrip("-").isdigit() else 0
    status_text = _safe_text(body.get("status")).lower()
    sent = response.status_code < 400 and retcode == 0 and status_text in {"", "ok", "success"}

    return {
        "platform": "qq",
        "mode": "napcat",
        "attempted": True,
        "sent": bool(sent),
        "status_code": response.status_code,
        "endpoint": "/send_msg",
        "message_payload_mode": payload_mode,
        "response": {
            "retcode": body.get("retcode"),
            "status": body.get("status"),
            "message": body.get("message") or body.get("msg"),
        },
    }


async def _deliver_qq_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
    payload: dict[str, Any],
) -> dict[str, Any]:
    mode = _safe_text(settings.chat_bridge_qq_delivery_mode).lower() or "auto"
    if mode not in {"auto", "official", "napcat"}:
        mode = "auto"

    if mode == "official":
        return await _deliver_qq_official_reply(client, settings=settings, bridge_result=bridge_result, payload=payload)
    if mode == "napcat":
        return await _deliver_qq_napcat_reply(client, settings=settings, bridge_result=bridge_result, payload=payload)

    # auto mode
    if _looks_like_napcat_event(bridge_result, payload):
        return await _deliver_qq_napcat_reply(client, settings=settings, bridge_result=bridge_result, payload=payload)

    if _has_qq_official_credentials(settings):
        return await _deliver_qq_official_reply(client, settings=settings, bridge_result=bridge_result, payload=payload)

    return await _deliver_qq_napcat_reply(client, settings=settings, bridge_result=bridge_result, payload=payload)


async def send_platform_callback_if_configured(
    platform: str,
    *,
    settings: Settings,
    bridge_result: PlatformBridgeResult,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Optionally deliver generated replies to platform official APIs."""
    normalized_platform = _safe_text(platform).lower()
    enabled = bool(settings.chat_bridge_callback_delivery_enabled)

    deliverable_event = bridge_result.event_type in {"message", "error"}
    reply_text = _safe_text(bridge_result.reply_text)

    if not deliverable_event or not reply_text:
        return {
            "enabled": enabled,
            "attempted": False,
            "sent": False,
            "reason": "Event does not contain deliverable reply text",
        }

    if not enabled:
        return {
            "enabled": False,
            "attempted": False,
            "sent": False,
            "reason": "Callback delivery is disabled",
        }

    timeout_seconds = max(int(settings.chat_bridge_callback_timeout_seconds), 1)
    timeout = httpx.Timeout(timeout=timeout_seconds)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if normalized_platform == "feishu":
                result = await _deliver_feishu_reply(client, settings=settings, bridge_result=bridge_result)
            elif normalized_platform == "wechat":
                result = await _deliver_wechat_reply(client, settings=settings, bridge_result=bridge_result)
            elif normalized_platform == "qq":
                result = await _deliver_qq_reply(client, settings=settings, bridge_result=bridge_result, payload=payload)
            else:
                result = {
                    "platform": normalized_platform,
                    "attempted": False,
                    "sent": False,
                    "reason": "No official delivery adapter for platform",
                }
    except Exception as exc:  # pragma: no cover - defensive guard
        result = {
            "platform": normalized_platform,
            "attempted": True,
            "sent": False,
            "reason": f"Delivery failed: {exc}",
        }

    result["enabled"] = enabled
    return result
