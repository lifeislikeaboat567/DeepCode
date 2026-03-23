"""Standalone NapCat inbound listener process management."""

from __future__ import annotations

import json
import locale
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from deepcode.config import Settings, get_settings

_META_FILE = "napcat_inbound_listener.json"


def napcat_inbound_listener_meta_path(settings: Settings | None = None) -> Path:
    active = settings or get_settings()
    active.ensure_data_dir()
    return active.data_dir / _META_FILE


def _load_meta(settings: Settings) -> dict[str, Any]:
    path = napcat_inbound_listener_meta_path(settings)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_meta(settings: Settings, payload: dict[str, Any]) -> None:
    path = napcat_inbound_listener_meta_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_meta(settings: Settings) -> None:
    path = napcat_inbound_listener_meta_path(settings)
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


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex((host, int(port))) == 0


def get_napcat_inbound_listener_status(
    settings: Settings | None = None,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> dict[str, Any]:
    active = settings or get_settings()
    meta = _load_meta(active)
    default_port = int(getattr(active, "chat_bridge_inbound_port", getattr(active, "api_port", 8000)) or 8000)
    meta_host = str(meta.get("host") or "").strip()
    meta_port = int(meta.get("port") or 0)

    target_host = host
    if port is None and meta_host:
        target_host = meta_host

    target_port = int(port if port is not None else (meta_port or default_port))

    pid = int(meta.get("pid") or 0)
    process_alive = _is_process_alive(pid) if pid else False
    listening = _is_port_open(target_host, target_port)
    managed = bool(meta)

    running = process_alive and listening if managed else False
    if managed and process_alive and listening:
        status = "running"
    elif listening and not managed:
        status = "port_in_use"
    else:
        status = "stopped"

    return {
        "status": status,
        "running": running,
        "managed": managed,
        "host": target_host,
        "port": target_port,
        "pid": pid,
        "process_alive": process_alive,
        "listening": listening,
        "started_at": str(meta.get("started_at", "")),
        "command": str(meta.get("command", "")),
    }


def start_napcat_inbound_listener(
    settings: Settings | None = None,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> dict[str, Any]:
    active = settings or get_settings()
    target_port = int(port or getattr(active, "chat_bridge_inbound_port", getattr(active, "api_port", 8000)) or 8000)
    status = get_napcat_inbound_listener_status(active, host=host, port=target_port)
    if status["running"]:
        status["action"] = "already_running"
        return status

    if status["listening"] and not status["managed"]:
        status["action"] = "port_in_use"
        return status

    python_executable = sys.executable
    command_parts = [
        python_executable,
        "-m",
        "deepcode",
        "serve",
        "--host",
        host,
        "--port",
        str(target_port),
        "--skip-preflight",
    ]

    creation_flags = 0
    if os.name == "nt":
        creation_flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        creation_flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0))
        creation_flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))

    process = subprocess.Popen(
        command_parts,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )

    _save_meta(
        active,
        {
            "pid": int(process.pid),
            "host": host,
            "port": target_port,
            "command": " ".join(command_parts),
            "started_at": str(int(time.time())),
        },
    )

    for _ in range(15):
        if _is_port_open(host, target_port):
            break
        time.sleep(0.2)

    next_status = get_napcat_inbound_listener_status(active, host=host, port=target_port)
    next_status["action"] = "started" if next_status["running"] else "start_requested"
    return next_status


def stop_napcat_inbound_listener(settings: Settings | None = None) -> dict[str, Any]:
    active = settings or get_settings()
    meta = _load_meta(active)
    target_host = str(meta.get("host") or "127.0.0.1")
    target_port = int(meta.get("port") or getattr(active, "chat_bridge_inbound_port", getattr(active, "api_port", 8000)) or 8000)
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

    for _ in range(10):
        status = get_napcat_inbound_listener_status(active, host=target_host, port=target_port)
        if not status["running"]:
            status["action"] = "stopped"
            return status
        time.sleep(0.2)

    status = get_napcat_inbound_listener_status(active, host=target_host, port=target_port)
    status["action"] = "stop_requested"
    return status
