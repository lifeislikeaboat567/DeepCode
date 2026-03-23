"""Unit tests for OpenAI client compatibility behaviors."""

from __future__ import annotations

from types import SimpleNamespace
import sys

import pytest

from deepcode.llm.base import LLMMessage
from deepcode.llm.openai_client import OpenAIClient


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            class _Stream:
                def __aiter__(self_inner):
                    chunk = SimpleNamespace(
                        choices=[
                            SimpleNamespace(delta=SimpleNamespace(content="chunk"))
                        ]
                    )

                    async def _gen():
                        yield chunk

                    return _gen()

            return _Stream()

        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model=str(kwargs.get("model", "fake-model")),
        )


class _FakeAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeToolCallCompletions:
    async def create(self, **kwargs):
        function_payload = SimpleNamespace(
            name="shell",
            arguments='{"command":"echo hello"}',
        )
        tool_call = SimpleNamespace(function=function_payload)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, tool_calls=[tool_call]),
                    finish_reason="tool_calls",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model=str(kwargs.get("model", "fake-model")),
        )


class _FakeToolCallAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.completions = _FakeToolCallCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeDictToolCallCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "file_manager",
                                    "arguments": '{"action":"list","path":"."}',
                                }
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model=str(kwargs.get("model", "fake-model")),
        )


class _FakeDictToolCallAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.completions = _FakeDictToolCallCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeReasoningCompletions:
    async def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=None, reasoning_content="reasoning fallback content"),
                    finish_reason="stop",
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model=str(kwargs.get("model", "fake-model")),
        )


class _FakeReasoningAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.completions = _FakeReasoningCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class _FakeNestedTextCompletions:
    async def create(self, **kwargs):
        nested_item = {
            "type": "output_text",
            "text": {"value": "nested text value content"},
        }
        return SimpleNamespace(
            choices=[
                {
                    "message": {
                        "content": [nested_item],
                    },
                    "finish_reason": "stop",
                }
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model=str(kwargs.get("model", "fake-model")),
        )


class _FakeNestedTextAsyncOpenAI:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.completions = _FakeNestedTextCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.fixture
def fake_openai_module(monkeypatch: pytest.MonkeyPatch):
    fake_module = SimpleNamespace(AsyncOpenAI=_FakeAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)
    return fake_module


@pytest.mark.asyncio
async def test_qwen_non_stream_sets_enable_thinking_false(fake_openai_module):
    client = OpenAIClient(
        api_key="test-key",
        model="qwen-max",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    response = await client.complete([LLMMessage.user("hello")])

    assert response.content == "ok"
    assert client._client.completions.calls
    first_call = client._client.completions.calls[0]
    assert first_call.get("extra_body") == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_non_qwen_non_stream_has_no_extra_body(fake_openai_module):
    client = OpenAIClient(
        api_key="test-key",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
    )

    _ = await client.complete([LLMMessage.user("hello")])

    assert client._client.completions.calls
    first_call = client._client.completions.calls[0]
    assert "extra_body" not in first_call


@pytest.mark.asyncio
async def test_qwen_stream_uses_enable_thinking_config(fake_openai_module):
    client = OpenAIClient(
        api_key="test-key",
        model="qwen-max",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        enable_thinking=True,
    )

    chunks = []
    async for chunk in client.stream_complete([LLMMessage.user("hello")]):
        chunks.append(chunk)

    assert chunks == ["chunk"]
    assert client._client.completions.calls
    stream_call = client._client.completions.calls[0]
    assert stream_call.get("stream") is True
    assert stream_call.get("extra_body") == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_complete_normalizes_empty_content_tool_calls(monkeypatch: pytest.MonkeyPatch):
    fake_module = SimpleNamespace(AsyncOpenAI=_FakeToolCallAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    client = OpenAIClient(
        api_key="test-key",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
    )

    response = await client.complete([LLMMessage.user("hello")])

    assert '"function_call"' in response.content
    assert '"name": "shell"' in response.content
    assert '"command": "echo hello"' in response.content


@pytest.mark.asyncio
async def test_complete_normalizes_dict_style_tool_calls(monkeypatch: pytest.MonkeyPatch):
    fake_module = SimpleNamespace(AsyncOpenAI=_FakeDictToolCallAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    client = OpenAIClient(
        api_key="test-key",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
    )

    response = await client.complete([LLMMessage.user("hello")])

    assert '"function_call"' in response.content
    assert '"name": "file_manager"' in response.content
    assert '"action": "list"' in response.content


@pytest.mark.asyncio
async def test_complete_falls_back_to_reasoning_content(monkeypatch: pytest.MonkeyPatch):
    fake_module = SimpleNamespace(AsyncOpenAI=_FakeReasoningAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    client = OpenAIClient(
        api_key="test-key",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
    )

    response = await client.complete([LLMMessage.user("hello")])

    assert response.content == "reasoning fallback content"


@pytest.mark.asyncio
async def test_complete_reads_nested_text_value_content(monkeypatch: pytest.MonkeyPatch):
    fake_module = SimpleNamespace(AsyncOpenAI=_FakeNestedTextAsyncOpenAI)
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    client = OpenAIClient(
        api_key="test-key",
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
    )

    response = await client.complete([LLMMessage.user("hello")])

    assert response.content == "nested text value content"


@pytest.mark.asyncio
async def test_glm_non_stream_sets_thinking_disabled_when_not_enabled(fake_openai_module):
    client = OpenAIClient(
        api_key="test-key",
        model="glm-4.7-flash",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        enable_thinking=False,
    )

    _ = await client.complete([LLMMessage.user("hello")])

    assert client._client.completions.calls
    first_call = client._client.completions.calls[0]
    assert first_call.get("extra_body") == {"thinking": {"type": "disabled"}}


@pytest.mark.asyncio
async def test_glm_stream_sets_thinking_enabled_when_configured(fake_openai_module):
    client = OpenAIClient(
        api_key="test-key",
        model="glm-4.7-flash",
        base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        enable_thinking=True,
    )

    chunks = []
    async for chunk in client.stream_complete([LLMMessage.user("hello")]):
        chunks.append(chunk)

    assert chunks == ["chunk"]
    assert client._client.completions.calls
    stream_call = client._client.completions.calls[0]
    assert stream_call.get("extra_body") == {"thinking": {"type": "enabled"}}
