"""yt-dlp Selection screen: title/duration/thumbnail, 8 presets, playlist picker, Manual Mode."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from ...engines.ytdlp_engine import PRESET_LABELS, YtdlpChoice, list_formats


class YtdlpSelectScreen(Screen):
    """Probe the URL, then let the user pick one of the 8 presets or go Manual."""

    BINDINGS = [("m", "manual_mode", "Manual"), ("escape", "back", "Back")]

    def __init__(self, input_value: str, extra_inputs: Optional[List[str]] = None) -> None:
        super().__init__()
        self.input_value = input_value
        self.extra_inputs = extra_inputs or []
        self._info: Optional[Dict[str, Any]] = None
        self._manual_open = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="glass-panel"):
            yield Label("yt-dlp — media extraction", classes="section-title")
            yield Static("Fetching video info…", id="info-line")
            yield Static("", id="thumb-line", classes="hint-text")

            option_list = OptionList(id="preset-list")
            yield option_list

            with Vertical(id="playlist-box"):
                yield Label("Playlist detected — choose items:", classes="section-title")
                yield SelectionList(id="playlist-list")

            with Vertical(id="clip-box"):
                yield Label("Clip range (seconds):", classes="section-title")
                with Horizontal():
                    yield Label("Start:")
                    yield Input(placeholder="0", id="clip-start")
                    yield Label("End:")
                    yield Input(placeholder="30", id="clip-end")

            with Vertical(id="manual-box"):
                yield Label("Manual format string (yt-dlp selector syntax):", classes="section-title")
                yield Input(id="manual-format")

            with Horizontal():
                yield Button("Start ▶", id="start-btn", classes="accent-button")
                yield Button("Manual (M)", id="manual-btn")
                yield Button("Back", id="back-btn")

    def on_mount(self) -> None:
        for widget_id in ("playlist-box", "clip-box", "manual-box"):
            self.query_one(f"#{widget_id}").display = False
        option_list = self.query_one("#preset-list", OptionList)
        for preset, label in PRESET_LABELS.items():
            option_list.add_option(Option(f"{preset}. {label}", id=str(preset)))
        # Currently the first focusable widget in DOM order happens to be
        # this visible OptionList, which sidesteps the hidden-widget-steals-
        # focus bug found in Aria2PreScreen — but that's DOM-order luck, not
        # a guarantee. Focus it explicitly so this stays correct even if the
        # layout above it changes later.
        option_list.focus()
        self.run_worker(self._probe(), exclusive=True)

    async def _probe(self) -> None:
        try:
            self._info = await self.app.ytdlp_engine.probe(self.input_value)
        except Exception as exc:
            self.query_one("#info-line", Static).update(
                f"[yellow]Couldn't fetch metadata ({exc}). You can still pick a preset "
                "or use Manual Mode with a raw format string.[/yellow]"
            )
            return

        info = self._info or {}
        title = info.get("title", self.input_value)
        duration = info.get("duration")
        duration_txt = f"  ·  {int(duration // 60)}:{int(duration % 60):02d}" if duration else ""
        self.query_one("#info-line", Static).update(f"[b]{title}[/b]{duration_txt}")
        thumb = info.get("thumbnail")
        self.query_one("#thumb-line", Static).update(f"thumbnail: {thumb}" if thumb else "")

        entries = info.get("entries")
        if entries:
            playlist_list = self.query_one("#playlist-list", SelectionList)
            for i, entry in enumerate(entries, start=1):
                if entry is None:
                    continue
                label = f"{i}. {entry.get('title', entry.get('id', 'unknown'))}"
                playlist_list.add_option(Selection(label, str(i), True))
            self.query_one("#playlist-box").display = True

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back-btn")
    def _back_pressed(self) -> None:
        self.action_back()

    def action_manual_mode(self) -> None:
        self._manual_open = not self._manual_open
        self.query_one("#manual-box").display = self._manual_open
        if self._manual_open and not self.query_one("#manual-format", Input).value:
            self._prefill_manual_format()

    @on(Button.Pressed, "#manual-btn")
    def _manual_pressed(self) -> None:
        self.action_manual_mode()

    def _prefill_manual_format(self) -> None:
        if not self._info:
            return
        formats = list_formats(self._info)
        video_formats = [f for f in formats if f.get("vcodec") not in (None, "none")]
        best = max(video_formats, key=lambda f: f.get("tbr") or 0, default=None)
        if best:
            self.query_one("#manual-format", Input).value = f"{best['format_id']}+bestaudio/best"

    @on(OptionList.OptionSelected, "#preset-list")
    def _preset_chosen(self, event: OptionList.OptionSelected) -> None:
        self._start(preset=int(event.option.id))

    @on(Button.Pressed, "#start-btn")
    def _start_pressed(self) -> None:
        if self._manual_open:
            manual = self.query_one("#manual-format", Input).value.strip()
            if not manual:
                self.notify("Enter a format string, or close Manual Mode to pick a preset.", severity="warning")
                return
            self._start(preset=1, manual_format=manual)
        else:
            highlighted = self.query_one("#preset-list", OptionList).highlighted
            preset = int(highlighted) + 1 if highlighted is not None else 1
            self._start(preset=preset)

    def _selected_playlist_items(self) -> Optional[str]:
        if not self.query_one("#playlist-box").display:
            return None
        playlist_list = self.query_one("#playlist-list", SelectionList)
        selected = sorted(int(v) for v in playlist_list.selected)
        return ",".join(str(v) for v in selected) if selected else None

    def _start(self, preset: int, manual_format: Optional[str] = None) -> None:
        from .ytdlp_progress import YtdlpProgressScreen

        clip_start = clip_end = None
        if preset == 8:
            try:
                clip_start = float(self.query_one("#clip-start", Input).value or 0)
                clip_end = float(self.query_one("#clip-end", Input).value or 0)
            except ValueError:
                self.notify("Clip start/end must be numbers (seconds).", severity="error")
                return

        choice = YtdlpChoice(
            preset=preset,
            clip_start=clip_start,
            clip_end=clip_end,
            manual_format=manual_format,
            playlist_items=self._selected_playlist_items(),
        )
        all_inputs = [self.input_value, *self.extra_inputs]
        self.app.pop_screen()
        self.app.push_screen(YtdlpProgressScreen(input_values=all_inputs, choice=choice))
