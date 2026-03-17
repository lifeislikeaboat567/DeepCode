"""Command-line interface for DeepCode Agent."""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax

from deepcode import __version__
from deepcode.config import get_settings
from deepcode.logging_config import configure_logging

console = Console()


# ─── Main group ───────────────────────────────────────────────────────────────

@click.group()
@click.version_option(version=__version__, prog_name="deepcode")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def main(debug: bool) -> None:
    """DeepCode Agent – AI-powered software engineering assistant."""
    configure_logging(debug=debug)


# ─── chat command ─────────────────────────────────────────────────────────────

@main.command()
@click.option("--session", "-s", default=None, help="Continue an existing session by ID")
@click.option("--stream/--no-stream", default=True, help="Stream the agent's thinking")
def chat(session: str | None, stream: bool) -> None:
    """Start an interactive chat session with DeepCode Agent.

    Type your request and press Enter. Use 'exit' or Ctrl-C to quit.
    """
    console.print(
        Panel.fit(
            f"[bold blue]DeepCode Agent[/] v{__version__}\n"
            "[dim]Type your coding request. 'exit' to quit.[/]",
            border_style="blue",
        )
    )

    asyncio.run(_chat_loop(session_id=session, stream=stream))


async def _chat_loop(session_id: str | None, stream: bool) -> None:
    from deepcode.agents.base import BaseAgent
    from deepcode.llm.factory import create_llm_client
    from deepcode.tools import CodeExecutorTool, FileManagerTool, ShellTool

    llm = create_llm_client()
    tools = [CodeExecutorTool(), FileManagerTool(), ShellTool()]
    agent = BaseAgent(llm=llm, tools=tools)

    while True:
        try:
            user_input = console.input("\n[bold green]You>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Goodbye![/]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "q"):
            console.print("[dim]Goodbye![/]")
            break

        console.print()

        if stream:
            async for chunk in agent.stream_run(user_input):
                # Replace escaped newlines
                console.print(chunk.replace("\\n", "\n"), end="")
            console.print()
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
                transient=True,
            ) as progress:
                progress.add_task("Thinking...", total=None)
                result = await agent.run(user_input)

            console.print(Markdown(result.answer))

            for artifact in result.code_artifacts:
                lang = artifact.get("filename", "").rsplit(".", 1)[-1] or "python"
                console.print(
                    Panel(
                        Syntax(artifact.get("content", ""), lang, theme="monokai"),
                        title=f"[cyan]{artifact.get('filename')}[/]",
                        border_style="cyan",
                    )
                )


# ─── run command ──────────────────────────────────────────────────────────────

@main.command()
@click.argument("task")
@click.option("--stream/--no-stream", default=False, help="Stream the agent's thinking")
def run(task: str, stream: bool) -> None:
    """Run a single task with the orchestrated multi-agent workflow.

    TASK is the natural language description of what to build.

    Example: deepcode run "Write a Python function to parse CSV files"
    """
    asyncio.run(_run_task(task=task, stream=stream))


async def _run_task(task: str, stream: bool) -> None:
    from deepcode.agents.orchestrator import OrchestratorAgent
    from deepcode.llm.factory import create_llm_client
    from deepcode.tools import CodeExecutorTool, FileManagerTool, ShellTool

    llm = create_llm_client()
    tools = [CodeExecutorTool(), FileManagerTool(), ShellTool()]
    orchestrator = OrchestratorAgent(llm=llm, tools=tools)

    console.print(
        Panel.fit(
            f"[bold]Task:[/] {task}",
            title="[blue]DeepCode Agent[/]",
            border_style="blue",
        )
    )

    if stream:
        async for chunk in orchestrator.stream_run(task):
            console.print(chunk.replace("\\n", "\n"), end="")
        console.print()
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Running multi-agent workflow...", total=None)
            result = await orchestrator.run(task)

        if result.plan:
            console.print("\n[bold]📋 Plan:[/]")
            for i, step in enumerate(result.plan, 1):
                console.print(f"  {i}. {step}")

        for artifact in result.code_artifacts:
            lang = artifact.get("filename", "").rsplit(".", 1)[-1] or "python"
            console.print(
                Panel(
                    Syntax(artifact.get("content", ""), lang, theme="monokai"),
                    title=f"[cyan]{artifact.get('filename')}[/]",
                    border_style="cyan",
                )
            )

        if result.review_result:
            score = result.review_result.get("score", "N/A")
            passed = result.review_result.get("passed", True)
            console.print(
                f"\n[bold]🔍 Review:[/] "
                f"{'✅ Passed' if passed else '❌ Failed'} (score: {score}/10)"
            )
            for issue in result.review_result.get("issues", []):
                console.print(f"  ⚠️  {issue}")

        if result.success:
            console.print("\n[bold green]✅ Task completed successfully![/]")
        else:
            console.print(f"\n[bold red]❌ Task failed: {result.error}[/]")
            sys.exit(1)


# ─── serve command ────────────────────────────────────────────────────────────

@main.command()
@click.option("--host", default=None, help="Host to bind (overrides config)")
@click.option("--port", default=None, type=int, help="Port to bind (overrides config)")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def serve(host: str | None, port: int | None, reload: bool) -> None:
    """Start the DeepCode Agent REST API server.

    The API documentation will be available at http://HOST:PORT/docs
    """
    import uvicorn

    settings = get_settings()
    h = host or settings.api_host
    p = port or settings.api_port

    console.print(
        Panel.fit(
            f"[bold blue]DeepCode Agent API[/]\n"
            f"URL: http://{h}:{p}\n"
            f"Docs: http://{h}:{p}/docs",
            border_style="blue",
        )
    )

    uvicorn.run(
        "deepcode.api.app:create_app",
        factory=True,
        host=h,
        port=p,
        reload=reload,
        log_level="debug" if settings.debug else "info",
    )


# ─── ui command ───────────────────────────────────────────────────────────────

@main.command()
@click.option("--port", default=8501, type=int, help="Streamlit port")
def ui(port: int) -> None:
    """Launch the DeepCode Agent Web UI (Streamlit)."""
    import subprocess
    import sys
    from pathlib import Path

    ui_app = Path(__file__).parent / "ui" / "app.py"
    if not ui_app.exists():
        console.print("[red]Web UI not found. Ensure the deepcode package is installed.[/]")
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold blue]DeepCode Agent Web UI[/]\n"
            f"URL: http://localhost:{port}",
            border_style="blue",
        )
    )

    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(ui_app), "--server.port", str(port)],
        check=True,
    )


if __name__ == "__main__":
    main()
