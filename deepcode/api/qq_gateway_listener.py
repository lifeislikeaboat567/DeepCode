"""Standalone QQ Official Gateway listener management and runtime."""

from __future__ import annotations

import asyncio
import json
import locale
import os
import random
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from deepcode.api.platform_bridge import process_platform_event
from deepcode.config import Settings, apply_chat_bridge_runtime_overrides, get_settings
from deepcode.logging_config import get_logger

logger = get_logger(__name__)

_META_FILE = "qq_gateway_listener.json"
_LOG_FILE = "qq_gateway_listener.log"
_QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_QQ_API_BASE = "https://api.sgroup.qq.com"
_INTENTS_PUBLIC_GUILD_MESSAGES = 1 << 30
_INTENTS_DIRECT_MESSAGE = 1 << 12
_INTENTS_GROUP_AND_C2C = 1 << 25
_FULL_INTENTS = _INTENTS_PUBLIC_GUILD_MESSAGES | _INTENTS_DIRECT_MESSAGE | _INTENTS_GROUP_AND_C2C
_MSG_SEQ_COUNTERS: dict[str, int] = {}


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _next_msg_seq(message_id: str) -> int:
    key = _safe_text(message_id) or "default"
    previous = _MSG_SEQ_COUNTERS.get(key)
    if previous is None:
        seed = int(time.time() * 1000) % 65536
        seq = (seed ^ random.randint(0, 65535)) % 65536
    else:
        seq = (previous + 1) % 65536
    _MSG_SEQ_COUNTERS[key] = seq
    return seq


def _resolve_runtime_settings(settings: Settings | None = None) -> Settings:
    active = settings or get_settings()
    apply_chat_bridge_runtime_overrides(active)
    return active


def qq_gateway_listener_meta_path(settings: Settings | None = None) -> Path:
    active = _resolve_runtime_settings(settings)
    active.ensure_data_dir()
    return active.data_dir / _META_FILE


def qq_gateway_listener_log_path(settings: Settings | None = None) -> Path:
    active = _resolve_runtime_settings(settings)
    active.ensure_data_dir()
    return active.data_dir / _LOG_FILE


def _load_meta(settings: Settings) -> dict[str, Any]:
    path = qq_gateway_listener_meta_path(settings)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_meta(settings: Settings, payload: dict[str, Any]) -> None:
    path = qq_gateway_listener_meta_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_meta(settings: Settings) -> None:
    path = qq_gateway_listener_meta_path(settings)
    if path.exists():
        path.unlink()


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False

        stdout_raw = result.stdout if isinstance(result.stdout, (bytes, bytearray)) else b""
        encoding = locale.getpreferredencoding(False) or "utf-8"
        output = stdout_raw.decode(encoding=encoding, errors="ignore").strip()
        if not output:
            return False
        if "No tasks are running" in output:
            return False
        return f'"{pid}"' in output or f",{pid}," in output

    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _has_qq_bot_credentials(settings: Settings) -> bool:
    app_id = _safe_text(getattr(settings, "chat_bridge_qq_bot_app_id", ""))
    app_secret = _safe_text(getattr(settings, "chat_bridge_qq_bot_app_secret", "")) or _safe_text(
        getattr(settings, "chat_bridge_qq_bot_token", "")
    )
    return bool(app_id and app_secret)


def get_qq_gateway_listener_status(settings: Settings | None = None) -> dict[str, Any]:
    active = _resolve_runtime_settings(settings)
    meta = _load_meta(active)
    pid = int(meta.get("pid") or 0)
    process_alive = _is_process_alive(pid) if pid else False
    managed = bool(meta)
    running = managed and process_alive
    status = "running" if running else "stopped"

    return {
        "status": status,
        "running": running,
        "managed": managed,
        "pid": pid,
        "process_alive": process_alive,
        "started_at": str(meta.get("started_at", "")),
        "command": str(meta.get("command", "")),
        "credentials_ready": _has_qq_bot_credentials(active),
        "log_file": str(qq_gateway_listener_log_path(active)),
    }


def start_qq_gateway_listener(settings: Settings | None = None) -> dict[str, Any]:
    active = _resolve_runtime_settings(settings)
    status = get_qq_gateway_listener_status(active)
    if status["running"]:
        status["action"] = "already_running"
        return status

    if not _has_qq_bot_credentials(active):
        status["action"] = "missing_credentials"
        status["reason"] = "chat_bridge_qq_bot_app_id/chat_bridge_qq_bot_app_secret is required"
        return status

    python_executable = sys.executable
    command_parts = [
        python_executable,
        "-m",
        "deepcode",
        "qqgateway",
        "run",
        "--skip-preflight",
    ]

    creation_flags = 0
    if os.name == "nt":
        creation_flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        creation_flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
        creation_flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    log_fp = open(qq_gateway_listener_log_path(active), "a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command_parts,
            stdout=log_fp,
            stderr=log_fp,
            creationflags=creation_flags,
        )
    finally:
        log_fp.close()

    _save_meta(
        active,
        {
            "pid": int(process.pid),
            "command": " ".join(command_parts),
            "started_at": str(int(time.time())),
        },
    )

    for _ in range(10):
        time.sleep(0.2)
        next_status = get_qq_gateway_listener_status(active)
        if next_status["running"]:
            next_status["action"] = "started"
            return next_status

    final_status = get_qq_gateway_listener_status(active)
    final_status["action"] = "start_requested"
    return final_status


