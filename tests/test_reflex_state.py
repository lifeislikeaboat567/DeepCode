"""Regression tests for DeepCode Reflex chat state."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from deepcode.exceptions import TaskNotFoundError
from deepcode.storage import TaskStore
from deepcode.storage.session_store import Message, SessionStore
from deepcode_reflex.state import (
    UIState,
    _apply_runtime_model_config,
    _compress_session_context_if_needed,
    _build_default_model_profile,
    _load_ui_runtime_flags,
    _create_draft_task,
    _normalize_model_profile,
    _parse_local_chat_command,
    _prepare_task_record_for_run,
    _save_ui_runtime_flags,
    _session_message_id,
)


@pytest.fixture
def store(tmp_session_db: str) -> SessionStore:
    return SessionStore(db_url=tmp_session_db)


@pytest.fixture
def task_store(tmp_session_db: str) -> TaskStore:
    return TaskStore(db_url=tmp_session_db)


def _build_state(session_id: str) -> UIState:
    state = object.__new__(UIState)
    object.__setattr__(state, "dirty_vars", set())
    object.__setattr__(state, "dirty_substates", set())
    object.__setattr__(state, "busy", False)
    object.__setattr__(state, "selected_session_id", session_id)
    object.__setattr__(state, "chat_edit_message_id", "")
    object.__setattr__(state, "chat_edit_prompt", "")
    object.__setattr__(state, "chat_plan_only", "disabled")
    object.__setattr__(state, "heartbeat_enabled", "enabled")
    object.__setattr__(state, "chat_messages", [])
    object.__setattr__(state, "chat_agent_traces", {})
    object.__setattr__(state, "chat_stop_requested", False)
    object.__setattr__(state, "error_message", "")
    object.__setattr__(state, "ui_language", "zh")
    object.__setattr__(state, "sessions", [])
    object.__setattr__(state, "pinned_session_ids", [])
    object.__setattr__(state, "collapsed_session_group_keys", [])
    object.__setattr__(state, "session_delete_confirm_id", "")
    object.__setattr__(state, "session_delete_confirm_group_key", "")
    object.__setattr__(state, "session_delete_confirm_group_title", "")
    object.__setattr__(state, "session_delete_confirm_group_count", 0)
    object.__setattr__(state, "session_action_open_id", "")
    object.__setattr__(state, "session_rename_id", "")
    object.__setattr__(state, "session_rename_value", "")
    return state


class TestChatEditFlow:
    @pytest.mark.asyncio
    async def test_start_edit_preserves_following_messages(self, monkeypatch, store: SessionStore):
        session = await store.create(name="Edit Session")
        first_user = Message(role="user", content="first")
        first_assistant = Message(role="assistant", content="reply")
        second_user = Message(role="user", content="later")
        session.messages = [first_user, first_assistant, second_user]
        await store.update(session)

        monkeypatch.setattr("deepcode_reflex.state._session_store", lambda: store)

        state = _build_state(session.id)
        message_id = _session_message_id(first_user)

        await state.start_edit_user_message(message_id)

        fetched = await store.get(session.id)
        assert [message.content for message in fetched.messages] == ["first", "reply", "later"]
        assert state.chat_edit_message_id == message_id
        assert state.chat_edit_prompt == "first"

    @pytest.mark.asyncio
    async def test_resend_edited_message_requires_event_dispatch(self, monkeypatch, store: SessionStore):
        session = await store.create(name="Edit Session")
        first_user = Message(role="user", content="first")
        first_assistant = Message(role="assistant", content="reply")
        second_user = Message(role="user", content="later")
        session.messages = [first_user, first_assistant, second_user]
        await store.update(session)

        monkeypatch.setattr("deepcode_reflex.state._session_store", lambda: store)

        state = _build_state(session.id)
        state.chat_edit_message_id = _session_message_id(first_user)
        state.chat_edit_prompt = "first updated"

        with pytest.raises(RuntimeError, match="Cannot directly call background task"):
            async for _ in state.resend_edited_message():
                pass


class TestChatSessionGrouping:
    def test_chat_session_groups_bucket_and_collapse_rows(self):
        state = _build_state("session-a")
        now = datetime.now()
        object.__setattr__(
            state,
            "sessions",
            [
                {
                    "id": "session-a",
                    "name": "今天的会话",
                    "messages": "4",
                    "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                {
                    "id": "session-b",
                    "name": "昨天的会话",
                    "messages": "2",
                    "updated_at": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                },
            ],
        )
        object.__setattr__(state, "collapsed_session_group_keys", ["today"])

        groups = state.chat_session_groups

        assert len(groups) == 2
        assert groups[0]["key"] == "today"
        assert groups[0]["collapsed"] == "1"
        assert groups[0]["count"] == "1"
        assert groups[0]["items"][0]["id"] == "session-a"

    def test_toggle_chat_context_updates_message_and_trace_map(self):
        state = _build_state("session-a")
        object.__setattr__(
            state,
            "chat_messages",
            [
                {
                    "id": "assistant-1",
                    "trace_context_collapsed": "0",
                    "trace_collapsed": "1",
                }
            ],
        )
        object.__setattr__(
            state,
            "chat_agent_traces",
            {
                "assistant-1": {
                    "trace_context_collapsed": "0",
                    "trace_collapsed": "1",
                }
            },
        )

        state.toggle_chat_context("assistant-1")

        assert state.chat_messages[0]["trace_context_collapsed"] == "1"
        assert state.chat_agent_traces["assistant-1"]["trace_context_collapsed"] == "1"

    def test_set_chat_plan_only_accepts_chinese_labels(self):
        state = _build_state("session-a")

        state.set_chat_plan_only("仅输出计划")
        assert state.chat_plan_only == "enabled"
        assert state.chat_plan_only_label == "仅输出计划"

        state.set_chat_plan_only("完整回复")
        assert state.chat_plan_only == "disabled"
        assert state.chat_plan_only_label == "完整回复"

    @pytest.mark.asyncio
    async def test_load_selected_session_messages_defaults_context_card_to_collapsed(
        self, monkeypatch, store: SessionStore
    ):
        session = await store.create(name="Trace Session")
        user = Message(role="user", content="hi")
        assistant = Message(role="assistant", content="hello")
        session.messages = [user, assistant]
        assistant_id = _session_message_id(assistant)
        session.metadata = {
            "agent_runs": [
                {
                    "assistant_message_id": assistant_id,
                    "trace_reason": "step-1",
                    "trace_collapsed": "1",
                }
            ]
        }
        await store.update(session)

        monkeypatch.setattr("deepcode_reflex.state._session_store", lambda: store)
        state = _build_state(session.id)

        await state._load_selected_session_messages()

        loaded_assistant = next(item for item in state.chat_messages if item["id"] == assistant_id)
        assert loaded_assistant["trace_context_collapsed"] == "1"


class _SummaryLLM:
    async def complete(self, messages):
        return SimpleNamespace(content="压缩摘要：保留关键目标与待办")


class TestContextCompression:
    @pytest.mark.asyncio
    async def test_compress_session_context_when_threshold_exceeded(self, store: SessionStore):
        session = await store.create(name="Compress Session")
        session.messages = [
            Message(role="user", content="需求描述 " + ("A" * 200)),
            Message(role="assistant", content="中间分析 " + ("B" * 200)),
            Message(role="user", content="继续执行 " + ("C" * 200)),
            Message(role="assistant", content="执行结果 " + ("D" * 200)),
            Message(role="user", content="补充要求 " + ("E" * 200)),
            Message(role="assistant", content="确认计划 " + ("F" * 200)),
        ]

        changed = await _compress_session_context_if_needed(
            session,
            _SummaryLLM(),
            token_threshold=80,
            keep_recent_messages=2,
            language="zh",
        )

        assert changed is True
        assert len(session.messages) == 4
        assert session.messages[0].role == "system"
        assert "历史摘要" in session.messages[0].content
        assert isinstance(session.metadata.get("context_summaries"), list)


class TestSessionDeleteMemoryCleanup:
    @pytest.mark.asyncio
    async def test_delete_session_by_id_also_cleans_task_memory(self, monkeypatch, store: SessionStore):
        session = await store.create(name="Delete Session")

        class _MemorySpy:
            def __init__(self) -> None:
                self.deleted: list[str] = []

            def delete_session_entries(self, session_id: str) -> int:
                self.deleted.append(session_id)
                return 1

        memory_spy = _MemorySpy()

        monkeypatch.setattr("deepcode_reflex.state._session_store", lambda: store)
        monkeypatch.setattr("deepcode_reflex.state._task_memory_store", lambda: memory_spy)

        state = _build_state(session.id)
        await state.delete_session_by_id(session.id)

        assert memory_spy.deleted == [session.id]


class TestTaskDraftFlow:
    @pytest.mark.asyncio
    async def test_create_draft_task_uses_placeholder_title(self, task_store: TaskStore):
        created = await _create_draft_task(task_store, "zh")

        assert created.task == "新任务"
        assert created.status == "pending"
        assert created.metadata["draft"] is True

    @pytest.mark.asyncio
    async def test_prepare_task_record_for_run_reuses_selected_draft(self, task_store: TaskStore):
        created = await _create_draft_task(task_store, "zh")

        updated, execution_input = await _prepare_task_record_for_run(
            task_store,
            created.id,
            "实现登录接口",
            "zh",
        )

        fetched = await task_store.get(created.id)
        assert updated.id == created.id
        assert execution_input == "实现登录接口"
        assert fetched.task == "实现登录接口"
        assert fetched.metadata["draft"] is False
        assert fetched.metadata["task_history"][-1] == "实现登录接口"

    @pytest.mark.asyncio
    async def test_prepare_task_record_for_run_builds_followup_prompt_for_existing_task(self, task_store: TaskStore):
        created = await task_store.create(task="实现登录接口", metadata={"origin": "web_reflex", "draft": False})
        created.summary = "已有初版接口"
        created.plan = ["创建 API", "补充测试"]
        created.code_artifacts = [{"filename": "app.py", "content": "print('ok')"}]
        await task_store.update(created)

        updated, execution_input = await _prepare_task_record_for_run(
            task_store,
            created.id,
            "补充权限校验",
            "zh",
        )

        fetched = await task_store.get(created.id)
        assert updated.id == created.id
        assert fetched.task == "实现登录接口"
        assert "实现登录接口" in execution_input
        assert "补充权限校验" in execution_input
        assert "已有初版接口" in execution_input
        assert "app.py" in execution_input


class TestModelThinkingConfig:
    def test_normalize_model_profile_parses_thinking_and_persist_flags(self):
        settings = SimpleNamespace(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_base_url="",
            llm_temperature=0.0,
            llm_max_tokens=4096,
            llm_enable_thinking=False,
        )
        fallback = _build_default_model_profile(settings, profile_id="profile-default", name="默认配置")

        normalized = _normalize_model_profile(
            {
                "id": "profile-1",
                "name": "测试配置",
                "llm_enable_thinking": "true",
                "persist_api_key": "false",
                "llm_api_key": "should-not-persist",
            },
            fallback=fallback,
            index=0,
        )

        assert normalized["llm_enable_thinking"] is True
        assert normalized["persist_api_key"] is False
        assert normalized["llm_api_key"] == ""

    def test_apply_runtime_model_config_updates_thinking_flag(self, monkeypatch: pytest.MonkeyPatch):
        settings = SimpleNamespace(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_base_url="",
            llm_temperature=0.0,
            llm_max_tokens=4096,
            llm_enable_thinking=False,
            llm_api_key="",
        )
        monkeypatch.setattr("deepcode_reflex.state.get_settings", lambda: settings)

        _apply_runtime_model_config({"llm_enable_thinking": "enabled"})

        assert settings.llm_enable_thinking is True


class TestHeartbeatSwitch:
    def test_ui_runtime_flags_roundtrip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        settings = SimpleNamespace(data_dir=tmp_path, ensure_data_dir=lambda: tmp_path.mkdir(parents=True, exist_ok=True))
        monkeypatch.setattr("deepcode_reflex.state.get_settings", lambda: settings)

        _save_ui_runtime_flags({"heartbeat_enabled": False})
        loaded = _load_ui_runtime_flags()

        assert loaded.get("heartbeat_enabled") is False

    @pytest.mark.asyncio
    async def test_bootstrap_prefers_persisted_heartbeat_flag(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        settings = SimpleNamespace(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_base_url="",
            llm_api_key="",
            llm_temperature=0.0,
            llm_max_tokens=4096,
            llm_enable_thinking=False,
            ui_heartbeat_enabled=True,
            data_dir=tmp_path,
            ensure_data_dir=lambda: tmp_path.mkdir(parents=True, exist_ok=True),
        )
        monkeypatch.setattr("deepcode_reflex.state.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode_reflex.state._apply_saved_model_overrides", lambda: None)

        async def _noop_refresh() -> None:
            return None

        state = _build_state("session-a")
        object.__setattr__(state, "refresh_data", _noop_refresh)

        _save_ui_runtime_flags({"heartbeat_enabled": False})
        await state.bootstrap()

        assert state.heartbeat_enabled == "disabled"


class TestPlatformBridgeInboundSettings:
    def test_refresh_platform_bridge_fields_loads_inbound_settings(self, monkeypatch: pytest.MonkeyPatch):
        settings = SimpleNamespace(
            chat_bridge_enabled=True,
            chat_bridge_inbound_enabled=False,
            chat_bridge_inbound_port=18000,
            chat_bridge_inbound_debug=True,
        )
        monkeypatch.setattr("deepcode_reflex.state.get_settings", lambda: settings)
        monkeypatch.setattr("deepcode_reflex.state.load_chat_bridge_runtime_overrides", lambda settings=None: {})
        monkeypatch.setattr("deepcode_reflex.state.apply_chat_bridge_runtime_overrides", lambda settings=None, overrides=None: {})

        state = _build_state("session-a")
        state._refresh_platform_bridge_fields()

        assert state.platform_bridge_enabled == "enabled"
        assert state.platform_bridge_inbound_enabled == "disabled"
        assert state.platform_bridge_inbound_port == "18000"
        assert state.platform_bridge_inbound_debug == "enabled"
        assert state.platform_bridge_inbound_callback_url.endswith(":18000/api/v1/platforms/qq/events")

    def test_refresh_platform_bridge_inbound_logs_maps_rows(self, monkeypatch: pytest.MonkeyPatch):
        event = SimpleNamespace(
            timestamp=datetime.now(),
            platform="qq",
            method="POST",
            url="http://127.0.0.1:18000/api/v1/platforms/qq/events",
            path="/api/v1/platforms/qq/events",
            client="127.0.0.1:54321",
            response_status=200,
            request_body='{"raw_message":"hello"}',
            response_body='{"ok":true}',
            headers={"content-type": "application/json"},
            query={"access_token": "***"},
        )
        monkeypatch.setattr(
            "deepcode_reflex.state._platform_inbound_debug_store",
            lambda: SimpleNamespace(list_recent=lambda limit=30: [event], clear=lambda: None),
        )

        state = _build_state("session-a")
        state._refresh_platform_bridge_inbound_logs()

        assert len(state.platform_bridge_inbound_logs) == 1
        assert state.platform_bridge_inbound_logs[0]["client"] == "127.0.0.1:54321"
        assert "hello" in state.platform_bridge_inbound_logs[0]["request_body"]
        assert '"ok":true' in state.platform_bridge_inbound_logs[0]["response_body"]


class TestLocalChatCommands:
    def test_parse_local_chat_command(self):
        assert _parse_local_chat_command("hello") is None
        assert _parse_local_chat_command("/skills") == ("skills_list", "")
        assert _parse_local_chat_command("/skills show browser") == ("skills_show", "browser")

    def test_resolve_local_chat_command_lists_skills_with_status(self, monkeypatch: pytest.MonkeyPatch):
        state = _build_state("session-a")
        fake_skills = [
            SimpleNamespace(name="alpha", path="/tmp/alpha.md", description="A skill", tags=["core"]),
            SimpleNamespace(name="beta", path="/tmp/beta.md", description="B skill", tags=[]),
        ]

        monkeypatch.setattr("deepcode_reflex.state.SkillRegistry", lambda: SimpleNamespace(discover=lambda: fake_skills))
        monkeypatch.setattr(
            "deepcode_reflex.state._skill_toggle_store",
            lambda: SimpleNamespace(load=lambda: {"/tmp/alpha.md": True, "/tmp/beta.md": False}),
        )

        result = state._resolve_local_chat_command("/skills")

        assert result is not None
        assert "| alpha | 已加载 | A skill |" in result["content"]
        assert "| beta | 已禁用 | B skill |" in result["content"]

    def test_resolve_local_chat_command_show_details(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        state = _build_state("session-a")
        skill_path = tmp_path / "browser.md"
        skill_path.write_text("# Browser Skill\nUse browser actions.\n", encoding="utf-8")
        fake_skills = [
            SimpleNamespace(name="browser", path=str(skill_path), description="Browser helper", tags=["web"]),
        ]

        monkeypatch.setattr("deepcode_reflex.state.SkillRegistry", lambda: SimpleNamespace(discover=lambda: fake_skills))
        monkeypatch.setattr(
            "deepcode_reflex.state._skill_toggle_store",
            lambda: SimpleNamespace(load=lambda: {str(skill_path): True}),
        )

        result = state._resolve_local_chat_command("/skills show browser")

        assert result is not None
        assert "技能详情：browser" in result["content"]
        assert "# Browser Skill" in result["content"]


def _build_extension_state() -> UIState:
    state = object.__new__(UIState)
    object.__setattr__(state, "dirty_vars", set())
    object.__setattr__(state, "dirty_substates", set())
    object.__setattr__(state, "ui_language", "zh")
    object.__setattr__(state, "pinned_task_ids", [])
    object.__setattr__(state, "collapsed_task_group_keys", [])
    object.__setattr__(state, "task_delete_confirm_group_key", "")
    object.__setattr__(state, "task_delete_confirm_group_title", "")
    object.__setattr__(state, "task_delete_confirm_group_count", 0)
    object.__setattr__(state, "task_delete_confirm_id", "")
    object.__setattr__(state, "task_action_open_id", "")
    object.__setattr__(state, "task_rename_id", "")
    object.__setattr__(state, "task_rename_value", "")
    object.__setattr__(state, "selected_task_id", "")
    object.__setattr__(state, "tasks", [])
    object.__setattr__(state, "skills", [])
    object.__setattr__(state, "skill_search_query", "")
    object.__setattr__(state, "skill_page", 1)
    object.__setattr__(state, "skill_page_size", "8")
    object.__setattr__(state, "skill_sort_by", "installed_at")
    object.__setattr__(state, "clawhub_query", "")
    object.__setattr__(state, "clawhub_source_url", "https://clawhub.ai")
    object.__setattr__(state, "clawhub_selected_slug", "")
    object.__setattr__(state, "clawhub_preview_query", "")
    object.__setattr__(state, "clawhub_preview_candidate_count", "")
    object.__setattr__(state, "clawhub_preview_name", "")
    object.__setattr__(state, "clawhub_preview_version", "")
    object.__setattr__(state, "clawhub_preview_score", "")
    object.__setattr__(state, "clawhub_preview_summary", "")
    object.__setattr__(state, "clawhub_preview_package_name", "")
    object.__setattr__(state, "clawhub_preview_install_dir", "")
    object.__setattr__(state, "clawhub_preview_text", "")
    object.__setattr__(state, "clawhub_panel_hint", "")
    object.__setattr__(state, "selected_skill_path", "")
    object.__setattr__(state, "skill_delete_confirm_path", "")
    object.__setattr__(state, "skill_delete_confirm_name", "")
    object.__setattr__(state, "extension_detail_kind", "")
    object.__setattr__(state, "error_message", "")
    object.__setattr__(state, "heartbeat_enabled", "enabled")
    return state


class TestExtensionPanelState:
    def test_filtered_skill_rows_support_sort_and_pagination(self):
        state = _build_extension_state()
        object.__setattr__(
            state,
            "skills",
            [
                {"name": "zeta", "path": "/tmp/zeta.md", "description": "", "tags": "", "installed_at": "2026-03-18 10:00:00", "enabled": "enabled"},
                {"name": "alpha", "path": "/tmp/alpha.md", "description": "", "tags": "", "installed_at": "2026-03-19 10:00:00", "enabled": "enabled"},
                {"name": "beta", "path": "/tmp/beta.md", "description": "", "tags": "", "installed_at": "2026-03-20 10:00:00", "enabled": "enabled"},
            ],
        )
        state.skill_page_size = "2"
        state.skill_sort_by = "name"

        assert [row["name"] for row in state.paginated_skill_rows] == ["alpha", "beta"]

        state.next_skill_page()

        assert [row["name"] for row in state.paginated_skill_rows] == ["zeta"]

    def test_task_groups_bucket_and_collapse_rows(self):
        state = _build_extension_state()
        now = datetime.now()
        object.__setattr__(
            state,
            "tasks",
            [
                {
                    "id": "task-today",
                    "task": "今天的任务",
                    "status": "done",
                    "artifacts": "0",
                    "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                },
                {
                    "id": "task-yesterday",
                    "task": "昨天的任务",
                    "status": "pending",
                    "artifacts": "0",
                    "updated_at": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                },
            ],
        )
        object.__setattr__(state, "collapsed_task_group_keys", ["today"])

        groups = state.task_groups

        assert len(groups) == 2
        assert groups[0]["key"] == "today"
        assert groups[0]["collapsed"] == "1"
        assert groups[0]["items"][0]["id"] == "task-today"
        assert groups[1]["key"] == "yesterday"

    def test_selected_skill_markdown_reads_file(self, tmp_path: Path):
        state = _build_extension_state()
        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# Browser Skill\nUse it.\n", encoding="utf-8")
        object.__setattr__(
            state,
            "skills",
            [
                {
                    "name": "browser-skill",
                    "path": str(skill_path),
                    "description": "Browser skill",
                    "tags": "browser",
                    "enabled": "enabled",
                }
            ],
        )
        object.__setattr__(state, "selected_skill_path", str(skill_path))

        assert state.selected_skill_markdown.startswith("# Browser Skill")

    @pytest.mark.asyncio
    async def test_delete_task_group_by_key_removes_all_group_members(self, monkeypatch, task_store: TaskStore):
        state = _build_extension_state()
        now = datetime.now()
        today_stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        older_stamp = (now - timedelta(days=40)).strftime("%Y-%m-%d %H:%M:%S")
        today = await task_store.create(task="今天任务", metadata={"origin": "web_reflex"})
        today.updated_at = now
        await task_store.update(today)

        older = await task_store.create(task="旧任务", metadata={"origin": "web_reflex"})
        older.updated_at = now - timedelta(days=40)
        await task_store.update(older)

        object.__setattr__(
            state,
            "tasks",
            [
                {
                    "id": today.id,
                    "task": today.task,
                    "status": today.status,
                    "artifacts": "0",
                    "updated_at": today_stamp,
                },
                {
                    "id": older.id,
                    "task": older.task,
                    "status": older.status,
                    "artifacts": "0",
                    "updated_at": older_stamp,
                },
            ],
        )
        object.__setattr__(state, "task_delete_confirm_group_title", "今天")
        object.__setattr__(state, "task_delete_confirm_group_key", "today")
        object.__setattr__(state, "task_delete_confirm_group_count", 1)

        async def _noop_refresh() -> None:
            return None

        monkeypatch.setattr("deepcode_reflex.state._task_store", lambda: task_store)
        object.__setattr__(state, "_refresh_tasks_only", _noop_refresh)

        await state.delete_task_group_by_key("today")

        with pytest.raises(TaskNotFoundError):
            await task_store.get(today.id)
        assert await task_store.get(older.id) is not None

    @pytest.mark.asyncio
    async def test_run_clawhub_auto_install_dry_run_sets_preview(self, monkeypatch: pytest.MonkeyPatch):
        state = _build_extension_state()
        state.clawhub_query = "browser"
        state.clawhub_source_url = "https://clawhub.ai"

        async def _fake_search(source_url: str, **kwargs: object):
            assert source_url == "https://clawhub.ai"
            assert kwargs["query"] == "browser"
            return [
                {"slug": "starter-browser", "name": "Starter Browser", "score": 0.2},
                {"slug": "agent-browser", "name": "Agent Browser", "score": 0.9},
            ]

        async def _fake_details(source_url: str, *, slug: str):
            assert source_url == "https://clawhub.ai"
            assert slug == "agent-browser"
            return {
                "name": "Agent Browser",
                "summary": "Headless browser automation",
                "version": "1.4.0",
            }

        async def _should_not_install(*args: object, **kwargs: object):
            raise AssertionError("install_skill_from_clawhub must not run in dry_run mode")

        monkeypatch.setattr("deepcode_reflex.state.search_clawhub_skills", _fake_search)
        monkeypatch.setattr("deepcode_reflex.state.get_clawhub_skill_details", _fake_details)
        monkeypatch.setattr("deepcode_reflex.state.install_skill_from_clawhub", _should_not_install)

        toast_event = await state.run_clawhub_auto_install(True)

        assert toast_event is not None
        assert state.clawhub_selected_slug == "agent-browser"
        assert state.clawhub_preview_name == "Agent Browser"
        assert state.clawhub_preview_version == "1.4.0"
        assert state.clawhub_preview_score == "0.900"
        assert "Agent Browser" in state.clawhub_preview_text
        assert "Headless browser automation" in state.clawhub_preview_text

    @pytest.mark.asyncio
    async def test_run_clawhub_auto_install_accepts_direct_slug_query(self, monkeypatch: pytest.MonkeyPatch):
        state = _build_extension_state()
        state.clawhub_query = "slug:agent-browser"
        state.clawhub_source_url = "https://clawhub.ai"

        async def _should_not_search(*args: object, **kwargs: object):
            raise AssertionError("search_clawhub_skills must not run for direct slug input")

        async def _fake_details(source_url: str, *, slug: str):
            assert source_url == "https://clawhub.ai"
            assert slug == "agent-browser"
            return {
                "name": "Agent Browser",
                "summary": "Headless browser automation",
                "version": "1.4.0",
            }

        monkeypatch.setattr("deepcode_reflex.state.search_clawhub_skills", _should_not_search)
        monkeypatch.setattr("deepcode_reflex.state.get_clawhub_skill_details", _fake_details)

        toast_event = await state.run_clawhub_auto_install(True)

        assert toast_event is not None
        assert state.clawhub_selected_slug == "agent-browser"
        assert state.clawhub_preview_name == "Agent Browser"
        assert state.clawhub_panel_hint == ""

    @pytest.mark.asyncio
    async def test_run_clawhub_auto_install_sets_rate_limit_hint(self, monkeypatch: pytest.MonkeyPatch):
        state = _build_extension_state()
        state.clawhub_query = "browser automation"
        state.clawhub_source_url = "https://clawhub.ai"

        async def _rate_limited(*args: object, **kwargs: object):
            raise RuntimeError("Rate limited by remote source (HTTP 429) for https://clawhub.ai/api/v1/search")

        monkeypatch.setattr("deepcode_reflex.state.search_clawhub_skills", _rate_limited)

        toast_event = await state.run_clawhub_auto_install(True)

        assert toast_event is not None
        assert "slug:agent-browser" in state.clawhub_panel_hint
        assert "详情页链接" in state.clawhub_panel_hint

    @pytest.mark.asyncio
    async def test_delete_requested_skill_removes_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        state = _build_extension_state()
        skill_path = tmp_path / "cleanup.md"
        skill_path.write_text("# Cleanup\n", encoding="utf-8")
        state.skill_delete_confirm_path = str(skill_path)
        state.skill_delete_confirm_name = "cleanup"
        state.selected_skill_path = str(skill_path)

        class _FakeToggleStore:
            def load(self):
                return {str(skill_path): True}

            def save(self, flags):
                self.flags = flags

        async def _noop_refresh() -> None:
            return None

        monkeypatch.setattr(
            "deepcode_reflex.state.SkillRegistry",
            lambda: SimpleNamespace(discover=lambda: [SimpleNamespace(name="cleanup", path=str(skill_path))]),
        )
        monkeypatch.setattr("deepcode_reflex.state._skill_toggle_store", lambda: _FakeToggleStore())
        monkeypatch.setattr("deepcode_reflex.state._clear_agent_cache", lambda: None)
        object.__setattr__(state, "refresh_data", _noop_refresh)

        toast_event = await state.delete_requested_skill()

        assert toast_event is not None
        assert not skill_path.exists()
        assert state.skill_delete_confirm_path == ""
        assert state.selected_skill_path == ""


