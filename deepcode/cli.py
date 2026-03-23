"""Command-line interface for DeepCode Agent."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table

from deepcode import __version__
from deepcode.api.napcat_inbound_listener import (
    get_napcat_inbound_listener_status,
    start_napcat_inbound_listener,
    stop_napcat_inbound_listener,
)
from deepcode.config import apply_chat_bridge_runtime_overrides, get_settings
from deepcode.extensions import HookEvent, MCPRegistry, MCPServerConfig, SkillRegistry
from deepcode.exceptions import SessionNotFoundError, TaskNotFoundError
from deepcode.governance import ApprovalStore, AuditLogger, PolicyRule, PolicyStore
from deepcode.logging_config import configure_logging
from deepcode.preflight import environment_snapshot, run_preflight
from deepcode.storage import Message, SessionStore, TaskStore

console = Console()
audit_logger = AuditLogger()


# ─── Main group ───────────────────────────────────────────────────────────────

@click.group()
@click.version_option(version=__version__, prog_name="deepcode")
@click.option("--debug", is_flag=True, help="Enable debug logging")
def main(debug: bool) -> None:
    """DeepCode Agent – AI-powered software engineering assistant."""
    configure_logging(debug=debug)


def _enforce_preflight(target: str, skip_preflight: bool) -> None:
    """Run preflight checks and abort on hard failures."""
    if skip_preflight:
        return

    checks = run_preflight(target=target)
    failed = [c for c in checks if c.status == "fail"]
    if not failed:
        return

    console.print("[bold red]Preflight checks failed:[/]")
    for check in failed:
        console.print(f"- [red]{check.name}[/]: {check.detail}")
        if check.fix:
            console.print(f"  [dim]Fix: {check.fix}[/]")

    console.print("\n[dim]Run 'deepcode doctor' for full diagnostics.[/]")
    sys.exit(2)


def _audit_event(
    event: str,
    status: str = "ok",
    resource: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write best-effort audit event without blocking command execution."""
    try:
        audit_logger.write(
            event=event,
            actor="cli",
            status=status,
            resource=resource,
            metadata=metadata or {},
        )
    except Exception:
        # Audit should never break primary command execution.
        pass


def _render_task_record(record: dict[str, Any]) -> None:
    """Render a task record in human-friendly format."""
    console.print(
        Panel.fit(
            f"[bold]Task ID:[/] {record['task_id']}\n"
            f"[bold]Status:[/] {record['status']}\n"
            f"[bold]Task:[/] {record['task']}",
            title="[blue]Task Detail[/]",
            border_style="blue",
        )
    )

    if record.get("plan"):
        console.print("\n[bold]Plan:[/]")
        for i, step in enumerate(record["plan"], 1):
            console.print(f"  {i}. {step}")

    for artifact in record.get("code_artifacts", []):
        lang = artifact.get("filename", "").rsplit(".", 1)[-1] or "python"
        console.print(
            Panel(
                Syntax(artifact.get("content", ""), lang, theme="monokai"),
                title=f"[cyan]{artifact.get('filename')}[/]",
                border_style="cyan",
            )
        )

    review = record.get("review_result") or {}
    if review:
        score = review.get("score", "N/A")
        passed = review.get("passed", True)
        console.print(
            f"\n[bold]Review:[/] {'✅ Passed' if passed else '❌ Failed'} (score: {score}/10)"
        )

    if record.get("error"):
        console.print(f"\n[red]Error:[/] {record['error']}")


def _task_record_to_dict(record: Any) -> dict[str, Any]:
    """Convert TaskRecord to JSON-safe dict for CLI output."""
    return {
        "task_id": record.id,
        "task": record.task,
        "session_id": record.session_id,
        "status": record.status,
        "plan": record.plan,
        "code_artifacts": record.code_artifacts,
        "review_result": record.review_result,
        "execution_results": record.execution_results,
        "task_state": record.task_state,
        "observations": record.observations,
        "reflections": record.reflections,
        "errors": record.errors,
        "summary": record.summary,
        "error": record.error,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "metadata": record.metadata,
    }


