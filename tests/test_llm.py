"""Unit tests for the LLM client layer."""

from __future__ import annotations

import pytest

from deepcode.llm.base import LLMMessage, LLMResponse
from deepcode.llm.mock_client import MockLLMClient


class TestLLMMessage:
    def test_system_factory(self):
        msg = LLMMessage.system("You are helpful")
        assert msg.role == "system"
        assert msg.content == "You are helpful"

    def test_user_factory(self):
        msg = LLMMessage.user("Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_assistant_factory(self):
        msg = LLMMessage.assistant("Hi there")
        assert msg.role == "assistant"
        assert msg.content == "Hi there"


class TestMockLLMClient:
    @pytest.mark.asyncio
    async def test_complete_returns_default_when_no_responses(self):
        client = MockLLMClient()
        response = await client.complete([LLMMessage.user("Hello")])
        assert isinstance(response, LLMResponse)
        assert response.content
        assert response.model == "mock-model"

    @pytest.mark.asyncio
    async def test_complete_returns_preconfigured_responses_in_order(self):
        client = MockLLMClient(responses=["first", "second", "third"])
        messages = [LLMMessage.user("test")]

        r1 = await client.complete(messages)
        r2 = await client.complete(messages)
        r3 = await client.complete(messages)
        r4 = await client.complete(messages)  # beyond list -> default

        assert r1.content == "first"
        assert r2.content == "second"
        assert r3.content == "third"
        assert "Mock response" in r4.content

    @pytest.mark.asyncio
    async def test_stream_complete_yields_chunks(self):
        client = MockLLMClient(responses=["hello world"])
        chunks = []
        async for chunk in client.stream_complete([LLMMessage.user("hi")]):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert "hello" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_call_count_tracks_invocations(self):
        client = MockLLMClient()
        await client.complete([LLMMessage.user("1")])
        await client.complete([LLMMessage.user("2")])
        assert client._call_count == 2
