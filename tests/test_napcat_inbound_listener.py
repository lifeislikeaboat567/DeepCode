"""Unit tests for standalone NapCat inbound listener lifecycle helpers."""

from __future__ import annotations

from deepcode.api import napcat_inbound_listener as listener
from deepcode.config import Settings


class _DummyProcess:
    def __init__(self, pid: int):
        self.pid = pid


def test_get_listener_status_without_meta_returns_stopped(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, chat_bridge_inbound_port=18080)
    monkeypatch.setattr(listener, "_is_port_open", lambda _host, _port: False)

    status = listener.get_napcat_inbound_listener_status(settings)

    assert status["status"] == "stopped"
    assert status["running"] is False
    assert status["managed"] is False
    assert status["pid"] == 0
    assert status["port"] == 18080


def test_get_listener_status_without_meta_reports_port_in_use(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, chat_bridge_inbound_port=18083)
    monkeypatch.setattr(listener, "_is_port_open", lambda _host, _port: True)

    status = listener.get_napcat_inbound_listener_status(settings)

    assert status["status"] == "port_in_use"
    assert status["running"] is False
    assert status["managed"] is False


def test_start_listener_persists_meta_and_reports_running(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, chat_bridge_inbound_port=18081)

    monkeypatch.setattr(listener, "_is_process_alive", lambda pid: pid == 4321)

    state = {"calls": 0}

    def _fake_is_port_open(_host: str, _port: int) -> bool:
        state["calls"] += 1
        return state["calls"] >= 2

    monkeypatch.setattr(listener, "_is_port_open", _fake_is_port_open)
    monkeypatch.setattr(listener.time, "sleep", lambda _sec: None)
    monkeypatch.setattr(
        listener.subprocess,
        "Popen",
        lambda *args, **kwargs: _DummyProcess(4321),
    )

    result = listener.start_napcat_inbound_listener(settings=settings)

    assert result["running"] is True
    assert result["status"] == "running"
    assert result["pid"] == 4321
    assert result["action"] == "started"
    assert listener.napcat_inbound_listener_meta_path(settings).exists()


def test_stop_listener_clears_meta_file(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, chat_bridge_inbound_port=18082)
    listener._save_meta(
        settings,
        {
            "pid": 9999,
            "host": "127.0.0.1",
            "port": 18082,
            "command": "python -m deepcode serve",
            "started_at": "1700000000",
        },
    )

    monkeypatch.setattr(listener, "_is_process_alive", lambda _pid: False)
    monkeypatch.setattr(listener, "_is_port_open", lambda _host, _port: False)
    monkeypatch.setattr(listener.time, "sleep", lambda _sec: None)

    result = listener.stop_napcat_inbound_listener(settings=settings)

    assert result["status"] == "stopped"
    assert result["running"] is False
    assert result["action"] == "stopped"
    assert not listener.napcat_inbound_listener_meta_path(settings).exists()


def test_start_listener_with_override_port_ignores_default_port_listener(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, chat_bridge_inbound_port=8000)

    started = {"called": False}

    def _fake_is_port_open(_host: str, port: int) -> bool:
        # Simulate default API port occupied, but override target port initially not listening.
        return int(port) == 8000

    def _fake_popen(*args, **kwargs):
        started["called"] = True
        return _DummyProcess(5432)

    monkeypatch.setattr(listener, "_is_port_open", _fake_is_port_open)
    monkeypatch.setattr(listener, "_is_process_alive", lambda _pid: False)
    monkeypatch.setattr(listener.time, "sleep", lambda _sec: None)
    monkeypatch.setattr(listener.subprocess, "Popen", _fake_popen)

    result = listener.start_napcat_inbound_listener(settings=settings, port=18000)

    assert started["called"] is True
    assert int(result["port"]) == 18000
    assert result["action"] in {"started", "start_requested"}


def test_status_prefers_meta_port_when_port_not_specified(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, chat_bridge_inbound_port=8000)
    listener._save_meta(
        settings,
        {
            "pid": 2468,
            "host": "127.0.0.1",
            "port": 18000,
            "command": "python -m deepcode serve --port 18000",
            "started_at": "1700000000",
        },
    )

    monkeypatch.setattr(listener, "_is_process_alive", lambda pid: pid == 2468)
    monkeypatch.setattr(listener, "_is_port_open", lambda _host, port: int(port) == 18000)

    status = listener.get_napcat_inbound_listener_status(settings)

    assert status["running"] is True
    assert status["status"] == "running"
    assert int(status["port"]) == 18000


def test_stop_uses_meta_port_for_status_check(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, chat_bridge_inbound_port=8000)
    listener._save_meta(
        settings,
        {
            "pid": 1357,
            "host": "127.0.0.1",
            "port": 18000,
            "command": "python -m deepcode serve --port 18000",
            "started_at": "1700000000",
        },
    )

    monkeypatch.setattr(listener, "_is_process_alive", lambda _pid: False)
    # Simulate default 8000 is occupied, but managed 18000 is not listening.
    monkeypatch.setattr(listener, "_is_port_open", lambda _host, port: int(port) == 8000)
    monkeypatch.setattr(listener.time, "sleep", lambda _sec: None)

    result = listener.stop_napcat_inbound_listener(settings=settings)

    assert result["action"] == "stopped"
    assert result["status"] == "stopped"
    assert result["running"] is False
    assert int(result["port"]) == 18000


def test_is_process_alive_windows_uses_tasklist(monkeypatch):
    monkeypatch.setattr(listener.os, "name", "nt", raising=False)

    class _Result:
        def __init__(self, stdout: bytes):
            self.stdout = stdout

    monkeypatch.setattr(
        listener.subprocess,
        "run",
        lambda *args, **kwargs: _Result(b'"python.exe","1234","Console","1","10,000 K"\n'),
    )

    assert listener._is_process_alive(1234) is True