@main.command()
@click.option(
    "--target",
    type=click.Choice(["all", "chat", "run", "serve", "ui"]),
    default="all",
    show_default=True,
    help="Check scope.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Doctor output format.",
)
@click.option("--strict", is_flag=True, help="Treat warnings as failures.")
def doctor(target: str, output_format: str, strict: bool) -> None:
    """Inspect environment and runtime readiness diagnostics."""
    checks = run_preflight(target=target)
    snapshot = environment_snapshot()

    if output_format == "json":
        payload = {
            "snapshot": snapshot,
            "checks": [c.to_dict() for c in checks],
        }
        console.print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        table = Table(title="DeepCode Doctor", show_lines=False)
        table.add_column("Check", style="cyan", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Detail")
        table.add_column("Fix")

        status_map = {
            "pass": "[green]PASS[/]",
            "warn": "[yellow]WARN[/]",
            "fail": "[red]FAIL[/]",
        }
        for check in checks:
            table.add_row(
                check.name,
                status_map[check.status],
                check.detail,
                check.fix,
            )

        console.print(table)
        console.print("\n[bold]Environment[/]")
        for key, value in snapshot.items():
            console.print(f"- {key}: {value}")

    has_failed = any(c.status == "fail" for c in checks)
    has_warned = any(c.status == "warn" for c in checks)
    _audit_event(
        event="doctor.run",
        status="error" if has_failed or (strict and has_warned) else "ok",
        resource=target,
        metadata={"strict": strict, "format": output_format},
    )
    if has_failed or (strict and has_warned):
        sys.exit(2)


# ─── chat command ─────────────────────────────────────────────────────────────

@main.command()
@click.option("--session", "-s", default=None, help="Continue an existing session by ID")
@click.option("--stream/--no-stream", default=True, help="Stream the agent's thinking")
@click.option("--skip-preflight", is_flag=True, help="Skip environment checks before command execution")
def chat(session: str | None, stream: bool, skip_preflight: bool) -> None:
    """Start an interactive chat session with DeepCode Agent.

    Type your request and press Enter. Use 'exit' or Ctrl-C to quit.
    """
    _enforce_preflight(target="chat", skip_preflight=skip_preflight)
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
    from deepcode.tools import build_default_tools

    store = SessionStore()
    if session_id:
        try:
            session = await store.get(session_id)
        except SessionNotFoundError:
            console.print(f"[red]Session '{session_id}' not found.[/]")
            return
    else:
        session = await store.create(name="CLI Chat")
        _audit_event(event="session.created", resource=session.id, metadata={"name": session.name})

    console.print(f"[dim]Session ID: {session.id}[/]")

    llm = create_llm_client()
    tools = build_default_tools()
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

        session.messages.append(Message(role="user", content=user_input))

        console.print()

        assistant_text = ""
        if stream:
            async for chunk in agent.stream_run(user_input):
                # Replace escaped newlines
                console.print(chunk.replace("\\n", "\n"), end="")
                assistant_text += chunk.replace("\\n", "\n")
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

            assistant_text = result.answer
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

        session.messages.append(Message(role="assistant", content=assistant_text))
        await store.update(session)
        _audit_event(event="session.updated", resource=session.id, metadata={"message_count": len(session.messages)})


# ─── run command ──────────────────────────────────────────────────────────────

@main.command()
@click.argument("task")
@click.option("--session-id", default=None, help="Attach task run to an existing session ID")
@click.option("--stream/--no-stream", default=False, help="Stream the agent's thinking")
@click.option(
    "--parallel-workers",
    default=1,
    type=int,
    show_default=True,
    help="Run coding stage in parallel when greater than 1.",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    show_default=True,
    help="Output format.",
)
@click.option("--skip-preflight", is_flag=True, help="Skip environment checks before command execution")
def run(
    task: str,
    session_id: str | None,
    stream: bool,
    parallel_workers: int,
    output_format: str,
    skip_preflight: bool,
) -> None:
    """Run a single task with the orchestrated multi-agent workflow.

    TASK is the natural language description of what to build.

    Example: deepcode run "Write a Python function to parse CSV files"
    """
    _enforce_preflight(target="run", skip_preflight=skip_preflight)
    _audit_event(
        event="task.run.requested",
        resource=task,
        metadata={"session_id": session_id, "stream": stream, "parallel_workers": parallel_workers},
    )
    exit_code = asyncio.run(
        _run_task(
            task=task,
            session_id=session_id,
            stream=stream,
            parallel_workers=parallel_workers,
            output_format=output_format,
        )
    )
    if exit_code:
        sys.exit(exit_code)


async def _run_task(
    task: str,
    session_id: str | None,
    stream: bool,
    parallel_workers: int,
    output_format: str,
) -> int:
    from deepcode.agents.orchestrator import OrchestratorAgent
    from deepcode.llm.factory import create_llm_client
    from deepcode.tools import build_default_tools

    task_store = TaskStore()
    record = await task_store.create(task=task, session_id=session_id, metadata={"origin": "cli"})
    await task_store.set_status(record.id, "running")

    llm = create_llm_client()
    tools = build_default_tools()
    orchestrator = OrchestratorAgent(llm=llm, tools=tools)

    console.print(
        Panel.fit(
            f"[bold]Task:[/] {task}\n[bold]Task ID:[/] {record.id}",
            title="[blue]DeepCode Agent[/]",
            border_style="blue",
        )
    )

    if stream:
        chunks: list[str] = []
        async for chunk in orchestrator.stream_run(task):
            rendered = chunk.replace("\\n", "\n")
            console.print(rendered, end="")
            chunks.append(rendered)
        console.print()
        updated = await task_store.set_status(
            record.id,
            "completed",
            summary="".join(chunks),
        )

        if output_format == "json":
            console.print(json.dumps(_task_record_to_dict(updated), ensure_ascii=False, indent=2))
        _audit_event(
            event="task.run.completed",
            resource=record.id,
            metadata={"status": "completed", "stream": True},
        )
        return 0
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Running multi-agent workflow...", total=None)
            if parallel_workers > 1:
                result = await orchestrator.run_parallel(task, max_parallel_steps=parallel_workers)
            else:
                result = await orchestrator.run(task)

        updated = await task_store.set_status(
            record.id,
            "completed" if result.success else "failed",
            plan=result.plan,
            code_artifacts=result.code_artifacts,
            review_result=result.review_result,
            execution_results=result.execution_results,
            task_state=result.task_state,
            observations=result.observations,
            reflections=result.reflections,
            errors=result.errors,
            summary=result.summary,
            error=result.error,
        )

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

        if output_format == "json":
            console.print(json.dumps(_task_record_to_dict(updated), ensure_ascii=False, indent=2))

        _audit_event(
            event="task.run.completed",
            status="ok" if result.success else "error",
            resource=record.id,
            metadata={
                "status": updated.status,
                "parallel_workers": parallel_workers,
                "artifact_count": len(updated.code_artifacts),
            },
        )

        return 0 if result.success else 1


# ─── serve command ────────────────────────────────────────────────────────────


@main.group("inbound")
def inbound_group() -> None:
    """Manage standalone NapCat inbound listener process."""


@inbound_group.command("status")
@click.option("--json-output", is_flag=True, help="Output status in JSON format")
def inbound_status(json_output: bool) -> None:
    """Show inbound listener status."""
    result = get_napcat_inbound_listener_status(get_settings())
    if json_output:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    console.print(
        Panel.fit(
            f"[bold]Status:[/] {result.get('status')}\n"
            f"[bold]Host:[/] {result.get('host')}\n"
            f"[bold]Port:[/] {result.get('port')}\n"
            f"[bold]PID:[/] {result.get('pid') or '-'}\n"
            f"[bold]Listening:[/] {result.get('listening')}\n"
            f"[bold]Managed:[/] {result.get('managed')}",
            title="NapCat Inbound Listener",
            border_style="blue",
        )
    )


@inbound_group.command("start")
@click.option("--port", default=None, type=int, help="Override inbound callback port for this start")
@click.option("--json-output", is_flag=True, help="Output start result in JSON format")
def inbound_start(port: int | None, json_output: bool) -> None:
    """Start standalone inbound listener process."""
    result = start_napcat_inbound_listener(get_settings(), port=port)
    if json_output:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    status_text = "running" if result.get("running") else "not-running"
    console.print(f"[green]Inbound listener start action:[/] {result.get('action')} ({status_text})")
    console.print(f"Host={result.get('host')} Port={result.get('port')} PID={result.get('pid')}")


@inbound_group.command("stop")
@click.option("--json-output", is_flag=True, help="Output stop result in JSON format")
def inbound_stop(json_output: bool) -> None:
    """Stop standalone inbound listener process."""
    result = stop_napcat_inbound_listener(get_settings())
    if json_output:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    status_text = "running" if result.get("running") else "stopped"
    console.print(f"[yellow]Inbound listener stop action:[/] {result.get('action')} ({status_text})")
    console.print(f"Host={result.get('host')} Port={result.get('port')} PID={result.get('pid')}")


@main.command("napcat")
@click.argument("port_arg", required=False, type=int)
@click.option("-p", "--port", "port_opt", default=None, type=int, help="Inbound callback port")
@click.option("--json-output", is_flag=True, help="Output start result in JSON format")
def napcat_start(port_arg: int | None, port_opt: int | None, json_output: bool) -> None:
    """Quick start NapCat inbound listener process.

    Examples:
      deepcode napcat 18000
      deepcode napcat -p 18000
    """
    if port_arg is not None and port_opt is not None and int(port_arg) != int(port_opt):
        raise click.BadParameter("port argument conflicts with --port option")

    target_port = port_opt if port_opt is not None else port_arg
    result = start_napcat_inbound_listener(get_settings(), port=target_port)

    if json_output:
        console.print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    status_text = "running" if result.get("running") else "not-running"
    console.print(
        f"[green]NapCat inbound listener start action:[/] {result.get('action')} ({status_text})"
    )
    console.print(f"Host={result.get('host')} Port={result.get('port')} PID={result.get('pid')}")


@main.command()
@click.option("--host", default=None, help="Host to bind (overrides config)")
@click.option("--port", default=None, type=int, help="Port to bind (overrides config)")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
@click.option("--skip-preflight", is_flag=True, help="Skip environment checks before command execution")
def serve(host: str | None, port: int | None, reload: bool, skip_preflight: bool) -> None:
    """Start the DeepCode Agent REST API server.

    The API documentation will be available at http://HOST:PORT/docs
    """
    _enforce_preflight(target="serve", skip_preflight=skip_preflight)
    import uvicorn

    settings = get_settings()
    apply_chat_bridge_runtime_overrides(settings)
    h = host or settings.api_host
    p = port or getattr(settings, "chat_bridge_inbound_port", settings.api_port) or settings.api_port

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
@click.option("--port", default=8501, type=int, help="Reflex frontend port")
@click.option("--backend-port", default=8502, type=int, help="Reflex backend port")
@click.option(
    "--mode",
    "ui_mode",
    type=click.Choice(["dev", "prod"]),
    default="dev",
    show_default=True,
    help="Reflex runtime mode.",
)
@click.option("--skip-preflight", is_flag=True, help="Skip environment checks before command execution")
def ui(
    port: int,
    backend_port: int,
    ui_mode: str,
    skip_preflight: bool,
) -> None:
    """Launch the DeepCode Agent Web UI."""
    import os
    import socket
    import subprocess
    import sys
    from pathlib import Path

    def _sync_reflex_env_json(web_root: Path, runtime_backend_port: int) -> None:
        """Keep Reflex frontend env endpoints aligned with the selected backend port."""
        env_path = web_root / ".web" / "env.json"
        base = f"http://localhost:{runtime_backend_port}"
        payload = {
            "PING": f"{base}/ping",
            "EVENT": f"ws://localhost:{runtime_backend_port}/_event",
            "UPLOAD": f"{base}/_upload",
            "AUTH_CODESPACE": f"{base}/auth-codespace",
            "HEALTH": f"{base}/_health",
            "ALL_ROUTES": f"{base}/_all_routes",
            "TRANSPORT": "websocket",
            "TEST_MODE": False,
        }
        try:
            env_path.parent.mkdir(parents=True, exist_ok=True)
            env_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            # Frontend can still boot; this pre-sync only prevents stale ws endpoint caching.
            pass

    def _is_port_busy(target_port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex(("127.0.0.1", target_port)) == 0

    workspace_root = Path(__file__).parent.parent
    _enforce_preflight(target="ui", skip_preflight=skip_preflight)

    busy_ports = [p for p in (port, backend_port) if _is_port_busy(p)]
    if busy_ports:
        ports_text = ", ".join(str(p) for p in busy_ports)
        console.print(
            f"[red]Cannot start Reflex UI: port(s) already in use: {ports_text}.[/] "
            "Use --port/--backend-port with free ports."
        )
        sys.exit(1)

    reflex_config = workspace_root / "rxconfig.py"
    if not reflex_config.exists():
        console.print(
            "[red]Reflex config not found. Ensure this command runs from a source checkout with rxconfig.py.[/]"
        )
        sys.exit(1)

    console.print(
        Panel.fit(
            f"[bold blue]DeepCode Agent Web UI (Reflex)[/]\n"
            f"Frontend: http://localhost:{port}\n"
            f"Backend: http://localhost:{backend_port}",
            border_style="blue",
        )
    )

    try:
        env = os.environ.copy()
        # Reflex 0.8 on Windows may hang during automatic Bun bootstrap;
        # prefer npm when Node.js is already available.
        env.setdefault("REFLEX_USE_NPM", "1")
        env["REFLEX_BACKEND_PORT"] = str(backend_port)
        env["REFLEX_FRONTEND_PORT"] = str(port)
        env["REFLEX_API_URL"] = f"http://localhost:{backend_port}"
        _sync_reflex_env_json(workspace_root, backend_port)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "reflex",
                "run",
                "--frontend-port",
                str(port),
                "--backend-port",
                str(backend_port),
                "--env",
                ui_mode,
            ],
            cwd=str(workspace_root),
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Failed to launch UI: {exc}[/]")
        sys.exit(exc.returncode)


@main.group()
def session() -> None:
    """Session management commands."""


@session.command("create")
@click.option("--name", default="New Session", show_default=True, help="Session display name")
def session_create(name: str) -> None:
    """Create a new chat session."""

    async def _create() -> None:
        store = SessionStore()
        created = await store.create(name=name)
        console.print(f"[green]Created session:[/] {created.id}")
        console.print(f"[dim]Name:[/] {created.name}")
        _audit_event(event="session.created", resource=created.id, metadata={"name": created.name})

    asyncio.run(_create())


@session.command("list")
@click.option("--limit", default=20, type=int, show_default=True, help="Maximum sessions to display")
def session_list(limit: int) -> None:
    """List recent sessions."""

    async def _list() -> None:
        store = SessionStore()
        sessions = (await store.list_all())[: max(limit, 1)]
        if not sessions:
            console.print("[yellow]No sessions found.[/]")
            return

        table = Table(title="Sessions")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Messages", justify="right")
        table.add_column("Updated")
        for s in sessions:
            table.add_row(s.id, s.name, str(len(s.messages)), s.updated_at.isoformat())
        console.print(table)

    asyncio.run(_list())


@session.command("show")
@click.argument("session_id")
@click.option("--messages/--no-messages", default=True, show_default=True, help="Include messages")
def session_show(session_id: str, messages: bool) -> None:
    """Show one session by ID."""

    async def _show() -> int:
        store = SessionStore()
        try:
            s = await store.get(session_id)
        except SessionNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            return 1

        console.print(
            Panel.fit(
                f"[bold]ID:[/] {s.id}\n"
                f"[bold]Name:[/] {s.name}\n"
                f"[bold]Messages:[/] {len(s.messages)}\n"
                f"[bold]Updated:[/] {s.updated_at.isoformat()}",
                title="[blue]Session[/]",
                border_style="blue",
            )
        )

        if messages and s.messages:
            for msg in s.messages:
                console.print(f"\n[bold]{msg.role}[/] ({msg.created_at.isoformat()}):")
                console.print(msg.content)

        return 0

    exit_code = asyncio.run(_show())
    if exit_code:
        sys.exit(exit_code)


@session.command("delete")
@click.argument("session_id")
def session_delete(session_id: str) -> None:
    """Delete a session by ID."""

    async def _delete() -> int:
        store = SessionStore()
        try:
            await store.delete(session_id)
        except SessionNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            return 1
        console.print(f"[green]Deleted session:[/] {session_id}")
        _audit_event(event="session.deleted", resource=session_id)
        return 0

    exit_code = asyncio.run(_delete())
    if exit_code:
        sys.exit(exit_code)


@main.group()
def task() -> None:
    """Task management commands."""


@task.command("run")
@click.argument("task_text")
@click.option("--session-id", default=None, help="Attach task run to an existing session ID")
@click.option("--stream/--no-stream", default=False, help="Stream the agent's thinking")
@click.option(
    "--parallel-workers",
    default=1,
    type=int,
    show_default=True,
    help="Run coding stage in parallel when greater than 1.",
)
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    show_default=True,
    help="Output format.",
)
@click.option("--skip-preflight", is_flag=True, help="Skip environment checks before command execution")
def task_run(
    task_text: str,
    session_id: str | None,
    stream: bool,
    parallel_workers: int,
    output_format: str,
    skip_preflight: bool,
) -> None:
    """Run an orchestrated task (alias of root run command)."""
    _enforce_preflight(target="run", skip_preflight=skip_preflight)
    exit_code = asyncio.run(
        _run_task(
            task=task_text,
            session_id=session_id,
            stream=stream,
            parallel_workers=parallel_workers,
            output_format=output_format,
        )
    )
    if exit_code:
        sys.exit(exit_code)


