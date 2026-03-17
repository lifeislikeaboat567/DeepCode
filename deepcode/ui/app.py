"""Streamlit Web UI for DeepCode Agent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure the package root is importable when running with `streamlit run`
_pkg_root = str(Path(__file__).parent.parent.parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

import streamlit as st  # noqa: E402

from deepcode import __version__  # noqa: E402
from deepcode.config import get_settings  # noqa: E402

# ─── Page config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="DeepCode Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _run_async(coro):
    """Run a coroutine from synchronous Streamlit code."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@st.cache_resource
def _get_agent():
    """Create and cache the agent instance."""
    from deepcode.agents.base import BaseAgent
    from deepcode.llm.factory import create_llm_client
    from deepcode.tools import CodeExecutorTool, FileManagerTool, ShellTool

    llm = create_llm_client()
    tools = [CodeExecutorTool(), FileManagerTool(), ShellTool()]
    return BaseAgent(llm=llm, tools=tools)


# ─── Sidebar ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.title("🤖 DeepCode Agent")
    st.caption(f"v{__version__}")
    st.divider()

    settings = get_settings()
    st.subheader("Configuration")
    st.info(
        f"**Provider:** {settings.llm_provider}\n\n"
        f"**Model:** {settings.llm_model}"
    )

    st.divider()
    page = st.radio(
        "Navigate",
        ["💬 Chat", "🚀 Run Task", "ℹ️ About"],
        label_visibility="collapsed",
    )

    if st.button("🗑️ Clear History", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


# ─── Chat page ────────────────────────────────────────────────────────────────

if page == "💬 Chat":
    st.header("💬 Chat with DeepCode Agent")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Display conversation history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            for artifact in msg.get("artifacts", []):
                lang = artifact.get("filename", "").rsplit(".", 1)[-1] or "python"
                with st.expander(f"📄 {artifact.get('filename', 'output')}"):
                    st.code(artifact.get("content", ""), language=lang)

    # Chat input
    if prompt := st.chat_input("Describe what you want to build or ask..."):
        st.session_state.messages.append({"role": "user", "content": prompt})

        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            placeholder = st.empty()
            full_response = ""

            try:
                agent = _get_agent()

                # Stream response
                async def _stream():
                    chunks = []
                    async for chunk in agent.stream_run(prompt):
                        chunks.append(chunk)
                    return "".join(chunks)

                full_response = _run_async(_stream())
                placeholder.markdown(full_response)

            except Exception as e:
                full_response = f"⚠️ Error: {e}"
                placeholder.error(full_response)

        st.session_state.messages.append(
            {"role": "assistant", "content": full_response, "artifacts": []}
        )


# ─── Run Task page ─────────────────────────────────────────────────────────────

elif page == "🚀 Run Task":
    st.header("🚀 Run Orchestrated Task")
    st.markdown(
        "Describe what you want to build. The multi-agent system will plan, "
        "code, review, and test it for you."
    )

    task = st.text_area(
        "Task description",
        placeholder="e.g. Write a Python class for a stack data structure with push, pop, and peek methods, including unit tests.",
        height=120,
    )

    if st.button("▶️ Run Task", type="primary", disabled=not task.strip()):
        from deepcode.agents.orchestrator import OrchestratorAgent
        from deepcode.llm.factory import create_llm_client
        from deepcode.tools import CodeExecutorTool, FileManagerTool, ShellTool

        with st.status("Running multi-agent workflow...", expanded=True) as status:
            try:
                llm = create_llm_client()
                tools = [CodeExecutorTool(), FileManagerTool(), ShellTool()]
                orchestrator = OrchestratorAgent(llm=llm, tools=tools)

                st.write("📋 Planning...")
                result = _run_async(orchestrator.run(task))

                status.update(
                    label="✅ Completed!" if result.success else "❌ Failed",
                    state="complete" if result.success else "error",
                )

                # Show plan
                if result.plan:
                    st.subheader("📋 Plan")
                    for i, step in enumerate(result.plan, 1):
                        st.markdown(f"{i}. {step}")

                # Show code artifacts
                if result.code_artifacts:
                    st.subheader("💻 Generated Code")
                    for artifact in result.code_artifacts:
                        lang = artifact.get("filename", "").rsplit(".", 1)[-1] or "python"
                        with st.expander(f"📄 {artifact.get('filename', 'output')}", expanded=True):
                            st.code(artifact.get("content", ""), language=lang)

                # Show review
                if result.review_result:
                    st.subheader("🔍 Code Review")
                    score = result.review_result.get("score", "N/A")
                    passed = result.review_result.get("passed", True)
                    col1, col2 = st.columns(2)
                    col1.metric("Review Score", f"{score}/10")
                    col2.metric("Status", "✅ Passed" if passed else "❌ Failed")

                    issues = result.review_result.get("issues", [])
                    if issues:
                        st.warning("**Issues found:**\n" + "\n".join(f"- {i}" for i in issues))

            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Task failed: {e}")


# ─── About page ───────────────────────────────────────────────────────────────

elif page == "ℹ️ About":
    st.header("ℹ️ About DeepCode Agent")

    st.markdown(
        f"""
## DeepCode Agent v{__version__}

An AI-powered software engineering assistant that can:

- 🧠 **Plan** complex development tasks
- 💻 **Generate** production-quality code
- 🔍 **Review** code for quality and security
- 🧪 **Test** generated code automatically
- 🔧 **Execute** code in a sandboxed environment

### Architecture

DeepCode Agent uses a multi-agent architecture:

| Agent | Role |
|-------|------|
| Orchestrator | Decomposes tasks and coordinates sub-agents |
| Coder | Generates and implements code |
| Reviewer | Audits code quality and security |
| Tester | Creates and runs unit tests |

### Getting Started

```bash
# Install
pip install -e .

# Configure
cp .env.example .env
# Edit .env with your API key

# CLI Chat
deepcode chat

# Run a task
deepcode run "Build a REST API for a todo list"

# Start API server
deepcode serve

# Launch Web UI
deepcode ui
```

### REST API

The API server provides endpoints for:
- `POST /api/v1/chat` – Single-turn chat
- `GET /api/v1/chat/stream` – Streaming chat
- `POST /api/v1/tasks` – Long-running tasks
- `POST /api/v1/sessions` – Session management

See `http://localhost:8000/docs` for the full API documentation.
        """
    )
