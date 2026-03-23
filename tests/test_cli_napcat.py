"""CLI tests for quick NapCat inbound listener command."""

from __future__ import annotations

from click.testing import CliRunner

from deepcode.cli import main


def test_napcat_command_starts_with_short_port_option(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_start(_settings, port=None):
        captured["port"] = port
        return {
            "action": "started",
            "running": True,
            "host": "127.0.0.1",
            "port": int(port or 8000),
            "pid": 1234,
        }

    monkeypatch.setattr("deepcode.cli.get_settings", lambda: object())
    monkeypatch.setattr("deepcode.cli.start_napcat_inbound_listener", _fake_start)

    runner = CliRunner()
    result = runner.invoke(main, ["napcat", "-p", "18000"])

    assert result.exit_code == 0
    assert captured["port"] == 18000
    assert "start action" in result.output.lower()


def test_napcat_command_starts_with_positional_port(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_start(_settings, port=None):
        captured["port"] = port
        return {
            "action": "started",
            "running": True,
            "host": "127.0.0.1",
            "port": int(port or 8000),
            "pid": 1234,
        }

    monkeypatch.setattr("deepcode.cli.get_settings", lambda: object())
    monkeypatch.setattr("deepcode.cli.start_napcat_inbound_listener", _fake_start)

    runner = CliRunner()
    result = runner.invoke(main, ["napcat", "19000"])

    assert result.exit_code == 0
    assert captured["port"] == 19000


def test_napcat_command_rejects_conflicting_ports(monkeypatch):
    monkeypatch.setattr("deepcode.cli.get_settings", lambda: object())
    monkeypatch.setattr(
        "deepcode.cli.start_napcat_inbound_listener",
        lambda _settings, port=None: {
            "action": "started",
            "running": True,
            "host": "127.0.0.1",
            "port": int(port or 8000),
            "pid": 1234,
        },
    )

    runner = CliRunner()
    result = runner.invoke(main, ["napcat", "18000", "-p", "19000"])

    assert result.exit_code != 0
    assert "conflicts" in result.output.lower()