@task.command("list")
@click.option("--limit", default=20, type=int, show_default=True, help="Maximum tasks to display")
def task_list(limit: int) -> None:
    """List recent tasks from persistent history."""

    async def _list() -> None:
        store = TaskStore()
        tasks = await store.list_all(limit=max(limit, 1))
        if not tasks:
            console.print("[yellow]No tasks found.[/]")
            return

        table = Table(title="Tasks")
        table.add_column("Task ID", style="cyan")
        table.add_column("Status")
        table.add_column("Task")
        table.add_column("Updated")
        for t in tasks:
            table.add_row(t.id, t.status, t.task, t.updated_at.isoformat())
        console.print(table)

    asyncio.run(_list())


@task.command("show")
@click.argument("task_id")
@click.option(
    "--output",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    show_default=True,
    help="Output format.",
)
def task_show(task_id: str, output_format: str) -> None:
    """Show one task record by ID."""

    async def _show() -> tuple[int, dict[str, Any] | None]:
        store = TaskStore()
        try:
            record = await store.get(task_id)
        except TaskNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            return 1, None
        return 0, _task_record_to_dict(record)

    exit_code, record = asyncio.run(_show())
    if exit_code:
        sys.exit(exit_code)

    if output_format == "json":
        console.print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        _render_task_record(record or {})


