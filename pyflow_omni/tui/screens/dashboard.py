"""Post-Download Dashboard: results table + summary + follow-up actions (spec 4.7)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Label, Static

from ...engines.base import DownloadResult, TaskStatus
from ...utils.file_utils import human_size, open_in_file_manager

_FORMAT_ICONS = {
    "mp4": "🎬", "mkv": "🎬", "webm": "🎬", "avi": "🎬", "mov": "🎬", "3gp": "🎬",
    "mp3": "🎵", "flac": "🎵", "wav": "🎵", "m4a": "🎵", "opus": "🎵", "aac": "🎵",
    "zip": "📦", "tar": "📦", "gz": "📦", "7z": "📦", "rar": "📦",
    "torrent": "🧲", "iso": "💿",
}


def _format_and_icon(result: DownloadResult) -> tuple[str, str]:
    if not result.output_paths:
        return "-", "📄"
    ext = Path(result.output_paths[0]).suffix.lstrip(".").lower() or "-"
    return ext.upper() or "-", _FORMAT_ICONS.get(ext, "📄")


class DashboardScreen(Screen):
    """Shown after a batch completes: a stat summary plus a table of
    File Name / Format / Size / Avg Speed / Time / Status."""

    BINDINGS = [("escape", "main_menu", "Main menu")]

    def compose(self) -> ComposeResult:
        with Vertical(classes="glass-panel"):
            yield Label("📊 Post-Download Dashboard", classes="section-title")
            with Horizontal(id="stat-cards"):
                yield Static("", id="stat-total", classes="stat-card")
                yield Static("", id="stat-ok", classes="stat-card stat-card-ok")
                yield Static("", id="stat-failed", classes="stat-card stat-card-fail")
                yield Static("", id="stat-size", classes="stat-card")
                yield Static("", id="stat-time", classes="stat-card")
            yield DataTable(id="results-table")
            yield Static("", id="summary-line", classes="hint-text")
            with Horizontal():
                yield Button("📂 Open output folder", id="open-folder-btn")
                yield Button("🔁 Retry failed", id="retry-btn")
                yield Button("🏠 Main menu", id="main-menu-btn", classes="accent-button")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.add_columns("File Name", "Format", "Size", "Avg Speed", "Time", "Status")
        results = self.app.completed_results

        total_bytes = 0
        total_time = 0.0
        succeeded = 0
        for r in results:
            ok = r.status == TaskStatus.COMPLETE
            succeeded += 1 if ok else 0
            icon = "✅" if ok else ("⏹️" if r.status == TaskStatus.CANCELLED else "❌")
            fmt_label, fmt_icon = _format_and_icon(r)
            table.add_row(
                r.name,
                f"{fmt_icon} {fmt_label}",
                human_size(r.total_bytes) if r.total_bytes else "-",
                f"{human_size(r.average_speed)}/s" if r.average_speed else "-",
                f"{r.elapsed_seconds:.1f}s",
                f"{icon} {r.status.value}",
                key=r.task_id,
            )
            total_bytes += r.total_bytes or 0
            total_time += r.elapsed_seconds

        failed = len(results) - succeeded
        avg_speed = human_size(total_bytes / total_time) + "/s" if total_time > 0.001 else "-"
       

        self.query_one("#stat-total", Static).update(f"📦 [b]{len(results)}[/b]\ntotal")
        self.query_one("#stat-ok", Static).update(f"✅ [b]{succeeded}[/b]\nsucceeded")
        self.query_one("#stat-failed", Static).update(f"❌ [b]{failed}[/b]\nfailed")
        self.query_one("#stat-size", Static).update(f"💾 [b]{human_size(total_bytes)}[/b]\ndownloaded")
        self.query_one("#stat-time", Static).update(f"⏱️ [b]{total_time:.1f}s[/b]\ntotal time")
        self.query_one("#summary-line", Static).update(f"Average throughput: {avg_speed}")

    @on(Button.Pressed, "#open-folder-btn")
    def _open_folder(self) -> None:
        self.run_worker(open_in_file_manager(self.app.config.download_dir), exclusive=False)

    @on(Button.Pressed, "#retry-btn")
    def _retry_failed(self) -> None:
        failed_inputs = [
            r.name for r in self.app.completed_results if r.status != TaskStatus.COMPLETE
        ]
        if not failed_inputs:
            self.notify("Nothing to retry — everything succeeded.", timeout=3)
            return
        self.notify(f"Re-queuing {len(failed_inputs)} failed item(s)…", timeout=3)
        self.app.pop_screen()
        for value in failed_inputs:
            self.run_worker(self.app.route_and_launch(value), exclusive=False)

    def action_main_menu(self) -> None:
        self._to_main_menu()

    @on(Button.Pressed, "#main-menu-btn")
    def _main_menu_pressed(self) -> None:
        self._to_main_menu()

    def _to_main_menu(self) -> None:
        from .main_menu import MainMenuScreen

        while len(self.app.screen_stack) > 1:
            self.app.pop_screen()
        self.app.push_screen(MainMenuScreen())
