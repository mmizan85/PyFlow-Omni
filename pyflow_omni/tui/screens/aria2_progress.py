"""Aria2 active-progress screen: one row per download, a live log, and queue controls."""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Label, RichLog

from ...engines.aria2_engine import Aria2SessionConfig
from ...engines.base import DownloadResult, ProgressUpdate, TaskStatus
from ...utils.async_utils import Throttler, retry_with_backoff
from ..widgets.enhanced_progress import EnhancedProgress


class Aria2ProgressScreen(Screen):
    """Runs one or more aria2 downloads concurrently and renders live progress for each."""

    BINDINGS = [
        ("p", "pause_selected", "Pause"),
        ("r", "resume_selected", "Resume"),
        ("x", "cancel_selected", "Cancel"),
        ("up", "select_prev", "▲"),
        ("down", "select_next", "▼"),
        ("q", "abort_all", "Abort all"),
    ]

    def __init__(self, input_values: List[str], session_config: Optional[Aria2SessionConfig] = None) -> None:
        super().__init__()
        self.input_values = input_values
        self.session_config = session_config or Aria2SessionConfig()
        self._rows: Dict[str, EnhancedProgress] = {}  # keyed by our own local row id, not the aria2 gid
        self._task_ids: Dict[str, str] = {}  # local row id -> engine task_id (once known)
        self._row_order: List[str] = []
        self._selected = 0
        self._throttler = Throttler(interval=0.15)
        self._results: List[DownloadResult] = []
        self._done_count = 0

    def compose(self) -> ComposeResult:
        yield Label(f"Downloading {len(self.input_values)} item(s) via aria2 — ↑/↓ select, p/r/x pause/resume/cancel", classes="section-title")
        with VerticalScroll(id="rows-container", classes="glass-panel"):
            for i, value in enumerate(self.input_values):
                row_id = f"row-{i}"
                row = EnhancedProgress(task_id=row_id, name=value, id=row_id)
                self._rows[row_id] = row
                self._row_order.append(row_id)
                yield row
        yield RichLog(id="aria2-log", max_lines=500, wrap=False, highlight=False)
        yield Button("Abort all (q)", id="abort-btn")

    def on_mount(self) -> None:
        for row_id, value in zip(self._row_order, self.input_values):
            self.run_worker(self._run_one(row_id, value), exclusive=False, name=row_id)
        self._highlight_selected()

    async def _run_one(self, row_id: str, input_value: str) -> None:
        row = self._rows[row_id]

        async def on_progress(update: ProgressUpdate) -> None:
            self._task_ids[row_id] = update.task_id
            row.rename(update.name)
            if not self._throttler.should_fire(row_id) and update.status.value not in ("complete", "error", "cancelled"):
                return
            row.update_progress(
                percent=update.percent,
                downloaded=update.downloaded_bytes,
                total=update.total_bytes,
                speed=update.speed_bytes_per_sec,
                eta=update.eta_seconds,
                status=update.status.value,
                extra_note=f"conns={update.extra.get('connections')}" if update.extra.get("connections") else "",
            )

        async def on_log(line: str) -> None:
            log = self.query_one("#aria2-log", RichLog)
            log.write(line)

        config = _ConfigView(self.app.config, self.session_config)
        log = self.query_one("#aria2-log", RichLog)

        async def attempt() -> DownloadResult:
            # aria2_engine.process() reports ordinary failures (DNS errors,
            # 404s, ...) via `.error` on a normal return rather than raising,
            # so we translate ERROR into a raise here — that's what actually
            # makes retry_with_backoff retry. COMPLETE/CANCELLED pass through
            # untouched so a user-initiated cancel is never retried.
            result = await self.app.aria2_engine.process(input_value, config, on_progress, on_log)

            if result.status.value == TaskStatus.ERROR:
                raise RuntimeError(result.error or "download failed")
            return result

        async def on_retry(attempt_no: int, exc: BaseException) -> None:
            log.write(f"[retry {attempt_no}/3] {input_value}: {exc}")
            row.update_progress(percent=None, downloaded=0, total=None, speed=0, eta=None,
                                 status=f"retrying ({attempt_no}/3)")

        try:
            result = await retry_with_backoff(attempt, max_retries=3, on_retry=on_retry)
        except Exception as exc:
            result = DownloadResult(task_id=row_id, name=input_value, status=TaskStatus.ERROR, error=str(exc))
            row.update_progress(percent=None, downloaded=0, total=None, speed=0, eta=None, status="error")
            log.write(f"[ERROR] {input_value}: {exc} (all retries exhausted)")

        self._results.append(result)
        self._done_count += 1
        if self._done_count >= len(self.input_values):
            self._all_done()

    def _all_done(self) -> None:
        self.app.completed_results.extend(self._results)
        self.notify(f"Batch complete: {len(self._results)} item(s). Press Ctrl+D for the dashboard.", timeout=6)

    def _highlight_selected(self) -> None:
        for i, row_id in enumerate(self._row_order):
            self._rows[row_id].styles.border = ("round", "#00d68f" if i == self._selected else "#0a0e17")

    def action_select_prev(self) -> None:
        if self._row_order:
            self._selected = (self._selected - 1) % len(self._row_order)
            self._highlight_selected()

    def action_select_next(self) -> None:
        if self._row_order:
            self._selected = (self._selected + 1) % len(self._row_order)
            self._highlight_selected()

    def _selected_task_id(self) -> Optional[str]:
        if not self._row_order:
            return None
        row_id = self._row_order[self._selected]
        return self._task_ids.get(row_id)

    def action_pause_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id:
            self.run_worker(self.app.aria2_engine.pause(task_id), exclusive=False)

    def action_resume_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id:
            self.run_worker(self.app.aria2_engine.resume(task_id), exclusive=False)

    def action_cancel_selected(self) -> None:
        task_id = self._selected_task_id()
        if task_id:
            self.run_worker(self.app.aria2_engine.cancel(task_id), exclusive=False)

    def action_abort_all(self) -> None:
        for task_id in list(self._task_ids.values()):
            self.run_worker(self.app.aria2_engine.cancel(task_id), exclusive=False)
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "abort-btn":
            self.action_abort_all()


def _error_status():
    from ...engines.base import TaskStatus

    return TaskStatus.ERROR


class _ConfigView:
    """Adapts the shared AppConfig + a chosen Aria2SessionConfig into what Aria2Engine.process expects."""

    def __init__(self, app_config, aria2_session: Aria2SessionConfig) -> None:
        self.download_dir = app_config.download_dir
        self.aria2_session = aria2_session
