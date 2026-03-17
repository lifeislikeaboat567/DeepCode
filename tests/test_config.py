"""Application configuration tests."""

from __future__ import annotations

from deepcode.config import Settings


class TestSettings:
    def test_default_provider_is_openai(self):
        s = Settings(llm_api_key="test-key")
        assert s.llm_provider == "openai"

    def test_mock_provider_requires_no_api_key(self):
        s = Settings(llm_provider="mock", llm_api_key="")
        assert s.llm_provider == "mock"

    def test_allowed_shell_commands_parses_comma_list(self):
        s = Settings(allowed_shells="ls,cat,grep")
        cmds = s.allowed_shell_commands
        assert "ls" in cmds
        assert "cat" in cmds
        assert "grep" in cmds
        assert len(cmds) == 3

    def test_resolved_db_url_uses_data_dir_when_empty(self, tmp_path):
        s = Settings(data_dir=tmp_path, db_url="")
        assert str(tmp_path) in s.resolved_db_url
        assert "deepcode.db" in s.resolved_db_url

    def test_resolved_db_url_uses_explicit_url(self):
        s = Settings(db_url="sqlite+aiosqlite:///custom.db")
        assert s.resolved_db_url == "sqlite+aiosqlite:///custom.db"

    def test_ensure_data_dir_creates_directory(self, tmp_path):
        target = tmp_path / "new_dir" / "nested"
        s = Settings(data_dir=target)
        s.ensure_data_dir()
        assert target.exists()

    def test_tilde_in_data_dir_is_expanded(self):
        s = Settings(data_dir="~/some_path")
        assert "~" not in str(s.data_dir)
