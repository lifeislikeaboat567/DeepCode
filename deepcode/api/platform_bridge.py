"""Platform webhook bridge for external chat software integration."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from deepcode.chat_runtime import (
    complete_agent_response,
    complete_chat_response,
    normalize_chat_mode,
)
from deepcode.api.platform_local_commands import try_execute_platform_local_command
from deepcode.config import Settings, get_settings
from deepcode.llm.factory import create_llm_client
from deepcode.logging_config import get_logger
from deepcode.storage import Message, PlatformEventIdStore, Session, SessionStore
from deepcode.tools import build_default_tools

logger = get_logger(__name__)

BridgeMode = Literal["ask", "agent"]
BridgeEventType = Literal["message", "challenge", "ignored", "duplicate", "error"]


@dataclass
class ParsedBridgeCommand:
    mode: BridgeMode
    plan_only: bool
    content: str


@dataclass
class ParsedPlatformEvent:
    event_type: BridgeEventType
    external_user_id: str = ""
    channel_id: str = ""
    text: str = ""
    message_id: str = ""
    challenge: str = ""
    platform_event_id: str = ""
    message_kind: str = "text"
    reply_protocol: str = "json"
    source_bot_id: str = ""
    raw_sender_id: str = ""
    segment_types: list[str] = field(default_factory=list)
    message_segments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PlatformBridgeResult:
    ok: bool
    event_type: BridgeEventType
    platform: str
    session_id: str = ""
    external_user_id: str = ""
    channel_id: str = ""
    mode: BridgeMode = "ask"
    plan_only: bool = False
    platform_event_id: str = ""
    message_kind: str = ""
    reply_text: str = ""
    challenge: str = ""
    platform_response: dict[str, Any] = field(default_factory=dict)


_EVENT_ID_TTL_SECONDS = 24 * 60 * 60


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return text
            nested = _extract_text(parsed)
            return nested or text
        return text
    if isinstance(value, dict):
        for key in ("text", "content", "message", "title"):
            if key in value:
                nested = _extract_text(value.get(key))
                if nested:
                    return nested
        for nested in value.values():
            text = _extract_text(nested)
            if text:
                return text
        return ""
    if isinstance(value, list):
        pieces = [_extract_text(item) for item in value]
        return "".join(piece for piece in pieces if piece)
    return _safe_text(value)


def _coerce_qq_message_segments(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("type"), str):
            return [value]
        nested = value.get("message")
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
        if isinstance(nested, dict):
            return [nested]
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text or text[0] not in "[{":
            return []
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return _coerce_qq_message_segments(parsed)
    return []


def _normalize_qq_message_segment(segment: dict[str, Any]) -> dict[str, Any]:
    seg_type = _safe_text(segment.get("type")).lower()
    data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
    normalized_data: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            normalized_data[str(key)] = _safe_text(value)
    return {
        "type": seg_type,
        "data": normalized_data,
    }


def _qq_segment_to_text(segment: dict[str, Any]) -> str:
    seg_type = _safe_text(segment.get("type")).lower()
    data = segment.get("data") if isinstance(segment.get("data"), dict) else {}

    if seg_type == "text":
        return _safe_text(data.get("text"))
    if seg_type == "markdown":
        return _safe_text(data.get("content") or data.get("text") or data.get("markdown"))
    if seg_type == "at":
        qq_value = _safe_text(data.get("qq"))
        if qq_value:
            return "@all" if qq_value.lower() == "all" else f"@{qq_value}"
        return "@"
    if seg_type in {"image", "record", "video", "file", "mface"}:
        source = _safe_text(
            data.get("name")
            or data.get("file")
            or data.get("url")
            or data.get("path")
            or data.get("file_id")
            or ""
        )
        return f"[{seg_type}] {source}".strip()
    if seg_type == "reply":
        reply_id = _safe_text(data.get("id"))
        return f"[reply:{reply_id}]" if reply_id else "[reply]"
    if seg_type == "json":
        raw_json = _safe_text(data.get("data"))
        return raw_json or "[json]"

    fallback = _extract_text(data)
    if fallback:
        return fallback
    return f"[{seg_type}]" if seg_type else ""


def _extract_qq_message_payload(payload: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    segments = _coerce_qq_message_segments(payload.get("message"))
    segment_types: list[str] = []
    segment_rows: list[dict[str, Any]] = []
    pieces: list[str] = []

    for segment in segments:
        seg_type = _safe_text(segment.get("type")).lower()
        if seg_type:
            segment_types.append(seg_type)
        segment_rows.append(_normalize_qq_message_segment(segment))
        text_piece = _qq_segment_to_text(segment)
        if text_piece:
            pieces.append(text_piece)

    text = "\n".join(piece for piece in pieces if piece).strip()
    if not text:
        text = _extract_text(payload.get("raw_message") or payload.get("message") or payload.get("content"))

    return text, segment_types, segment_rows


def _cdata(value: str) -> str:
    # Split nested CDATA terminators to keep XML well-formed.
    return str(value or "").replace("]]>", "]]]]><![CDATA[>")


def _normalize_default_mode(default_mode: str | None) -> BridgeMode:
    mode = normalize_chat_mode(default_mode)
    return "agent" if mode == "agent" else "ask"


def _extract_new_chat_command_content(text: str) -> tuple[bool, str]:
    raw = _safe_text(text)
    lowered = raw.lower()
    for prefix in ("/newchat", "/new"):
        if lowered == prefix:
            return True, ""
        if lowered.startswith(f"{prefix} "):
            return True, raw[len(prefix) :].strip()
    return False, raw


def parse_bridge_command(text: str, *, default_mode: str | None) -> ParsedBridgeCommand:
    """Parse user-facing command prefixes from chat software text."""
    raw = _safe_text(text)
    lowered = raw.lower()
    commands: list[tuple[str, BridgeMode, bool]] = [
        ("/ask", "ask", False),
        ("/agent", "agent", False),
        ("/plan", "agent", True),
    ]
    for prefix, mode, plan_only in commands:
        if lowered == prefix:
            return ParsedBridgeCommand(mode=mode, plan_only=plan_only, content="")
        if lowered.startswith(f"{prefix} "):
            return ParsedBridgeCommand(
                mode=mode,
                plan_only=plan_only,
                content=raw[len(prefix) :].strip(),
            )

    return ParsedBridgeCommand(
        mode=_normalize_default_mode(default_mode),
        plan_only=False,
        content=raw,
    )


def _parse_generic_payload(payload: dict[str, Any]) -> ParsedPlatformEvent:
    user_id = _safe_text(
        payload.get("user_id")
        or payload.get("sender_id")
        or payload.get("from")
        or payload.get("from_user")
    )
    channel_id = _safe_text(
        payload.get("channel_id")
        or payload.get("chat_id")
        or payload.get("conversation_id")
        or payload.get("group_id")
        or payload.get("thread_id")
    )
    text = _extract_text(payload.get("text") or payload.get("message") or payload.get("content"))
    message_id = _safe_text(payload.get("message_id") or payload.get("msg_id") or payload.get("id"))
    if not user_id or not text:
        return ParsedPlatformEvent(event_type="ignored")
    return ParsedPlatformEvent(
        event_type="message",
        external_user_id=user_id,
        channel_id=channel_id,
        text=text,
        message_id=message_id,
    )


def _parse_qq_payload(payload: dict[str, Any]) -> ParsedPlatformEvent:
    post_type = _safe_text(payload.get("post_type")).lower()
    if post_type:
        message_type = _safe_text(payload.get("message_type") or "private").lower()
        if post_type != "message":
            return ParsedPlatformEvent(
                event_type="ignored",
                platform_event_id=_safe_text(payload.get("event_id") or payload.get("id") or payload.get("time")),
                message_kind=f"napcat.{post_type}",
            )

        user_id = _safe_text(payload.get("user_id") or payload.get("sender_id"))
        group_id = _safe_text(payload.get("group_id"))
        channel_id = group_id if message_type == "group" else user_id
        text, segment_types, message_segments = _extract_qq_message_payload(payload)
        message_id = _safe_text(payload.get("message_id") or payload.get("id"))
        event_marker = _safe_text(payload.get("event_id") or payload.get("id") or message_id)
        if not event_marker:
            event_marker = _safe_text(payload.get("time"))

        if not user_id or not text:
            return ParsedPlatformEvent(
                event_type="ignored",
                platform_event_id=event_marker,
                message_kind=f"napcat.{message_type or 'message'}",
                segment_types=segment_types,
                message_segments=message_segments,
            )

        return ParsedPlatformEvent(
            event_type="message",
            external_user_id=user_id,
            channel_id=channel_id,
            text=text,
            message_id=message_id,
            platform_event_id=event_marker,
            message_kind=f"napcat.{message_type or 'message'}",
            segment_types=segment_types,
            message_segments=message_segments,
        )

    gateway_event = _safe_text(payload.get("t"))
    if gateway_event and isinstance(payload.get("d"), dict):
        data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        member = data.get("member") if isinstance(data.get("member"), dict) else {}
        member_user = member.get("user") if isinstance(member.get("user"), dict) else {}

        message_events = {
            "AT_MESSAGE_CREATE",
            "GROUP_AT_MESSAGE_CREATE",
            "C2C_MESSAGE_CREATE",
            "MESSAGE_CREATE",
            "DIRECT_MESSAGE_CREATE",
        }
        if gateway_event not in message_events:
            return ParsedPlatformEvent(
                event_type="ignored",
                platform_event_id=_safe_text(payload.get("id") or payload.get("event_id")),
                message_kind=gateway_event.lower() or "event",
            )

        user_id = _safe_text(
            author.get("id")
            or author.get("user_openid")
            or author.get("member_openid")
            or member_user.get("id")
            or member_user.get("user_openid")
            or member_user.get("member_openid")
            or data.get("user_id")
            or data.get("openid")
            or data.get("member_openid")
            or payload.get("user_id")
        )
        channel_id = _safe_text(
            data.get("channel_id")
            or data.get("group_openid")
            or data.get("group_id")
            or payload.get("group_id")
            or payload.get("channel_id")
        )
        text = _extract_text(data.get("content") or data.get("message") or data.get("raw_message"))
        message_id = _safe_text(data.get("id") or payload.get("message_id") or payload.get("id"))
        if not user_id or not text:
            return ParsedPlatformEvent(event_type="ignored")

        return ParsedPlatformEvent(
            event_type="message",
            external_user_id=user_id,
            channel_id=channel_id,
            text=text,
            message_id=message_id,
            platform_event_id=_safe_text(payload.get("id") or payload.get("event_id")),
            message_kind=gateway_event.lower(),
        )

    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    user_id = _safe_text(payload.get("user_id") or sender.get("user_id") or payload.get("sender_id"))
    channel_id = _safe_text(
        payload.get("group_id")
        or payload.get("guild_id")
        or payload.get("channel_id")
        or payload.get("discuss_id")
    )
    text, segment_types, message_segments = _extract_qq_message_payload(payload)
    message_id = _safe_text(payload.get("message_id") or payload.get("id"))
    if not user_id or not text:
        return ParsedPlatformEvent(event_type="ignored", segment_types=segment_types, message_segments=message_segments)
    return ParsedPlatformEvent(
        event_type="message",
        external_user_id=user_id,
        channel_id=channel_id,
        text=text,
        message_id=message_id,
        platform_event_id=_safe_text(payload.get("event_id") or payload.get("id")),
        message_kind="text",
        segment_types=segment_types,
        message_segments=message_segments,
    )


def _parse_wechat_payload(payload: dict[str, Any]) -> ParsedPlatformEvent:
    protocol = _safe_text(payload.get("_bridge_payload_protocol") or "json").lower()
    user_id = _safe_text(payload.get("FromUserName") or payload.get("from_user_name") or payload.get("user_id"))
    channel_id = _safe_text(payload.get("ToUserName") or payload.get("to_user_name") or payload.get("chat_id"))
    message_type = _safe_text(payload.get("MsgType") or payload.get("msg_type") or payload.get("msgtype") or "text").lower()

    if message_type == "text":
        text = _extract_text(
            payload.get("Content")
            or payload.get("content")
            or payload.get("text")
            or (payload.get("text") if isinstance(payload.get("text"), dict) else None)
        )
    elif message_type == "event":
        event_name = _safe_text(payload.get("Event") or payload.get("event"))
        event_key = _safe_text(payload.get("EventKey") or payload.get("event_key"))
        text = " ".join(part for part in ["/event", event_name, event_key] if part).strip()
    else:
        text = ""

    message_id = _safe_text(payload.get("MsgId") or payload.get("msg_id") or payload.get("message_id") or payload.get("id"))
    if not user_id or not text:
        return ParsedPlatformEvent(
            event_type="ignored",
            platform_event_id=_safe_text(payload.get("event_id") or payload.get("id") or message_id),
            message_kind=message_type,
            reply_protocol="xml" if protocol == "xml" else "json",
            source_bot_id=channel_id,
            raw_sender_id=user_id,
        )

    return ParsedPlatformEvent(
        event_type="message",
        external_user_id=user_id,
        channel_id=channel_id,
        text=text,
        message_id=message_id,
        platform_event_id=_safe_text(payload.get("event_id") or payload.get("id") or message_id),
        message_kind=message_type,
        reply_protocol="xml" if protocol == "xml" else "json",
        source_bot_id=channel_id,
        raw_sender_id=user_id,
    )


def _parse_feishu_payload(payload: dict[str, Any]) -> ParsedPlatformEvent:
    header = payload.get("header") if isinstance(payload.get("header"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender_id = sender.get("sender_id") if isinstance(sender.get("sender_id"), dict) else {}
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    message_type = _safe_text(message.get("message_type") or event.get("message_type") or "text").lower()

    user_id = _safe_text(
        sender_id.get("open_id")
        or sender_id.get("union_id")
        or sender_id.get("user_id")
        or event.get("operator_id")
    )
    channel_id = _safe_text(message.get("chat_id") or message.get("open_chat_id") or event.get("chat_id"))
    content = message.get("content") or payload.get("text") or payload.get("content")
    text = ""
    if message_type == "text":
        text = _extract_text(content)
    elif message_type in {"post", "interactive", "share_chat", "share_user"}:
        text = _extract_text(content)
    elif message_type in {"image", "audio", "video", "file", "sticker", "media"}:
        text = ""
    else:
        text = _extract_text(content)

    message_id = _safe_text(message.get("message_id") or event.get("message_id") or payload.get("event_id"))
    if not user_id or not text:
        return ParsedPlatformEvent(
            event_type="ignored",
            platform_event_id=_safe_text(header.get("event_id") or payload.get("event_id")),
            message_kind=message_type,
        )

    return ParsedPlatformEvent(
        event_type="message",
        external_user_id=user_id,
        channel_id=channel_id,
        text=text,
        message_id=message_id,
        platform_event_id=_safe_text(header.get("event_id") or payload.get("event_id")),
        message_kind=message_type,
    )


def parse_platform_event(platform: str, payload: dict[str, Any]) -> ParsedPlatformEvent:
    """Normalize platform-specific payload into a common chat message shape."""
    challenge = _safe_text(payload.get("challenge"))
    if challenge:
        return ParsedPlatformEvent(event_type="challenge", challenge=challenge)

    normalized = _safe_text(platform).lower()
    parser_map = {
        "generic": _parse_generic_payload,
        "qq": _parse_qq_payload,
        "wechat": _parse_wechat_payload,
        "feishu": _parse_feishu_payload,
    }
    parser = parser_map.get(normalized, _parse_generic_payload)
    return parser(payload)


def _build_binding_key(platform: str, external_user_id: str, channel_id: str) -> str:
    normalized_platform = _safe_text(platform).lower() or "generic"
    normalized_user = _safe_text(external_user_id) or "unknown-user"
    normalized_channel = _safe_text(channel_id) or "dm"
    return f"{normalized_platform}:{normalized_channel}:{normalized_user}"


def _build_wechat_reply_xml(parsed: ParsedPlatformEvent, answer: str) -> str:
    sender = _cdata(parsed.raw_sender_id)
    bot = _cdata(parsed.source_bot_id)
    text = _cdata(answer)
    created = str(int(time.time()))
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{sender}]]></ToUserName>"
        f"<FromUserName><![CDATA[{bot}]]></FromUserName>"
        f"<CreateTime>{created}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{text}]]></Content>"
        "</xml>"
    )


def _build_platform_response_payload(
    platform: str,
    *,
    parsed: ParsedPlatformEvent,
    answer: str,
) -> dict[str, Any]:
    normalized = _safe_text(platform).lower()
    if normalized == "feishu":
        receive_id = parsed.channel_id or parsed.external_user_id
        receive_id_type = "chat_id" if parsed.channel_id else "open_id"
        return {
            "receive_id_type": receive_id_type,
            "receive_id": receive_id,
            "msg_type": "text",
            "content": {"text": answer},
        }

    if normalized == "wechat":
        payload: dict[str, Any] = {
            "reply_json": {
                "msgtype": "text",
                "text": {"content": answer},
                "touser": parsed.external_user_id,
            }
        }
        if parsed.reply_protocol == "xml":
            payload["reply_xml"] = _build_wechat_reply_xml(parsed, answer)
        return payload

    if normalized == "qq":
        if parsed.message_kind.startswith("napcat."):
            message_type = "group" if parsed.message_kind.endswith("group") else "private"
            response: dict[str, Any] = {
                "delivery_mode": "napcat",
                "event_type": parsed.message_kind,
                "message_type": message_type,
                "user_id": parsed.external_user_id,
                "reply": {"content": answer},
            }
            if parsed.segment_types:
                response["segment_types"] = parsed.segment_types
            if parsed.message_segments:
                response["message_segments"] = parsed.message_segments
            if message_type == "group":
                response["group_id"] = parsed.channel_id
            return response

        response = {
            "delivery_mode": "official",
            "event_type": parsed.message_kind,
            "channel_id": parsed.channel_id,
            "user_id": parsed.external_user_id,
            "reply": {"content": answer},
        }
        if parsed.segment_types:
            response["segment_types"] = parsed.segment_types
        if parsed.message_segments:
            response["message_segments"] = parsed.message_segments
        return response

    return {"reply": {"content": answer}}


async def _find_or_create_bound_session(
    store: SessionStore,
    *,
    platform: str,
    external_user_id: str,
    channel_id: str,
    force_new: bool = False,
) -> Session:
    binding_key = _build_binding_key(platform, external_user_id, channel_id)
    sessions = await store.list_all()
    for session in sessions:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        if _safe_text(metadata.get("chat_bridge_binding_key")) == binding_key:
            if not force_new:
                return session

            next_metadata = dict(metadata)
            next_metadata.pop("chat_bridge_binding_key", None)
            session.metadata = next_metadata
            await store.update(session)

    display_name = f"{_safe_text(platform).lower()}:{external_user_id}"
    created = await store.create(name=display_name)
    metadata = dict(created.metadata or {})
    metadata["chat_bridge_binding_key"] = binding_key
    metadata["chat_bridge_platform"] = _safe_text(platform).lower()
    metadata["chat_bridge_external_user_id"] = external_user_id
    metadata["chat_bridge_channel_id"] = channel_id
    created.metadata = metadata
    await store.update(created)
    return created


def _is_duplicate_message(session: Session, message_id: str) -> bool:
    normalized_id = _safe_text(message_id)
    if not normalized_id:
        return False

    metadata = dict(session.metadata or {})
    recent = metadata.get("chat_bridge_recent_message_ids")
    if not isinstance(recent, list):
        recent = []

    if normalized_id in recent:
        return True

    recent.append(normalized_id)
    metadata["chat_bridge_recent_message_ids"] = recent[-40:]
    session.metadata = metadata
    return False


async def process_platform_event(
    platform: str,
    payload: dict[str, Any],
    *,
    settings: Settings | None = None,
    store: SessionStore | None = None,
    event_id_store: PlatformEventIdStore | None = None,
) -> PlatformBridgeResult:
    """Process one platform callback and return a normalized response payload."""
    settings = settings or get_settings()
    normalized_platform = _safe_text(platform).lower()
    parsed = parse_platform_event(normalized_platform, payload)

    if parsed.event_type == "challenge":
        return PlatformBridgeResult(
            ok=True,
            event_type="challenge",
            platform=normalized_platform,
            challenge=parsed.challenge,
            platform_event_id=parsed.platform_event_id,
            message_kind=parsed.message_kind,
        )

    if parsed.event_type != "message":
        return PlatformBridgeResult(
            ok=True,
            event_type="ignored",
            platform=normalized_platform,
            platform_event_id=parsed.platform_event_id,
            message_kind=parsed.message_kind,
        )

    event_id_ttl = max(int(getattr(settings, "chat_bridge_event_id_ttl_seconds", _EVENT_ID_TTL_SECONDS)), 1)
    event_store = event_id_store or PlatformEventIdStore()
    if await event_store.is_duplicate_or_store(
        normalized_platform,
        parsed.platform_event_id,
        ttl_seconds=event_id_ttl,
    ):
        return PlatformBridgeResult(
            ok=True,
            event_type="duplicate",
            platform=normalized_platform,
            external_user_id=parsed.external_user_id,
            channel_id=parsed.channel_id,
            mode="ask",
            plan_only=False,
            platform_event_id=parsed.platform_event_id,
            message_kind=parsed.message_kind,
        )

    command = parse_bridge_command(parsed.text, default_mode=settings.chat_bridge_default_mode)
    force_new_session, next_content = _extract_new_chat_command_content(parsed.text)

    session_store = store or SessionStore()
    if force_new_session:
        session = await _find_or_create_bound_session(
            session_store,
            platform=normalized_platform,
            external_user_id=parsed.external_user_id,
            channel_id=parsed.channel_id,
            force_new=True,
        )

        if not next_content:
            answer = f"已开启新对话，会话 ID: {session.id}"
            session.messages.append(Message(role="assistant", content=answer))
            metadata = dict(session.metadata or {})
            metadata["chat_bridge_last_local_command"] = "/new"
            metadata["chat_bridge_last_message_id"] = parsed.message_id
            session.metadata = metadata
            await session_store.update(session)

            return PlatformBridgeResult(
                ok=True,
                event_type="message",
                platform=normalized_platform,
                session_id=session.id,
                external_user_id=parsed.external_user_id,
                channel_id=parsed.channel_id,
                mode="ask",
                plan_only=False,
                platform_event_id=parsed.platform_event_id,
                message_kind=parsed.message_kind,
                reply_text=answer,
                platform_response={
                    "local_command": {
                        "action": "new_chat",
                        "session_id": session.id,
                    }
                },
            )

        command = parse_bridge_command(next_content, default_mode=settings.chat_bridge_default_mode)
    else:
        session = await _find_or_create_bound_session(
            session_store,
            platform=normalized_platform,
            external_user_id=parsed.external_user_id,
            channel_id=parsed.channel_id,
        )

    if not command.content:
        return PlatformBridgeResult(
            ok=False,
            event_type="error",
            platform=normalized_platform,
            external_user_id=parsed.external_user_id,
            channel_id=parsed.channel_id,
            mode=command.mode,
            plan_only=command.plan_only,
            platform_event_id=parsed.platform_event_id,
            message_kind=parsed.message_kind,
            reply_text="Command received but no message body was provided.",
        )

    if _is_duplicate_message(session, parsed.message_id):
        return PlatformBridgeResult(
            ok=True,
            event_type="duplicate",
            platform=normalized_platform,
            session_id=session.id,
            external_user_id=parsed.external_user_id,
            channel_id=parsed.channel_id,
            mode=command.mode,
            plan_only=command.plan_only,
            platform_event_id=parsed.platform_event_id,
            message_kind=parsed.message_kind,
        )

    session.messages.append(Message(role="user", content=command.content))
    await session_store.update(session)

    local_command_result = await try_execute_platform_local_command(
        command.content,
        settings=settings,
    )
    if local_command_result.handled:
        answer = _safe_text(local_command_result.reply_text)
        session.messages.append(Message(role="assistant", content=answer))

        metadata = dict(session.metadata or {})
        metadata["chat_bridge_platform"] = normalized_platform
        metadata["chat_bridge_external_user_id"] = parsed.external_user_id
        metadata["chat_bridge_channel_id"] = parsed.channel_id
        metadata["chat_bridge_last_mode"] = "ask"
        metadata["chat_bridge_last_plan_only"] = False
        metadata["chat_bridge_last_message_id"] = parsed.message_id
        metadata["chat_bridge_last_local_command"] = command.content
        if local_command_result.metadata:
            metadata["chat_bridge_last_local_command_metadata"] = local_command_result.metadata
        session.metadata = metadata
        await session_store.update(session)

        platform_response = _build_platform_response_payload(
            normalized_platform,
            parsed=parsed,
            answer=answer,
        )
        if local_command_result.metadata:
            platform_response["local_command"] = local_command_result.metadata

        return PlatformBridgeResult(
            ok=True,
            event_type="message",
            platform=normalized_platform,
            session_id=session.id,
            external_user_id=parsed.external_user_id,
            channel_id=parsed.channel_id,
            mode="ask",
            plan_only=False,
            platform_event_id=parsed.platform_event_id,
            message_kind=parsed.message_kind,
            reply_text=answer,
            platform_response=platform_response,
        )

    try:
        llm = create_llm_client()
        tools = build_default_tools()
        if command.mode == "agent":
            agent_result = await complete_agent_response(
                llm,
                session.messages,
                tools=tools,
                plan_only=command.plan_only,
            )
            answer = _safe_text(agent_result.answer)
        else:
            answer = _safe_text(await complete_chat_response(llm, session.messages, tools=tools))
    except Exception as exc:
        logger.error(
            "Platform bridge request failed",
            platform=normalized_platform,
            session_id=session.id,
            error=str(exc),
        )
        return PlatformBridgeResult(
            ok=False,
            event_type="error",
            platform=normalized_platform,
            session_id=session.id,
            external_user_id=parsed.external_user_id,
            channel_id=parsed.channel_id,
            mode=command.mode,
            plan_only=command.plan_only,
            platform_event_id=parsed.platform_event_id,
            message_kind=parsed.message_kind,
            reply_text=f"Bridge processing failed: {exc}",
        )

    if not answer:
        answer = "收到消息了，但模型返回了空内容。请重试，或切换到 /ask 模式。"

    session.messages.append(Message(role="assistant", content=answer))
    metadata = dict(session.metadata or {})
    metadata["chat_bridge_platform"] = normalized_platform
    metadata["chat_bridge_external_user_id"] = parsed.external_user_id
    metadata["chat_bridge_channel_id"] = parsed.channel_id
    metadata["chat_bridge_last_mode"] = command.mode
    metadata["chat_bridge_last_plan_only"] = command.plan_only
    metadata["chat_bridge_last_message_id"] = parsed.message_id
    session.metadata = metadata
    await session_store.update(session)

    platform_response = _build_platform_response_payload(
        normalized_platform,
        parsed=parsed,
        answer=answer,
    )

    return PlatformBridgeResult(
        ok=True,
        event_type="message",
        platform=normalized_platform,
        session_id=session.id,
        external_user_id=parsed.external_user_id,
        channel_id=parsed.channel_id,
        mode=command.mode,
        plan_only=command.plan_only,
        platform_event_id=parsed.platform_event_id,
        message_kind=parsed.message_kind,
        reply_text=answer,
        platform_response=platform_response,
    )