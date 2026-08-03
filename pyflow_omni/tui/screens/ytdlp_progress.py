"""yt-dlp active-progress screen: fragment/speed/ETA per item, fed by the progress hook."""
from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Label, RichLog

from ...engines.base import DownloadResult, ProgressUpdate, TaskStatus
from ...engines.ytdlp_engine import YtdlpChoice
from ...utils.async_utils import Throttler, retry_with_backoff
from ..widgets.enhanced_progress import EnhancedProgress


class YtdlpProgressScreen(Screen):
    """Runs one or more yt-dlp downloads concurrently and renders live progress for each."""

    BINDINGS = [
        ("x", "cancel_selected", "Cancel"),
        ("up", "select_prev", "▲"),
        ("down", "select_next", "▼"),
        ("q", "abort_all", "Abort all"),
    ]

    def __init__(self, input_values: List[str], choice: YtdlpChoice) -> None:
        super().__init__()
        self.input_values = input_values
        self.choice = choice
        self._rows: Dict[str, EnhancedProgress] = {}
        self._task_ids: Dict[str, str] = {}
        self._row_order: List[str] = []
        self._selected = 0
        self._throttler = Throttler(interval=0.15)
        self._results: List[DownloadResult] = []
        self._done_count = 0
        self._concurrency_limit: Optional[asyncio.Semaphore] = None  # sized in on_mount from config

    def compose(self) -> ComposeResult:
        yield Label(
            f"Extracting {len(self.input_values)} item(s) via yt-dlp — ↑/↓ select, x cancel", classes="section-title"
        )
        with VerticalScroll(id="rows-container", classes="glass-panel"):
            for i, value in enumerate(self.input_values):
                row_id = f"row-{i}"
                row = EnhancedProgress(task_id=row_id, name=value, id=row_id)
                self._rows[row_id] = row
                self._row_order.append(row_id)
                yield row
        yield RichLog(id="ytdlp-log", max_lines=500, wrap=False, highlight=False)
        yield Button("Abort all (q)", id="abort-btn")

    def on_mount(self) -> None:
        limit = max(1, getattr(self.app.config, "ytdlp_max_concurrent", 3))
        self._concurrency_limit = asyncio.Semaphore(limit)
        for row_id, value in zip(self._row_order, self.input_values):
            # Only the primary (first) input carries the user's playlist/clip
            # selections — batch siblings download with the preset alone.
            per_item_choice = self.choice if row_id == self._row_order[0] else YtdlpChoice(preset=self.choice.preset)
            self.run_worker(self._run_one(row_id, value, per_item_choice), exclusive=False, name=row_id)
        self._highlight_selected()

    async def _run_one(self, row_id: str, input_value: str, choice: YtdlpChoice) -> None:
        row = self._rows[row_id]
        row.update_progress(percent=None, downloaded=0, total=None, speed=0, eta=None, status="queued")
        assert self._concurrency_limit is not None

        async def on_progress(update: ProgressUpdate) -> None:
            self._task_ids[row_id] = update.task_id
            row.rename(update.name)
            if not self._throttler.should_fire(row_id) and update.status.value not in ("complete", "error", "cancelled"):
                return
            frag = update.extra.get("fragment_index")
            frag_total = update.extra.get("fragment_count")
            note = f"frag {frag}/{frag_total}" if frag and frag_total else ""
            row.update_progress(
                percent=update.percent,
                downloaded=update.downloaded_bytes,
                total=update.total_bytes,
                speed=update.speed_bytes_per_sec,
                eta=update.eta_seconds,
                status=update.status.value,
                extra_note=note,
            )

        async def on_log(line: str) -> None:
            self.query_one("#ytdlp-log", RichLog).write(line)

        config = _ConfigView(self.app.config, choice)
        log = self.query_one("#ytdlp-log", RichLog)

        async def attempt() -> DownloadResult:
            result = await self.app.ytdlp_engine.process(input_value, config, on_progress, on_log)
            if result.status == TaskStatus.ERROR:
                raise RuntimeError(result.error or "extraction failed")
            return result  # COMPLETE or CANCELLED both pass through untouched

        async def on_retry(attempt_no: int, exc: BaseException) -> None:
            log.write(f"[retry {attempt_no}/3] {input_value}: {exc}")
            row.update_progress(percent=None, downloaded=0, total=None, speed=0, eta=None,
                                 status=f"retrying ({attempt_no}/3)")

        # The semaphore guards only the actual download work — held from
        # just before it starts until it finishes, so a slot frees up for
        # the next queued item the moment this one completes rather than
        # after this method's bookkeeping tail also runs.
        async with self._concurrency_limit:
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

    def action_cancel_selected(self) -> None:
        if not self._row_order:
            return
        row_id = self._row_order[self._selected]
        task_id = self._task_ids.get(row_id)
        if task_id:
            self.run_worker(self.app.ytdlp_engine.cancel(task_id), exclusive=False)

    def action_abort_all(self) -> None:
        for task_id in list(self._task_ids.values()):
            self.run_worker(self.app.ytdlp_engine.cancel(task_id), exclusive=False)
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "abort-btn":
            self.action_abort_all()


class _ConfigView:
    """Adapts the shared AppConfig + a chosen YtdlpChoice into what YtdlpEngine.process expects."""

    def __init__(self, app_config, ytdlp_choice: YtdlpChoice) -> None:
        self.download_dir = app_config.download_dir
        self.ytdlp_output_template = app_config.ytdlp_output_template
        self.ytdlp_choice = ytdlp_choice
