"""A labeled progress row: name, bar, and a speed/ETA/status meta line.

One instance represents one active task in the aria2/yt-dlp progress
screens. `update_progress` is called from the screen each time a
`ProgressUpdate` arrives — the widget itself holds no engine knowledge.
"""
from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, ProgressBar

from ...utils.file_utils import human_size


def _format_eta(seconds: Optional[int]) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class EnhancedProgress(Widget):
    """One row in an active-downloads list."""

    DEFAULT_CSS = """
    EnhancedProgress {
        height: 3;
        margin-bottom: 1;
        padding: 0 1;
        background: $panel 50%;
        border: round $panel;
    }
    EnhancedProgress > #epname {
        width: 1fr;
        text-style: bold;
    }
    EnhancedProgress > #epmeta {
        color: $text-muted;
    }
    """

    def __init__(self, task_id: str, name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.task_id = task_id
        self.display_name = name

    def compose(self) -> ComposeResult:
        yield Label(self.display_name, id="epname")
        yield ProgressBar(total=100, show_eta=False, id="epbar")
        yield Label("queued", id="epmeta")

    def rename(self, name: str) -> None:
        if name and name != self.display_name:
            self.display_name = name
            self.query_one("#epname", Label).update(name)

    def update_progress(
        self,
        *,
        percent: Optional[float],
        downloaded: int,
        total: Optional[int],
        speed: float,
        eta: Optional[int],
        status: str,
        extra_note: str = "",
    ) -> None:
        bar = self.query_one("#epbar", ProgressBar)
        if percent is not None:
            bar.update(total=100, progress=percent)
        else:
            bar.update(total=None)  # indeterminate — total unknown yet
        bar.styles.color = "#00d68f" if (percent or 0) >= 100 else "#007bff"

        size_txt = human_size(downloaded) + (f" / {human_size(total)}" if total else "")
        note = f"  ·  {extra_note}" if extra_note else ""
        meta = f"{size_txt}  ·  {human_size(speed)}/s  ·  ETA {_format_eta(eta)}  ·  {status}{note}"
        self.query_one("#epmeta", Label).update(meta)