@task.command("delete")
@click.argument("task_id")
def task_delete(task_id: str) -> None:
    """Delete one task by ID."""

    async def _delete() -> int:
        store = TaskStore()
        try:
            await store.delete(task_id)
        except TaskNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            return 1
        console.print(f"[green]Deleted task:[/] {task_id}")
        _audit_event(event="task.deleted", resource=task_id)
        return 0

    exit_code = asyncio.run(_delete())
    if exit_code:
        sys.exit(exit_code)


@main.group()
def artifact() -> None:
    """Artifact browsing commands."""


@artifact.command("list")
@click.option("--task-id", default=None, help="Filter artifacts by task ID")
@click.option("--limit", default=20, type=int, show_default=True, help="Maximum tasks to scan")
def artifact_list(task_id: str | None, limit: int) -> None:
    """List generated code artifacts from recent tasks."""

    async def _list() -> int:
        store = TaskStore()
        tasks = [await store.get(task_id)] if task_id else await store.list_all(limit=max(limit, 1))
        rows: list[tuple[str, int, str]] = []
        for t in tasks:
            for idx, artifact in enumerate(t.code_artifacts, start=1):
                rows.append((t.id, idx, artifact.get("filename", f"artifact_{idx}")))

        if not rows:
            console.print("[yellow]No artifacts found.[/]")
            return 0

        table = Table(title="Artifacts")
        table.add_column("Task ID", style="cyan")
        table.add_column("Index", justify="right")
        table.add_column("Filename")
        for row in rows:
            table.add_row(row[0], str(row[1]), row[2])
        console.print(table)
        return 0

    try:
        exit_code = asyncio.run(_list())
    except TaskNotFoundError as exc:
        console.print(f"[red]{exc}[/]")
        exit_code = 1
    if exit_code:
        sys.exit(exit_code)


