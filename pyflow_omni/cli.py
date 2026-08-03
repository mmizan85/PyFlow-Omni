"""CLI entry point: argument parsing and top-level mode dispatch.

Three ways to run:
  pyflow-omni                        -> launches the full Textual TUI
  pyflow-omni <url> [<url> ...]      -> TUI, pre-filled/queued with the input(s)
  pyflow-omni <url> --no-tui         -> headless, Rich progress bars, scriptable
  pyflow-omni --config               -> permanent settings editor
"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

import click

from .config_manager import ConfigManager
from .router import InputKind, SmartRouter
from .utils.subprocess_utils import which


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("inputs", nargs=-1)
@click.option("--config", "show_config", is_flag=True, help="Launch the global settings editor.")
@click.option("--no-tui", is_flag=True, help="Run non-interactively with Rich progress bars (for scripting).")
@click.option("--output-dir", type=click.Path(), default=None, help="Override the download directory for this run.")
@click.option("--engine", "forced_engine", type=click.Choice(["aria2", "ytdlp"]), default=None,
              help="Force a specific engine instead of auto-detecting.")
@click.option("--schedule", default=None, metavar="'YYYY-MM-DD HH:MM'",
              help="Delay the batch until this local time.")
@click.version_option(package_name="pyflow-omni")
def main(
    inputs: Sequence[str],
    show_config: bool,
    no_tui: bool,
    output_dir: Optional[str],
    forced_engine: Optional[str],
    schedule: Optional[str],
) -> None:
    """PyFlow Omni — one CLI for direct/torrent downloads (aria2c) and media extraction (yt-dlp).

    INPUTS may be HTTP(S)/FTP URLs, magnet links, .torrent file paths, or a
    .txt batch file (one link per line, '#' for comments). With no
    arguments, the clipboard is scanned for a link on TUI launch.
    """
    config_manager = ConfigManager()
    config = config_manager.load()
    if output_dir:
        config.download_dir = str(Path(output_dir).expanduser())
        config_manager.save(config)

    if show_config:
        _run_config_editor(config_manager)
        return

    if not inputs and not sys.stdin.isatty():
        piped = sys.stdin.read().strip()
        if piped:
            inputs = tuple(line.strip() for line in piped.splitlines() if line.strip())

    if schedule:
        _wait_until(schedule)

    if no_tui:
        exit_code = asyncio.run(_run_headless(inputs, config_manager, forced_engine=forced_engine))
        sys.exit(exit_code)

    from .tui.app import PyFlowOmniApp

    initial = inputs[0] if len(inputs) == 1 else None
    app = PyFlowOmniApp(initial_input=initial, config_manager=config_manager)
    app.run()


def _wait_until(schedule: str) -> None:
    try:
        target = datetime.strptime(schedule, "%Y-%m-%d %H:%M")
    except ValueError:
        click.echo(f"Couldn't parse --schedule {schedule!r}; expected 'YYYY-MM-DD HH:MM'.", err=True)
        sys.exit(2)
    delay = (target - datetime.now()).total_seconds()
    if delay > 0:
        click.echo(f"Scheduled for {target}; sleeping {delay:.0f}s… (Ctrl+C to cancel)")
        time.sleep(delay)


def _run_config_editor(config_manager: ConfigManager) -> None:
    if not sys.stdin.isatty():
        click.echo(f"Interactive config editor needs a TTY; edit {config_manager.config_path} directly.")
        return
    from .tui.app import PyFlowOmniApp
    app = PyFlowOmniApp(config_manager=config_manager, open_settings=True)
    app.run()


async def _run_headless(
    inputs: Sequence[str], config_manager: ConfigManager, *, forced_engine: Optional[str]
) -> int:
    import signal

    from rich.console import Console

    from .engines.aria2_engine import Aria2Engine
    from .engines.ytdlp_engine import YtdlpEngine

    console = Console()
    config = config_manager.load()
    router = SmartRouter()
    aria2_engine = Aria2Engine(
        aria2c_path=config.aria2c_path,
        max_concurrent_downloads=config.aria2_defaults.max_concurrent_downloads,
    )
    ytdlp_engine = YtdlpEngine(ffmpeg_path=config.ffmpeg_path)

    # SIGTERM has no default Python handler at all (unlike SIGINT, which
    # Python turns into KeyboardInterrupt) — without this, a plain `kill` or
    # `docker stop` would leave the aria2c daemon orphaned. Both signals are
    # routed to the same clean cancellation so the try/finally below always
    # gets to run terminate_gracefully() on the spawned daemon.
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        console.print("\n[yellow]Interrupted — shutting down aria2c cleanly…[/yellow]")
        if main_task is not None:
            main_task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows lacks add_signal_handler
            loop.add_signal_handler(sig, _request_shutdown)

    if not inputs:
        console.print("[yellow]No input given.[/yellow] Provide a URL, magnet link, .torrent path, or batch .txt file.")
        return 1

    try:
        return await _download_all(inputs, config, router, aria2_engine, ytdlp_engine, forced_engine, console)
    except asyncio.CancelledError:
        return 130
    finally:
        await aria2_engine.shutdown()


async def _download_all(inputs, config, router, aria2_engine, ytdlp_engine, forced_engine, console) -> int:
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn

    targets = []
    for raw in inputs:
        decision = await router.classify_async(raw)
        if decision.kind == InputKind.BATCH_FILE:
            targets.extend(await router.classify_batch(raw))
        else:
            targets.append(decision)

    had_failure = False
    bar_tasks: dict[str, int] = {}
    with Progress(
        TextColumn("[bold blue]{task.fields[name]}", justify="left"),
        BarColumn(), DownloadColumn(), TransferSpeedColumn(), TimeRemainingColumn(),
        console=console,
    ) as progress_bar:

        async def on_progress(update) -> None:
            if update.task_id not in bar_tasks:
                bar_tasks[update.task_id] = progress_bar.add_task(
                    "download", name=update.name[:40], total=update.total_bytes or None
                )
            progress_bar.update(
                bar_tasks[update.task_id],
                completed=update.downloaded_bytes,
                total=update.total_bytes or None,
            )

        for decision in targets:
            engine_name = forced_engine or decision.engine
            if engine_name is None:
                console.print(f"[yellow]Skipping ambiguous input:[/yellow] {decision.original}")
                had_failure = True
                continue
            engine = aria2_engine if engine_name == "aria2" else ytdlp_engine
            try:
                result = await engine.process(decision.original, config, on_progress)
                if result.error:
                    console.print(f"[red]✗[/red] {result.name}: {result.error}")
                    had_failure = True
                else:
                    console.print(f"[green]✓[/green] {result.name} -> {result.status.value}")
            except Exception as exc:
                console.print(f"[red]✗ {decision.original}: {exc}[/red]")
                had_failure = True

    return 1 if had_failure else 0
