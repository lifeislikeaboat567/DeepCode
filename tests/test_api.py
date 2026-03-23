"""Integration tests for the FastAPI application."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except Exception:  # pragma: no cover - optional dependency fallback
    Ed25519PrivateKey = None

from deepcode.api.app import create_app
from deepcode.api.platform_inbound_debug import PlatformInboundDebugStore
from deepcode.config import Settings, load_chat_bridge_runtime_overrides
from deepcode.storage import SessionStore


def _qq_seed_bytes(secret: str) -> bytes:
    seed = secret.encode("utf-8")
    while len(seed) < 32:
        seed += seed
    return seed[:32]


def _qq_private_key(secret: str):
    if Ed25519PrivateKey is None:
        return None
    return Ed25519PrivateKey.from_private_bytes(_qq_seed_bytes(secret))


def _qq_sign_event_payload(secret: str, timestamp: str, raw_body: str) -> str:
    private_key = _qq_private_key(secret)
    if private_key is None:
        raise RuntimeError("cryptography is required for QQ signature tests")
    signature = private_key.sign(timestamp.encode("utf-8") + raw_body.encode("utf-8"))
    return signature.hex()


def _qq_sign_validation_payload(secret: str, event_ts: str, plain_token: str) -> str:
    private_key = _qq_private_key(secret)
    if private_key is None:
        raise RuntimeError("cryptography is required for QQ signature tests")
    signature = private_key.sign(f"{event_ts}{plain_token}".encode("utf-8"))
    return signature.hex()


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """Return a test client with the LLM mocked out."""
    from deepcode.llm.mock_client import MockLLMClient

    task_answer = json.dumps(
        {
            "thought": "done",
            "action": "final_answer",
            "action_input": {"answer": "Test response"},
        }
    )

    def _mock_chat_factory(*args, **kwargs):
        return MockLLMClient(responses=["Test response"])

    def _mock_task_factory(*args, **kwargs):
        return MockLLMClient(responses=[task_answer])

    bridge_db_url = f"sqlite+aiosqlite:///{tmp_path / 'platform_bridge_test.db'}"

    def _bridge_store_factory():
        return SessionStore(db_url=bridge_db_url)

    monkeypatch.setattr("deepcode.api.routes.chat.create_llm_client", _mock_chat_factory)
    monkeypatch.setattr("deepcode.api.platform_bridge.create_llm_client", _mock_chat_factory)
    monkeypatch.setattr("deepcode.api.platform_bridge.SessionStore", _bridge_store_factory)
    monkeypatch.setattr("deepcode.api.routes.tasks.create_llm_client", _mock_task_factory)
    monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _settings: None)

    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient):
        response = client.get("/api/v1/health")
        assert response.status_code == 200

    def test_health_response_structure(self, client: TestClient):
        data = client.get("/api/v1/health").json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "version" in data
        assert "llm_provider" in data


class TestSessionEndpoints:
    def test_create_session(self, client: TestClient):
        response = client.post("/api/v1/sessions", json={"name": "Test"})
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["name"] == "Test"

    def test_list_sessions(self, client: TestClient):
        client.post("/api/v1/sessions", json={"name": "S1"})
        client.post("/api/v1/sessions", json={"name": "S2"})
        response = client.get("/api/v1/sessions")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_get_session(self, client: TestClient):
        created = client.post("/api/v1/sessions", json={"name": "FetchMe"}).json()
        response = client.get(f"/api/v1/sessions/{created['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_get_nonexistent_session_returns_404(self, client: TestClient):
        response = client.get("/api/v1/sessions/does-not-exist")
        assert response.status_code == 404

    def test_delete_session(self, client: TestClient):
        created = client.post("/api/v1/sessions", json={}).json()
        delete_resp = client.delete(f"/api/v1/sessions/{created['id']}")
        assert delete_resp.status_code == 204

        get_resp = client.get(f"/api/v1/sessions/{created['id']}")
        assert get_resp.status_code == 404


class TestChatEndpoints:
    def test_chat_returns_response_and_persists_messages(self, client: TestClient):
        response = client.post("/api/v1/chat", json={"message": "hello"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["message"] == "Test response"
        assert payload["success"] is True

        session = client.get(f"/api/v1/sessions/{payload['session_id']}")
        assert session.status_code == 200
        messages = session.json()["messages"]
        assert [item["role"] for item in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "hello"
        assert messages[1]["content"] == "Test response"

    def test_chat_stream_returns_sse_and_persists_messages(self, client: TestClient):
        with client.stream("GET", "/api/v1/chat/stream", params={"message": "stream this"}) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

        events = [
            json.loads(line[len("data: ") :])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        event_types = [str(item.get("type", "")) for item in events]

        assert event_types[0] == "start"
        assert "chunk" in event_types
        assert event_types[-1] == "done"
        assert events[0].get("payload", {}).get("mode") == "ask"

        chunk_payloads = [item.get("payload", {}) for item in events if item.get("type") == "chunk"]
        assert all(isinstance(payload, dict) and "content" in payload for payload in chunk_payloads)
        assert "Test response" in str(events[-1].get("payload", {}).get("message", ""))

    def test_chat_agent_mode_returns_react_payload(self, client: TestClient):
        response = client.post("/api/v1/chat", json={"message": "auto handle this", "mode": "agent"})
        assert response.status_code == 200

        payload = response.json()
        assert payload["mode"] == "agent"
        assert payload["message"] == "Test response"
        assert isinstance(payload["steps"], list)
        assert len(payload["steps"]) >= 1
        assert payload["steps"][0]["action"] == "final_answer"
        assert isinstance(payload.get("agent_context"), dict)
        assert "intent_route" in payload["agent_context"]

    def test_chat_agent_mode_plan_only_returns_structured_context(self, client: TestClient):
        response = client.post(
            "/api/v1/chat",
            json={"message": "plan first", "mode": "agent", "plan_only": True},
        )
        assert response.status_code == 200

        payload = response.json()
        assert payload["mode"] == "agent"
        assert payload["steps"] == []
        assert "Plan-Only mode" in str(payload["message"])
        assert isinstance(payload.get("agent_context"), dict)
        assert "decomposed_task" in payload["agent_context"]

    def test_chat_stream_agent_mode_returns_sse(self, client: TestClient):
        with client.stream(
            "GET",
            "/api/v1/chat/stream",
            params={"message": "agent stream", "mode": "agent"},
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

        events = [
            json.loads(line[len("data: ") :])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        event_types = [str(item.get("type", "")) for item in events]

        assert event_types[0] == "start"
        assert events[0].get("payload", {}).get("mode") == "agent"
        assert "agent_context" in event_types
        assert "final_answer" in event_types
        assert event_types[-1] == "done"

        final_event = next(item for item in events if item.get("type") == "final_answer")
        assert "Test response" in str(final_event.get("payload", {}).get("answer", ""))
        assert "Test response" in str(events[-1].get("payload", {}).get("message", ""))

    def test_chat_stream_agent_mode_plan_only_returns_sse(self, client: TestClient):
        with client.stream(
            "GET",
            "/api/v1/chat/stream",
            params={"message": "plan only", "mode": "agent", "plan_only": True},
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

        events = [
            json.loads(line[len("data: ") :])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        event_types = [str(item.get("type", "")) for item in events]

        assert event_types[0] == "start"
        assert events[0].get("payload", {}).get("plan_only") is True
        assert "agent_context" in event_types
        assert "final_answer" in event_types
        assert event_types[-1] == "done"
        final_event = next(item for item in events if item.get("type") == "final_answer")
        assert "Plan-Only mode" in str(final_event.get("payload", {}).get("answer", ""))


class TestTaskEndpoints:
    def test_create_task(self, client: TestClient):
        response = client.post("/api/v1/tasks", json={"task": "Generate hello world"})
        assert response.status_code == 202
        data = response.json()
        assert "task_id" in data
        assert data["task"] == "Generate hello world"
        assert "task_state" in data
        assert "observations" in data
        assert "reflections" in data
        assert "errors" in data
        assert "execution_results" in data

    def test_list_tasks(self, client: TestClient):
        client.post("/api/v1/tasks", json={"task": "Task one"})
        client.post("/api/v1/tasks", json={"task": "Task two"})
        response = client.get("/api/v1/tasks")
        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload, list)
        assert len(payload) >= 2

    def test_get_and_delete_task(self, client: TestClient):
        created = client.post("/api/v1/tasks", json={"task": "Task to remove"}).json()
        task_id = created["task_id"]

        get_resp = client.get(f"/api/v1/tasks/{task_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["task_id"] == task_id

        delete_resp = client.delete(f"/api/v1/tasks/{task_id}")
        assert delete_resp.status_code == 204

        missing_resp = client.get(f"/api/v1/tasks/{task_id}")
        assert missing_resp.status_code == 404


class TestPlatformBridgeEndpoints:
    def test_platform_bridge_local_help_command_bypasses_llm(self, client: TestClient, monkeypatch):
        def _should_not_call_llm(*args, **kwargs):
            raise AssertionError("LLM should not be called for local command")

        monkeypatch.setattr("deepcode.api.platform_bridge.create_llm_client", _should_not_call_llm)

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-help-user-{uniq}",
                "channel_id": f"local-help-room-{uniq}",
                "message_id": f"local-help-msg-{uniq}",
                "text": "/help",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert "可用本地指令" in payload["reply_text"]

    def test_platform_bridge_local_config_set_and_show(self, client: TestClient, monkeypatch, tmp_path: Path):
        settings = Settings(
            data_dir=tmp_path,
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_inbound_port=8000,
            chat_bridge_verify_token="",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        uniq = str(time.time_ns())
        set_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-cfg-user-{uniq}",
                "channel_id": f"local-cfg-room-{uniq}",
                "message_id": f"local-cfg-msg-set-{uniq}",
                "text": "/config set chat_bridge_inbound_port 19000",
            },
        )
        assert set_resp.status_code == 200
        assert "chat_bridge_inbound_port = 19000" in set_resp.json()["reply_text"]

        show_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-cfg-user-{uniq}",
                "channel_id": f"local-cfg-room-{uniq}",
                "message_id": f"local-cfg-msg-show-{uniq}",
                "text": "/config show chat_bridge_inbound_port",
            },
        )
        assert show_resp.status_code == 200
        assert "chat_bridge_inbound_port = 19000" in show_resp.json()["reply_text"]

        overrides = load_chat_bridge_runtime_overrides(settings=settings)
        assert int(overrides["chat_bridge_inbound_port"]) == 19000

        runtime_set_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-cfg-user-{uniq}",
                "channel_id": f"local-cfg-room-{uniq}",
                "message_id": f"local-cfg-msg-llm-set-{uniq}",
                "text": "/config set llm_model gpt-4o",
            },
        )
        assert runtime_set_resp.status_code == 200
        assert "配置已更新(persisted): llm_model = gpt-4o" in runtime_set_resp.json()["reply_text"]

        runtime_show_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-cfg-user-{uniq}",
                "channel_id": f"local-cfg-room-{uniq}",
                "message_id": f"local-cfg-msg-llm-show-{uniq}",
                "text": "/config show llm_model",
            },
        )
        assert runtime_show_resp.status_code == 200
        assert "llm_model = gpt-4o" in runtime_show_resp.json()["reply_text"]

        overrides = load_chat_bridge_runtime_overrides(settings=settings)
        assert overrides.get("llm_model") == "gpt-4o"

    def test_platform_bridge_local_skill_commands(self, client: TestClient, monkeypatch, tmp_path: Path):
        settings = Settings(
            data_dir=tmp_path,
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
        )
        skills_dir = settings.data_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skills_dir / "demo_skill.md"
        skill_file.write_text("# Demo Skill\n用于本地命令测试\n", encoding="utf-8")

        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)
        monkeypatch.setattr("deepcode.extensions.skill_registry.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.extensions.skill_toggle_store.get_settings", lambda: settings)

        uniq = str(time.time_ns())
        list_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-skill-user-{uniq}",
                "channel_id": f"local-skill-room-{uniq}",
                "message_id": f"local-skill-msg-list-{uniq}",
                "text": "/skill list",
            },
        )
        assert list_resp.status_code == 200
        assert "demo_skill [enabled]" in list_resp.json()["reply_text"]

        disable_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-skill-user-{uniq}",
                "channel_id": f"local-skill-room-{uniq}",
                "message_id": f"local-skill-msg-disable-{uniq}",
                "text": "/skill disable demo_skill",
            },
        )
        assert disable_resp.status_code == 200
        assert "技能已禁用" in disable_resp.json()["reply_text"]

        disabled_list_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-skill-user-{uniq}",
                "channel_id": f"local-skill-room-{uniq}",
                "message_id": f"local-skill-msg-list-disabled-{uniq}",
                "text": "/skill list disabled",
            },
        )
        assert disabled_list_resp.status_code == 200
        assert "demo_skill [disabled]" in disabled_list_resp.json()["reply_text"]

        uninstall_resp = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-skill-user-{uniq}",
                "channel_id": f"local-skill-room-{uniq}",
                "message_id": f"local-skill-msg-uninstall-{uniq}",
                "text": "/skill uninstall demo_skill",
            },
        )
        assert uninstall_resp.status_code == 200
        assert "技能已卸载" in uninstall_resp.json()["reply_text"]
        assert not skill_file.exists()

    def test_platform_bridge_challenge_echo(self, client: TestClient):
        response = client.post("/api/v1/platforms/feishu/events", json={"challenge": "verify-me"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "challenge"
        assert payload["challenge"] == "verify-me"

    def test_platform_bridge_processes_generic_message(self, client: TestClient):
        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"user-1-{uniq}",
                "channel_id": f"room-1-{uniq}",
                "message_id": f"msg-1-{uniq}",
                "text": "hello from generic bridge",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["event_type"] == "message"
        assert payload["mode"] == "ask"
        assert payload["reply_text"] == "Test response"
        assert payload["session_id"]

    def test_platform_bridge_uses_non_empty_fallback_when_model_returns_empty(self, client: TestClient, monkeypatch):
        from deepcode.llm.mock_client import MockLLMClient

        monkeypatch.setattr(
            "deepcode.api.platform_bridge.create_llm_client",
            lambda: MockLLMClient(responses=[""]),
        )

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"user-empty-{uniq}",
                "channel_id": f"room-empty-{uniq}",
                "message_id": f"msg-empty-{uniq}",
                "text": "hello",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert "模型返回了空内容" in payload["reply_text"]

    def test_platform_bridge_reuses_session_for_same_user_and_channel(self, client: TestClient):
        uniq = str(time.time_ns())
        user_id = f"user-2-{uniq}"
        channel_id = f"room-2-{uniq}"
        first = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": user_id,
                "channel_id": channel_id,
                "message_id": f"msg-a-{uniq}",
                "text": "first turn",
            },
        )
        second = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": user_id,
                "channel_id": channel_id,
                "message_id": f"msg-b-{uniq}",
                "text": "second turn",
            },
        )
        assert first.status_code == 200
        assert second.status_code == 200
        first_payload = first.json()
        second_payload = second.json()
        assert first_payload["session_id"] == second_payload["session_id"]
        assert first_payload["event_type"] == "message"
        assert second_payload["event_type"] == "message"

    def test_platform_bridge_new_command_resets_bound_session(self, client: TestClient):
        uniq = str(time.time_ns())
        user_id = f"user-new-{uniq}"
        channel_id = f"room-new-{uniq}"

        first = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": user_id,
                "channel_id": channel_id,
                "message_id": f"msg-new-a-{uniq}",
                "text": "first turn",
            },
        )
        assert first.status_code == 200
        first_session_id = first.json()["session_id"]

        reset = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": user_id,
                "channel_id": channel_id,
                "message_id": f"msg-new-b-{uniq}",
                "text": "/new",
            },
        )
        assert reset.status_code == 200
        reset_payload = reset.json()
        assert "已开启新对话" in reset_payload["reply_text"]
        second_session_id = reset_payload["session_id"]
        assert second_session_id != first_session_id

        follow_up = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": user_id,
                "channel_id": channel_id,
                "message_id": f"msg-new-c-{uniq}",
                "text": "after reset",
            },
        )
        assert follow_up.status_code == 200
        assert follow_up.json()["session_id"] == second_session_id

    def test_platform_bridge_newchat_with_content_creates_new_session_and_continues(
        self,
        client: TestClient,
    ):
        uniq = str(time.time_ns())
        user_id = f"user-newchat-{uniq}"
        channel_id = f"room-newchat-{uniq}"

        first = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": user_id,
                "channel_id": channel_id,
                "message_id": f"msg-newchat-a-{uniq}",
                "text": "hello",
            },
        )
        assert first.status_code == 200
        first_session_id = first.json()["session_id"]

        second = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": user_id,
                "channel_id": channel_id,
                "message_id": f"msg-newchat-b-{uniq}",
                "text": "/newchat continue in fresh context",
            },
        )
        assert second.status_code == 200
        payload = second.json()
        assert payload["session_id"] != first_session_id
        assert payload["reply_text"] == "Test response"

    def test_platform_bridge_local_inbound_status_command_bypasses_llm(self, client: TestClient, monkeypatch):
        def _should_not_call_llm(*args, **kwargs):
            raise AssertionError("LLM should not be called for local inbound command")

        monkeypatch.setattr("deepcode.api.platform_bridge.create_llm_client", _should_not_call_llm)
        monkeypatch.setattr(
            "deepcode.api.platform_local_commands.get_napcat_inbound_listener_status",
            lambda settings: {
                "status": "running",
                "running": True,
                "managed": True,
                "host": "127.0.0.1",
                "port": 18000,
                "pid": 12345,
                "process_alive": True,
                "listening": True,
                "started_at": "1700000000",
                "command": "python -m deepcode serve",
            },
        )

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-inbound-user-{uniq}",
                "channel_id": f"local-inbound-room-{uniq}",
                "message_id": f"local-inbound-msg-{uniq}",
                "text": "/inbound status",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert "入站监听状态" in payload["reply_text"]
        assert '"status": "running"' in payload["reply_text"]

    def test_platform_bridge_local_mode_command_updates_default_mode(self, client: TestClient, monkeypatch, tmp_path: Path):
        settings = Settings(
            data_dir=tmp_path,
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_default_mode="ask",
            chat_bridge_verify_token="",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-mode-user-{uniq}",
                "channel_id": f"local-mode-room-{uniq}",
                "message_id": f"local-mode-msg-{uniq}",
                "text": "/mode agent",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert "chat_bridge_default_mode = agent" in payload["reply_text"]
        assert settings.chat_bridge_default_mode == "agent"

    def test_platform_bridge_local_config_profile_use_switches_model_profile(
        self,
        client: TestClient,
        monkeypatch,
        tmp_path: Path,
    ):
        settings = Settings(
            data_dir=tmp_path,
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        model_profile_file = settings.data_dir / "ui_model_config.json"
        model_profile_file.parent.mkdir(parents=True, exist_ok=True)
        model_profile_file.write_text(
            json.dumps(
                {
                    "active_profile_id": "profile-default",
                    "profiles": [
                        {
                            "id": "profile-default",
                            "name": "默认配置",
                            "llm_provider": "openai",
                            "llm_model": "gpt-4o-mini",
                            "llm_base_url": "",
                            "llm_temperature": 0.0,
                            "llm_max_tokens": 4096,
                            "llm_enable_thinking": False,
                            "persist_api_key": False,
                            "llm_api_key": "",
                        },
                        {
                            "id": "profile-fast",
                            "name": "快速模型",
                            "llm_provider": "mock",
                            "llm_model": "mock-fast",
                            "llm_base_url": "",
                            "llm_temperature": 0.1,
                            "llm_max_tokens": 2048,
                            "llm_enable_thinking": False,
                            "persist_api_key": False,
                            "llm_api_key": "",
                        },
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"local-profile-user-{uniq}",
                "channel_id": f"local-profile-room-{uniq}",
                "message_id": f"local-profile-msg-{uniq}",
                "text": "/config profile use profile-fast",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert "已切换模型配置档" in payload["reply_text"]
        assert settings.llm_provider == "mock"
        assert settings.llm_model == "mock-fast"

        overrides = load_chat_bridge_runtime_overrides(settings=settings)
        assert overrides.get("llm_provider") == "mock"
        assert overrides.get("llm_model") == "mock-fast"

    def test_platform_bridge_plan_command_switches_to_agent_plan_only(self, client: TestClient):
        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"user-plan-{uniq}",
                "channel_id": f"room-plan-{uniq}",
                "message_id": f"msg-plan-{uniq}",
                "text": "/plan draft integration milestones",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["mode"] == "agent"
        assert payload["plan_only"] is True
        assert "Plan-Only mode" in payload["reply_text"]

    def test_platform_bridge_rejects_invalid_shared_token(self, client: TestClient, monkeypatch):
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="expected-token",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)

        response = client.post(
            "/api/v1/platforms/generic/events",
            headers={"X-DeepCode-Bridge-Token": "wrong-token"},
            json={
                "user_id": "user-token",
                "channel_id": "room-token",
                "message_id": "msg-token",
                "text": "hello",
            },
        )

        assert response.status_code == 401
        assert "Invalid bridge token" in response.json()["detail"]

    def test_platform_bridge_feishu_signature_validation(self, client: TestClient, monkeypatch):
        secret = "feishu-secret"
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_feishu_encrypt_key=secret,
            chat_bridge_signature_ttl_seconds=300,
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)

        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
        signature = base64.b64encode(
            hmac.new(secret.encode("utf-8"), string_to_sign, hashlib.sha256).digest()
        ).decode("utf-8")

        response = client.post(
            "/api/v1/platforms/feishu/events",
            headers={
                "Content-Type": "application/json",
                "X-Lark-Request-Timestamp": timestamp,
                "X-Lark-Signature": signature,
            },
            content='{"challenge":"signed-verify"}',
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "challenge"
        assert payload["challenge"] == "signed-verify"

    def test_platform_bridge_rejects_invalid_feishu_signature(self, client: TestClient, monkeypatch):
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_feishu_encrypt_key="feishu-secret",
            chat_bridge_signature_ttl_seconds=300,
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)

        response = client.post(
            "/api/v1/platforms/feishu/events",
            headers={
                "Content-Type": "application/json",
                "X-Lark-Request-Timestamp": str(int(time.time())),
                "X-Lark-Signature": "invalid",
            },
            content='{"challenge":"signed-verify"}',
        )

        assert response.status_code == 401
        assert "signature" in response.json()["detail"].lower()

    def test_platform_bridge_wechat_signature_validation(self, client: TestClient, monkeypatch):
        token = "wechat-token"
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_wechat_token=token,
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)

        timestamp = "1730000000"
        nonce = "888"
        uniq = str(time.time_ns())
        signature = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode("utf-8")).hexdigest()
        response = client.post(
            "/api/v1/platforms/wechat/events",
            json={
            "FromUserName": f"wx-user-1-{uniq}",
                "ToUserName": "wx-bot",
                "Content": "hello from wx",
            "MsgId": f"wx-msg-1-{uniq}",
                "timestamp": timestamp,
                "nonce": nonce,
                "signature": signature,
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert payload["reply_text"] == "Test response"

    def test_platform_bridge_qq_signature_validation(self, client: TestClient, monkeypatch):
        if Ed25519PrivateKey is None:
            pytest.skip("cryptography is required for QQ signature tests")

        secret = "qq-secret"
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_qq_signing_secret=secret,
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        uniq = str(time.time_ns())
        raw = (
            "{"
            f"\"user_id\":\"qq-user-1-{uniq}\","
            f"\"channel_id\":\"qq-group-{uniq}\","
            f"\"message_id\":\"qq-msg-1-{uniq}\","
            "\"message\":\"hello from qq\""
            "}"
        )
        timestamp = str(int(time.time()))
        signature = _qq_sign_event_payload(secret, timestamp, raw)
        response = client.post(
            "/api/v1/platforms/qq/events",
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": signature,
                "X-Signature-Timestamp": timestamp,
            },
            content=raw,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert payload["reply_text"] == "Test response"

    def test_platform_bridge_qq_napcat_accepts_query_access_token(self, client: TestClient, monkeypatch):
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_qq_delivery_mode="napcat",
            chat_bridge_qq_napcat_webhook_token="napcat-secret",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/qq/events?access_token=napcat-secret",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": f"qq-user-{uniq}",
                "message_id": f"qq-msg-{uniq}",
                "raw_message": "hello from napcat",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert payload["message_kind"] == "private"

    def test_platform_bridge_qq_napcat_rejects_missing_token_before_payload_parse(self, client: TestClient, monkeypatch):
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_qq_delivery_mode="napcat",
            chat_bridge_qq_napcat_webhook_token="napcat-secret",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        response = client.post(
            "/api/v1/platforms/qq/events",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": "qq-user-no-token",
                "message_id": "qq-msg-no-token",
                "raw_message": "hello from napcat",
            },
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Missing NapCat webhook token"

    def test_platform_bridge_qq_napcat_skips_official_signature_when_webhook_token_empty(
        self,
        client: TestClient,
        monkeypatch,
    ):
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_qq_delivery_mode="napcat",
            chat_bridge_qq_signing_secret="qq-official-secret",
            chat_bridge_qq_napcat_webhook_token="",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/qq/events",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": f"qq-user-{uniq}",
                "message_id": f"qq-msg-{uniq}",
                "raw_message": "hello from napcat without webhook token",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"

    def test_platform_bridge_rejects_when_inbound_callbacks_disabled(self, client: TestClient, monkeypatch):
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_inbound_enabled=False,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        response = client.post(
            "/api/v1/platforms/qq/events",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": "qq-user-disabled",
                "message_id": "qq-msg-disabled",
                "raw_message": "hello",
            },
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Platform bridge inbound callbacks are disabled"

    def test_platform_bridge_qq_napcat_debug_log_records_request_and_response(
        self,
        client: TestClient,
        monkeypatch,
        tmp_path,
    ):
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_inbound_enabled=True,
            chat_bridge_inbound_debug=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_qq_delivery_mode="napcat",
            chat_bridge_qq_napcat_webhook_token="",
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        store = PlatformInboundDebugStore(file_path=str(tmp_path / "platform_inbound_debug.log"))
        monkeypatch.setattr("deepcode.api.routes.platforms.PlatformInboundDebugStore", lambda: store)

        response = client.post(
            "/api/v1/platforms/qq/events",
            json={
                "post_type": "message",
                "message_type": "private",
                "user_id": "qq-user-debug",
                "message_id": "qq-msg-debug",
                "raw_message": "hello debug",
            },
        )

        assert response.status_code == 200
        events = store.list_recent(limit=5)
        assert len(events) >= 1

        post_event = next((item for item in events if item.method == "POST"), events[0])
        assert post_event.platform == "qq"
        assert "hello debug" in post_event.request_body
        assert post_event.response_status == 200
        assert '"ok":true' in post_event.response_body

        async_events = [item for item in events if item.method == "ASYNC"]
        assert len(async_events) >= 1

    def test_platform_bridge_qq_callback_url_validation_op13(self, client: TestClient, monkeypatch):
        if Ed25519PrivateKey is None:
            pytest.skip("cryptography is required for QQ signature tests")

        secret = "qq-secret"
        settings = Settings(
            chat_bridge_enabled=True,
            chat_bridge_allowed_platforms="generic,qq,wechat,feishu",
            chat_bridge_verify_token="",
            chat_bridge_qq_signing_secret=secret,
        )
        monkeypatch.setattr("deepcode.api.routes.platforms.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode.api.routes.platforms.apply_chat_bridge_runtime_overrides", lambda _: None)

        plain_token = "token-verify-123"
        event_ts = "1725442341"
        raw = (
            "{"
            "\"op\":13,"
            f"\"d\":{{\"plain_token\":\"{plain_token}\",\"event_ts\":\"{event_ts}\"}}"
            "}"
        )

        timestamp = str(int(time.time()))
        signature = _qq_sign_event_payload(secret, timestamp, raw)
        response = client.post(
            "/api/v1/platforms/qq/events",
            headers={
                "Content-Type": "application/json",
                "X-Signature-Ed25519": signature,
                "X-Signature-Timestamp": timestamp,
            },
            content=raw,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["plain_token"] == plain_token
        assert payload["signature"] == _qq_sign_validation_payload(secret, event_ts, plain_token)

    def test_platform_bridge_feishu_message_kind_and_response_payload(self, client: TestClient):
        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/feishu/events",
            json={
                "header": {
                    "event_id": f"feishu-event-{uniq}",
                    "event_type": "im.message.receive_v1",
                },
                "event": {
                    "sender": {
                        "sender_id": {
                            "open_id": f"ou_{uniq}",
                        }
                    },
                    "message": {
                        "message_id": f"om_{uniq}",
                        "chat_id": f"oc_{uniq}",
                        "message_type": "interactive",
                        "content": json.dumps({"text": "hello from interactive card"}),
                    },
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert payload["message_kind"] == "interactive"
        assert payload["platform_event_id"] == f"feishu-event-{uniq}"
        assert payload["platform_response"]["receive_id_type"] == "chat_id"
        assert payload["platform_response"]["receive_id"] == f"oc_{uniq}"
        assert payload["platform_response"]["msg_type"] == "text"

    def test_platform_bridge_feishu_event_id_is_idempotent(self, client: TestClient):
        uniq = str(time.time_ns())
        event_id = f"feishu-dedupe-{uniq}"
        body = {
            "header": {
                "event_id": event_id,
                "event_type": "im.message.receive_v1",
            },
            "event": {
                "sender": {
                    "sender_id": {
                        "open_id": f"ou-user-{uniq}",
                    }
                },
                "message": {
                    "message_id": f"om-msg-{uniq}",
                    "chat_id": f"oc-chat-{uniq}",
                    "message_type": "text",
                    "content": json.dumps({"text": "hello dedupe"}),
                },
            },
        }
        first = client.post("/api/v1/platforms/feishu/events", json=body)
        second = client.post("/api/v1/platforms/feishu/events", json=body)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["event_type"] == "message"
        assert second.json()["event_type"] == "duplicate"
        assert second.json()["platform_event_id"] == event_id

    def test_platform_bridge_wechat_xml_message_returns_xml_reply(self, client: TestClient):
        uniq = str(time.time_ns())
        raw_xml = (
            "<xml>"
            f"<ToUserName><![CDATA[wx-bot-{uniq}]]></ToUserName>"
            f"<FromUserName><![CDATA[wx-user-{uniq}]]></FromUserName>"
            "<CreateTime>1730000000</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            f"<Content><![CDATA[/ask hello xml {uniq}]]></Content>"
            f"<MsgId>{uniq}</MsgId>"
            "</xml>"
        )
        response = client.post(
            "/api/v1/platforms/wechat/events",
            headers={"Content-Type": "application/xml"},
            content=raw_xml,
        )

        assert response.status_code == 200
        assert "application/xml" in response.headers.get("content-type", "")
        assert "<xml>" in response.text
        assert "<Content><![CDATA[Test response]]></Content>" in response.text
        assert f"<ToUserName><![CDATA[wx-user-{uniq}]]></ToUserName>" in response.text
        assert f"<FromUserName><![CDATA[wx-bot-{uniq}]]></FromUserName>" in response.text

    def test_platform_bridge_wechat_json_event_protocol_supported(self, client: TestClient):
        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/wechat/events",
            json={
                "FromUserName": f"wx-user-event-{uniq}",
                "ToUserName": f"wx-bot-event-{uniq}",
                "MsgType": "event",
                "Event": "subscribe",
                "EventKey": "qrscene_abc",
                "id": f"wx-event-id-{uniq}",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert payload["message_kind"] == "event"
        assert payload["platform_event_id"] == f"wx-event-id-{uniq}"
        assert payload["platform_response"]["reply_json"]["msgtype"] == "text"

    def test_platform_bridge_qq_gateway_event_mapping(self, client: TestClient):
        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/qq/events",
            json={
                "id": f"qq-event-{uniq}",
                "t": "GROUP_AT_MESSAGE_CREATE",
                "d": {
                    "id": f"qq-message-{uniq}",
                    "channel_id": f"qq-channel-{uniq}",
                    "author": {"id": f"qq-user-{uniq}"},
                    "content": "/ask hello from qq gateway",
                },
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert payload["message_kind"] == "group_at_message_create"
        assert payload["platform_event_id"] == f"qq-event-{uniq}"
        assert payload["platform_response"]["event_type"] == "group_at_message_create"

    def test_platform_bridge_includes_delivery_result(self, client: TestClient, monkeypatch):
        async def _mock_delivery(*args, **kwargs):
            return {
                "enabled": True,
                "attempted": True,
                "sent": True,
                "platform": "generic",
                "status_code": 200,
            }

        monkeypatch.setattr(
            "deepcode.api.routes.platforms.send_platform_callback_if_configured",
            _mock_delivery,
        )

        uniq = str(time.time_ns())
        response = client.post(
            "/api/v1/platforms/generic/events",
            json={
                "user_id": f"delivery-user-{uniq}",
                "channel_id": f"delivery-room-{uniq}",
                "message_id": f"delivery-msg-{uniq}",
                "text": "hello delivery",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["event_type"] == "message"
        assert payload["platform_response"]["delivery"]["attempted"] is True
        assert payload["platform_response"]["delivery"]["sent"] is True