def stop_qq_gateway_listener(settings: Settings | None = None) -> dict[str, Any]:
    active = _resolve_runtime_settings(settings)
    meta = _load_meta(active)
    pid = int(meta.get("pid") or 0)

    if pid > 0 and _is_process_alive(pid):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    _clear_meta(active)
    next_status = get_qq_gateway_listener_status(active)
    next_status["action"] = "stopped"
    return next_status


async def _fetch_access_token(client: httpx.AsyncClient, settings: Settings) -> tuple[str, str]:
    app_id = _safe_text(getattr(settings, "chat_bridge_qq_bot_app_id", ""))
    app_secret = _safe_text(getattr(settings, "chat_bridge_qq_bot_app_secret", "")) or _safe_text(
        getattr(settings, "chat_bridge_qq_bot_token", "")
    )
    if not app_id or not app_secret:
        return "", "missing qq app credentials"

    response = await client.post(
        _QQ_TOKEN_URL,
        json={
            "appId": app_id,
            "clientSecret": app_secret,
        },
    )
    if response.status_code >= 400:
        return "", f"token http {response.status_code}"

    try:
        payload = response.json()
    except ValueError:
        return "", "token response is not json"

    if not isinstance(payload, dict):
        return "", "token response shape invalid"

    token = _safe_text(payload.get("access_token") or payload.get("accessToken"))
    if not token:
        return "", _safe_text(payload.get("message") or payload.get("msg") or "token missing")
    return token, ""


async def _fetch_gateway_url(client: httpx.AsyncClient, token: str) -> tuple[str, str]:
    response = await client.get(
        f"{_QQ_API_BASE}/gateway",
        headers={"Authorization": f"QQBot {token}"},
    )
    if response.status_code >= 400:
        return "", f"gateway http {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return "", "gateway response is not json"
    if not isinstance(payload, dict):
        return "", "gateway response shape invalid"
    url = _safe_text(payload.get("url"))
    if not url:
        return "", "gateway url missing"
    return url, ""


def _resolve_send_target(event_type: str, data: dict[str, Any]) -> tuple[str, str]:
    event = _safe_text(event_type).upper()
    if event == "C2C_MESSAGE_CREATE":
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        user_openid = _safe_text(author.get("user_openid") or author.get("id"))
        return (f"/v2/users/{user_openid}/messages", user_openid)

    if event == "GROUP_AT_MESSAGE_CREATE":
        group_openid = _safe_text(data.get("group_openid") or data.get("group_id"))
        return (f"/v2/groups/{group_openid}/messages", group_openid)

    if event == "AT_MESSAGE_CREATE":
        channel_id = _safe_text(data.get("channel_id"))
        return (f"/channels/{channel_id}/messages", channel_id)

    if event == "MESSAGE_CREATE":
        channel_id = _safe_text(data.get("channel_id"))
        return (f"/channels/{channel_id}/messages", channel_id)

    if event == "DIRECT_MESSAGE_CREATE":
        guild_id = _safe_text(data.get("guild_id"))
        return (f"/dms/{guild_id}/messages", guild_id)

    return "", ""


async def _send_gateway_reply(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    access_token: str,
    event_type: str,
    data: dict[str, Any],
    reply_text: str,
) -> None:
    endpoint, target_id = _resolve_send_target(event_type, data)
    if not endpoint or not target_id:
        return

    app_id = _safe_text(getattr(settings, "chat_bridge_qq_bot_app_id", ""))
    message_id = _safe_text(data.get("id"))
    msg_seq = _next_msg_seq(message_id)
    body: dict[str, Any] = {
        "content": reply_text,
        "msg_type": 0,
        "msg_seq": msg_seq,
    }
    if message_id:
        body["msg_id"] = message_id

    response = await client.post(
        f"{_QQ_API_BASE}{endpoint}",
        headers={
            "Authorization": f"QQBot {access_token}",
            "X-Union-Appid": app_id,
        },
        json=body,
    )
    if response.status_code >= 400:
        response_text = _safe_text(response.text)
        logger.warning(
            "QQ gateway reply failed",
            endpoint=endpoint,
            status_code=response.status_code,
            event_type=event_type,
            response=response_text[:800],
        )
        return

    logger.info(
        "QQ gateway reply sent",
        endpoint=endpoint,
        status_code=response.status_code,
        event_type=event_type,
        msg_id=message_id,
        msg_seq=msg_seq,
    )