@artifact.command("show")
@click.argument("task_id")
@click.argument("artifact_ref")
def artifact_show(task_id: str, artifact_ref: str) -> None:
    """Show one artifact by task ID and index or filename."""

    async def _show() -> int:
        store = TaskStore()
        try:
            task_record = await store.get(task_id)
        except TaskNotFoundError as exc:
            console.print(f"[red]{exc}[/]")
            return 1

        artifacts = task_record.code_artifacts
        if not artifacts:
            console.print("[yellow]No artifacts found for this task.[/]")
            return 1

        picked: dict[str, Any] | None = None
        if artifact_ref.isdigit():
            idx = int(artifact_ref) - 1
            if 0 <= idx < len(artifacts):
                picked = artifacts[idx]
        else:
            for artifact in artifacts:
                if artifact.get("filename") == artifact_ref:
                    picked = artifact
                    break

        if picked is None:
            console.print(f"[red]Artifact '{artifact_ref}' not found in task {task_id}.[/]")
            return 1

        filename = picked.get("filename", "output.py")
        lang = filename.rsplit(".", 1)[-1] or "python"
        console.print(
            Panel(
                Syntax(picked.get("content", ""), lang, theme="monokai"),
                title=f"[cyan]{filename}[/]",
                border_style="cyan",
            )
        )
        return 0

    exit_code = asyncio.run(_show())
    if exit_code:
        sys.exit(exit_code)


