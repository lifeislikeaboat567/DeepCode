"""NapCat message-list parsing and delivery payload tests."""

from __future__ import annotations

from deepcode.api.platform_bridge import PlatformBridgeResult, parse_platform_event
from deepcode.api.platform_delivery import _resolve_napcat_outbound_message


def test_parse_qq_message_list_with_markdown_and_file_segments() -> None:
    payload = {
        "post_type": "message",
        "message_type": "private",
        "user_id": "10001",
        "message_id": "msg-seg-1",
        "message": [
            {"type": "markdown", "data": {"content": "# Heading"}},
            {"type": "file", "data": {"name": "spec.md", "file": "spec.md"}},
            {"type": "text", "data": {"text": "tail"}},
        ],
    }

    parsed = parse_platform_event("qq", payload)

    assert parsed.event_type == "message"
    assert parsed.external_user_id == "10001"
    assert "# Heading" in parsed.text
    assert "[file] spec.md" in parsed.text
    assert "tail" in parsed.text
    assert "markdown" in parsed.segment_types
    assert "file" in parsed.segment_types


def test_parse_qq_file_only_segment_is_still_message_event() -> None:
    payload = {
        "post_type": "message",
        "message_type": "group",
        "group_id": "20002",
        "user_id": "10001",
        "message_id": "msg-seg-2",
        "message": [
            {"type": "file", "data": {"name": "report.pdf", "file": "report.pdf"}},
        ],
    }

    parsed = parse_platform_event("qq", payload)

    assert parsed.event_type == "message"
    assert parsed.channel_id == "20002"
    assert "[file] report.pdf" in parsed.text
    assert parsed.message_kind == "napcat.group"


def test_resolve_napcat_outbound_message_from_json_segments() -> None:
    bridge_result = PlatformBridgeResult(
        ok=True,
        event_type="message",
        platform="qq",
        reply_text=(
            "```json\n"
            "[{\"type\":\"markdown\",\"data\":{\"content\":\"# Release Notes\"}},"
            "{\"type\":\"file\",\"data\":{\"name\":\"artifact.zip\",\"file\":\"artifact.zip\"}}]"
            "\n```"
        ),
    )

    payload, payload_mode = _resolve_napcat_outbound_message(bridge_result)

    assert payload_mode == "segments"
    assert isinstance(payload, list)
    assert payload[0]["type"] == "markdown"
    assert payload[0]["data"]["content"] == "# Release Notes"
    assert payload[1]["type"] == "file"
    assert payload[1]["data"]["name"] == "artifact.zip"


def test_resolve_napcat_outbound_message_from_markdown_image_and_file_links() -> None:
    bridge_result = PlatformBridgeResult(
        ok=True,
        event_type="message",
        platform="qq",
        reply_text=(
            "这是你要的截图：![screen](C:/work/screenshot.png)\n"
            "完整报告：[download](C:/work/report.zip)"
        ),
    )

    payload, payload_mode = _resolve_napcat_outbound_message(bridge_result)

    assert payload_mode.startswith("segments")
    assert isinstance(payload, list)
    assert any(item["type"] == "image" and item["data"].get("file") == "C:/work/screenshot.png" for item in payload)
    assert any(item["type"] == "file" and item["data"].get("file") == "C:/work/report.zip" for item in payload)


def test_resolve_napcat_outbound_message_from_markdown_audio_and_video_links() -> None:
    bridge_result = PlatformBridgeResult(
        ok=True,
        event_type="message",
        platform="qq",
        reply_text=(
            "语音：[voice](https://cdn.example.com/reply.mp3)\n"
            "录像：[clip](https://cdn.example.com/demo.mp4)"
        ),
    )

    payload, payload_mode = _resolve_napcat_outbound_message(bridge_result)

    assert payload_mode.startswith("segments")
    assert isinstance(payload, list)
    assert any(item["type"] == "record" and item["data"].get("file") == "https://cdn.example.com/reply.mp3" for item in payload)
    assert any(item["type"] == "video" and item["data"].get("file") == "https://cdn.example.com/demo.mp4" for item in payload)


def test_resolve_napcat_outbound_message_from_markdown_data_uri_image() -> None:
    bridge_result = PlatformBridgeResult(
        ok=True,
        event_type="message",
        platform="qq",
        reply_text="截图：![inline](data:image/png;base64,QUJDRA==)",
    )

    payload, payload_mode = _resolve_napcat_outbound_message(bridge_result)

    assert payload_mode.startswith("segments")
    assert isinstance(payload, list)
    assert any(item["type"] == "image" and item["data"].get("file") == "base64://QUJDRA==" for item in payload)
