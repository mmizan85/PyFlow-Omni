"""Main menu: the input panel every session starts from (spec 4.5)."""
from __future__ import annotations

from typing import Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static

_FALLBACK_LOGO = "PyFlow Omni — aria2c + yt-dlp, one router, one TUI"


def _load_logo() -> str:
    from pathlib import Path

    asset_path = Path(__file__).resolve().parent.parent.parent / "assets" / "logo.txt"
    try:
        return asset_path.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK_LOGO


class MainMenuScreen(Screen):
    """Landing screen: paste/type a link, or pull a previous one from history."""

    BINDINGS = [("ctrl+v", "paste_clipboard", "Paste")]

    def __init__(self, prefill: Optional[str] = None) -> None:
        super().__init__()
        self._prefill = prefill

    def compose(self) -> ComposeResult:
        with Vertical(classes="glass-panel"):
            yield Static(_load_logo(), id="logo")
            yield Label(
                "Paste a URL, magnet link, .torrent path, or .txt batch file:",
                classes="section-title",
            )
            with Horizontal():
                yield Input(placeholder="https:// or magnet:? or /path/to/file.torrent", id="main-input")
                yield Button("Paste", id="paste-btn")
                yield Button("Go ▶", id="go-btn", classes="accent-button")
            recent = self.app.config.recent_inputs if hasattr(self.app, "config") else []
            if recent:
                yield Label("Recent:", classes="hint-text")
                yield Select(
                    [(r[:70], r) for r in recent], id="recent-select", allow_blank=True, prompt="Pick a recent input…"
                )
            yield Label(
                "  q  quit   ·   C-v  paste clipboard   ·   Enter  go   ·   "
                "C-s  settings   ·   C-d  dashboard",
                classes="hint-text",
            )

    def on_mount(self) -> None:
        if self._prefill:
            self.query_one("#main-input", Input).value = self._prefill
            self.notify(f"Found a link on your clipboard: {self._prefill[:60]}", timeout=4)

    @on(Button.Pressed, "#paste-btn")
    def action_paste_clipboard(self) -> None:
        from ...utils.clipboard import get_clipboard_text

        text = get_clipboard_text()
        if text:
            self.query_one("#main-input", Input).value = text
        else:
            self.notify("Clipboard is empty or unavailable.", severity="warning")

    @on(Select.Changed, "#recent-select")
    def _on_recent_selected(self, event: Select.Changed) -> None:
        if event.value is not None and event.value != Select.BLANK:
            self.query_one("#main-input", Input).value = str(event.value)

    @on(Button.Pressed, "#go-btn")
    @on(Input.Submitted, "#main-input")
    def action_submit(self) -> None:
        value = self.query_one("#main-input", Input).value.strip()
        if not value:
            self.notify("Type or paste something first.", severity="warning")
            return
        self.run_worker(self.app.route_and_launch(value), exclusive=True)