@main.group()
def extension() -> None:
    """MCP/Hook/Skill extension commands."""


@extension.group()
def mcp() -> None:
    """Manage MCP server registry."""


@mcp.command("list")
def mcp_list() -> None:
    """List configured MCP servers."""
    rows = MCPRegistry().to_rows()
    if not rows:
        console.print("[yellow]No MCP servers configured.[/]")
        return

    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Transport")
    table.add_column("Command")
    table.add_column("Enabled")
    table.add_column("Description")
    for row in rows:
        table.add_row(
            row["name"],
            row["transport"],
            row["command"],
            "yes" if row["enabled"] else "no",
            row["description"],
        )
    console.print(table)


@mcp.command("add")
@click.option("--name", required=True, help="MCP server name")
@click.option("--command", required=True, help="Executable command")
@click.option("--arg", "args", multiple=True, help="Command argument; repeat as needed")
@click.option("--transport", default="stdio", show_default=True, help="Transport type")
@click.option("--description", default="", help="Server description")
@click.option("--disabled", is_flag=True, help="Create server in disabled state")
def mcp_add(
    name: str,
    command: str,
    args: tuple[str, ...],
    transport: str,
    description: str,
    disabled: bool,
) -> None:
    """Add or update an MCP server configuration."""
    registry = MCPRegistry()
    registry.upsert(
        MCPServerConfig(
            name=name,
            command=command,
            args=list(args),
            transport=transport,
            enabled=not disabled,
            description=description,
        )
    )
    console.print(f"[green]MCP server upserted:[/] {name}")
    _audit_event(event="mcp.upsert", resource=name)