async def run_qq_gateway_loop(settings: Settings | None = None) -> None:
    active = _resolve_runtime_settings(settings)
    try:
        import websockets
    except Exception as exc:  # pragma: no cover - runtime guard
        raise RuntimeError("websockets dependency is required for qqgateway") from exc

    timeout_seconds = max(int(getattr(active, "chat_bridge_callback_timeout_seconds", 12) or 12), 5)
    timeout = httpx.Timeout(timeout=timeout_seconds)

    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout) as http_client:
                access_token, token_error = await _fetch_access_token(http_client, active)
                if token_error:
                    logger.error("QQ gateway token fetch failed", reason=token_error)
                    await asyncio.sleep(3)
                    continue

                gateway_url, gateway_error = await _fetch_gateway_url(http_client, access_token)
                if gateway_error:
                    logger.error("QQ gateway url fetch failed", reason=gateway_error)
                    await asyncio.sleep(3)
                    continue

                logger.info("QQ gateway connecting", url=gateway_url)

                async with websockets.connect(gateway_url) as ws:  # type: ignore[attr-defined]
                    heartbeat_task: asyncio.Task[Any] | None = None
                    last_seq: int | None = None

                    async def _start_heartbeat(interval_ms: int) -> None:
                        nonlocal heartbeat_task
                        if heartbeat_task is not None and not heartbeat_task.done():
                            heartbeat_task.cancel()

                        async def _heartbeat() -> None:
                            while True:
                                await asyncio.sleep(max(interval_ms / 1000.0, 1.0))
                                await ws.send(json.dumps({"op": 1, "d": last_seq}))

                        heartbeat_task = asyncio.create_task(_heartbeat())

                    try:
                        async for raw in ws:
                            try:
                                payload = json.loads(raw)
                            except (TypeError, ValueError, json.JSONDecodeError):
                                continue
                            if not isinstance(payload, dict):
                                continue

                            op = int(payload.get("op", -1)) if str(payload.get("op", "")).lstrip("-").isdigit() else -1
                            seq_raw = payload.get("s")
                            if str(seq_raw or "").lstrip("-").isdigit():
                                last_seq = int(seq_raw)

                            if op == 10:
                                data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
                                interval_ms = int(data.get("heartbeat_interval") or 30000)
                                await _start_heartbeat(interval_ms)
                                await ws.send(
                                    json.dumps(
                                        {
                                            "op": 2,
                                            "d": {
                                                "token": f"QQBot {access_token}",
                                                "intents": _FULL_INTENTS,
                                                "shard": [0, 1],
                                            },
                                        }
                                    )
                                )
                                continue

                            if op in {7, 9}:
                                break

                            if op != 0:
                                continue

                            event_type = _safe_text(payload.get("t"))
                            data = payload.get("d") if isinstance(payload.get("d"), dict) else {}
                            if event_type not in {
                                "C2C_MESSAGE_CREATE",
                                "GROUP_AT_MESSAGE_CREATE",
                                "AT_MESSAGE_CREATE",
                                "MESSAGE_CREATE",
                                "DIRECT_MESSAGE_CREATE",
                            }:
                                continue

                            event_payload = {
                                "t": event_type,
                                "d": data,
                                "id": _safe_text(data.get("id") or payload.get("id")),
                            }

                            logger.info(
                                "QQ gateway inbound event",
                                event_type=event_type,
                                event_id=_safe_text(event_payload.get("id")),
                            )

                            bridge_result = await process_platform_event("qq", event_payload, settings=active)
                            reply_text = _safe_text(bridge_result.reply_text)
                            reply_len = len(reply_text)
                            if not reply_text:
                                logger.info(
                                    "QQ gateway event skipped",
                                    event_type=event_type,
                                    bridge_event_type=bridge_result.event_type,
                                    reply_len=reply_len,
                                )
                                continue

                            if bridge_result.event_type not in {"message", "error"}:
                                logger.info(
                                    "QQ gateway non-reply bridge event",
                                    event_type=event_type,
                                    bridge_event_type=bridge_result.event_type,
                                    reply_len=reply_len,
                                )
                                continue

                            logger.info(
                                "QQ gateway preparing reply",
                                event_type=event_type,
                                bridge_event_type=bridge_result.event_type,
                                reply_len=reply_len,
                            )

                            await _send_gateway_reply(
                                http_client,
                                settings=active,
                                access_token=access_token,
                                event_type=event_type,
                                data=data,
                                reply_text=reply_text,
                            )
                    finally:
                        if heartbeat_task is not None and not heartbeat_task.done():
                            heartbeat_task.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - reconnect guard
            logger.exception("QQ gateway loop crashed, reconnecting", error=str(exc))
            await asyncio.sleep(3)


def run_qq_gateway_listener(settings: Settings | None = None) -> None:
    active = _resolve_runtime_settings(settings)
    asyncio.run(run_qq_gateway_loop(active))