@mcp.command("remove")
@click.argument("name")
def mcp_remove(name: str) -> None:
    """Remove an MCP server configuration."""
    removed = MCPRegistry().remove(name)
    if not removed:
        console.print(f"[red]MCP server '{name}' not found.[/]")
        sys.exit(1)
    console.print(f"[green]MCP server removed:[/] {name}")
    _audit_event(event="mcp.remove", resource=name)


@extension.group()
def skill() -> None:
    """Inspect discovered skill files."""


@skill.command("list")
@click.option("--skills-dir", default=None, help="Override default skills directory")
def skill_list(skills_dir: str | None) -> None:
    """List discovered skill definitions."""
    skills = SkillRegistry(skills_dir=skills_dir).discover()
    if not skills:
        console.print("[yellow]No skills discovered.[/]")
        return

    table = Table(title="Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Path")
    table.add_column("Tags")
    for item in skills:
        table.add_row(item.name, item.description, item.path, ", ".join(item.tags))
    console.print(table)


@extension.command("hook-list")
def hook_list() -> None:
    """List supported hook lifecycle events."""
    table = Table(title="Hook Events")
    table.add_column("Event", style="cyan")
    table.add_column("Description")
    descriptions = {
        HookEvent.TASK_STARTED.value: "Task entrypoint",
        HookEvent.BEFORE_LLM.value: "Before LLM completion call",
        HookEvent.AFTER_LLM.value: "After LLM completion call",
        HookEvent.BEFORE_TOOL.value: "Before tool invocation",
        HookEvent.AFTER_TOOL.value: "After tool invocation",
        HookEvent.TASK_FINISHED.value: "Task terminal state",
    }
    for event in HookEvent:
        table.add_row(event.value, descriptions.get(event.value, ""))
    console.print(table)


@main.group()
def governance() -> None:
    """Governance and audit commands."""


@governance.group("policy")
def governance_policy() -> None:
    """Policy rule management commands."""


@governance_policy.command("list")
def governance_policy_list() -> None:
    """List policy rules."""
    rows = PolicyStore().to_rows()
    if not rows:
        console.print("[yellow]No policy rules found.[/]")
        return

    table = Table(title="Policy Rules")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Scope")
    table.add_column("Target")
    table.add_column("Decision")
    table.add_column("Enabled")
    table.add_column("Updated")
    for row in rows:
        table.add_row(
            row["id"],
            row["name"],
            row["scope"],
            row["target"],
            row["decision"],
            row["enabled"],
            row["updated_at"],
        )
    console.print(table)


@governance_policy.command("add")
@click.option("--name", required=True, help="Policy rule name")
@click.option("--target", required=True, help="Rule target pattern, e.g. tool:shell:rm")
@click.option(
    "--decision",
    type=click.Choice(["allow", "ask", "deny"]),
    required=True,
    help="Policy decision",
)
@click.option("--scope", default="global", show_default=True, help="Rule scope")
@click.option("--description", default="", help="Rule description")
@click.option("--disabled", is_flag=True, help="Create the rule in disabled state")
def governance_policy_add(
    name: str,
    target: str,
    decision: str,
    scope: str,
    description: str,
    disabled: bool,
) -> None:
    """Create a policy rule."""
    store = PolicyStore()
    created = store.upsert(
        PolicyRule(
            name=name,
            scope=scope,
            target=target,
            decision=decision,
            enabled=not disabled,
            description=description,
        )
    )
    console.print(f"[green]Policy rule upserted:[/] {created.id}")
    _audit_event(
        event="policy.upsert",
        resource=created.id,
        metadata={"name": created.name, "target": created.target, "decision": created.decision},
    )


@governance_policy.command("remove")
@click.argument("rule_id")
def governance_policy_remove(rule_id: str) -> None:
    """Remove one policy rule by ID."""
    removed = PolicyStore().remove(rule_id)
    if not removed:
        console.print(f"[red]Policy rule '{rule_id}' not found.[/]")
        sys.exit(1)
    console.print(f"[green]Policy rule removed:[/] {rule_id}")
    _audit_event(event="policy.remove", resource=rule_id)


@governance.group("approval")
def governance_approval() -> None:
    """Approval request management commands."""


@governance_approval.command("list")
@click.option(
    "--status",
    "filter_status",
    type=click.Choice(["pending", "approved", "rejected"]),
    default=None,
    help="Filter approval requests by status",
)
def governance_approval_list(filter_status: str | None) -> None:
    """List approval requests."""
    store = ApprovalStore()
    rows = store.list_all(status=filter_status) if filter_status else store.list_all()
    if not rows:
        console.print("[yellow]No approval requests found.[/]")
        return

    table = Table(title="Approval Requests")
    table.add_column("ID", style="cyan")
    table.add_column("Tool")
    table.add_column("Status")
    table.add_column("Rule ID")
    table.add_column("Created")
    table.add_column("Reason")
    for item in rows:
        table.add_row(
            item.id,
            item.tool_name,
            item.status,
            item.rule_id,
            item.created_at.isoformat(),
            item.reason[:80],
        )
    console.print(table)


@governance_approval.command("approve")
@click.argument("request_id")
def governance_approval_approve(request_id: str) -> None:
    """Approve one pending approval request."""
    updated = ApprovalStore().decide(request_id, "approved")
    if updated is None:
        console.print(f"[red]Approval request '{request_id}' not found.[/]")
        sys.exit(1)
    console.print(f"[green]Approved:[/] {request_id}")
    _audit_event(event="approval.approved", resource=request_id, metadata={"tool_name": updated.tool_name})


@governance_approval.command("reject")
@click.argument("request_id")
def governance_approval_reject(request_id: str) -> None:
    """Reject one pending approval request."""
    updated = ApprovalStore().decide(request_id, "rejected")
    if updated is None:
        console.print(f"[red]Approval request '{request_id}' not found.[/]")
        sys.exit(1)
    console.print(f"[green]Rejected:[/] {request_id}")
    _audit_event(event="approval.rejected", resource=request_id, metadata={"tool_name": updated.tool_name})


@governance.command("audit-list")
@click.option("--limit", default=50, type=int, show_default=True, help="Maximum events to display")
def governance_audit_list(limit: int) -> None:
    """List recent audit events."""
    events = audit_logger.list_recent(limit=max(limit, 1))
    if not events:
        console.print("[yellow]No audit events found.[/]")
        return

    table = Table(title="Audit Events")
    table.add_column("Timestamp", style="cyan")
    table.add_column("Event")
    table.add_column("Status")
    table.add_column("Resource")
    for event in events:
        table.add_row(
            event.timestamp.isoformat(),
            event.event,
            event.status,
            event.resource,
        )
    console.print(table)


if __name__ == "__main__":
    main()
