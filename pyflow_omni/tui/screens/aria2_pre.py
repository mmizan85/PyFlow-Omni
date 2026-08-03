"""Aria2 pre-download screen: show defaults, offer Quick Config, then launch (spec 4.3)."""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Static, Switch

from ...engines.aria2_engine import Aria2SessionConfig


class Aria2PreScreen(Screen):
    """Shown before an aria2 download starts: defaults panel + optional customisation."""

    BINDINGS = [("c", "toggle_customise", "Customise"), ("escape", "back", "Back")]

    def __init__(self, input_value: str, extra_inputs: list[str] | None = None) -> None:
        super().__init__()
        self.input_value = input_value
        self.extra_inputs = extra_inputs or []
        self._customising = False

    def compose(self) -> ComposeResult:
        defaults = self.app.config.aria2_defaults
        with Vertical(classes="glass-panel"):
            if self.extra_inputs:
                yield Label(f"Aria2 — {1 + len(self.extra_inputs)} items queued", classes="section-title")
                yield Static(f"[b]First:[/b] {self.input_value}  [dim](+{len(self.extra_inputs)} more)[/dim]")
            else:
                yield Label("Aria2 — file / torrent / magnet download", classes="section-title")
                yield Static(f"[b]Input:[/b] {self.input_value}")
            yield Static(
                f"max-connection-per-server={defaults.max_connection_per_server}   "
                f"split={defaults.split}   "
                f"download-limit={defaults.max_overall_download_limit}   "
                f"upload-limit={defaults.max_upload_limit}   "
                f"seed-ratio={defaults.seed_ratio}",
                classes="hint-text",
            )
            yield Label("Press Enter to start with defaults, or 'C' to customise this session.", classes="hint-text")

            with Vertical(id="quick-config", classes="glass-panel"):
                yield Label("Session Quick Config (this batch only — not saved)", classes="section-title")
                with Horizontal():
                    yield Label("Connections/server:")
                    yield Input(str(defaults.max_connection_per_server), id="qc-connections")
                with Horizontal():
                    yield Label("Split:")
                    yield Input(str(defaults.split), id="qc-split")
                with Horizontal():
                    yield Label("Download limit:")
                    yield Input(defaults.max_overall_download_limit, id="qc-dllimit")
                with Horizontal():
                    yield Label("Upload limit:")
                    yield Input(defaults.max_upload_limit, id="qc-ullimit")
                with Horizontal():
                    yield Label("Seed ratio:")
                    yield Input(str(defaults.seed_ratio), id="qc-seedratio")
                with Horizontal():
                    yield Label("Pause metadata only:")
                    yield Switch(value=False, id="qc-pause-metadata")

            with Horizontal():
                yield Button("Start ▶", id="start-btn", classes="accent-button")
                yield Button("Customise (C)", id="customise-btn")
                yield Button("Back", id="back-btn")

    def on_mount(self) -> None:
        self.query_one("#quick-config").display = False
        # Textual's default auto-focus picks the first focusable widget in
        # DOM order, which is one of the (hidden) Quick Config Inputs here —
        # display=False doesn't exclude it from that initial scan. Left
        # alone, it silently eats every keystroke typed on this screen,
        # including the 'C' meant to toggle Quick Config open. Focus the
        # visible default action instead.
        self.query_one("#start-btn", Button).focus()

    def action_toggle_customise(self) -> None:
        self._customising = not self._customising
        quick_config = self.query_one("#quick-config")
        quick_config.display = self._customising
        quick_config.disabled = not self._customising

    @on(Button.Pressed, "#customise-btn")
    def _customise_pressed(self) -> None:
        self.action_toggle_customise()

    def action_back(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#back-btn")
    def _back_pressed(self) -> None:
        self.action_back()

    def _build_session_config(self) -> Aria2SessionConfig:
        if not self._customising:
            return Aria2SessionConfig.from_aria2_defaults(self.app.config.aria2_defaults)
        try:
            return Aria2SessionConfig(
                max_connection_per_server=int(self.query_one("#qc-connections", Input).value or 8),
                split=int(self.query_one("#qc-split", Input).value or 8),
                max_overall_download_limit=self.query_one("#qc-dllimit", Input).value or "0",
                max_upload_limit=self.query_one("#qc-ullimit", Input).value or "1M",
                seed_ratio=float(self.query_one("#qc-seedratio", Input).value or 1.0),
                pause_metadata=self.query_one("#qc-pause-metadata", Switch).value,
            )
        except ValueError as exc:
            self.notify(f"Invalid Quick Config value: {exc}", severity="error")
            return Aria2SessionConfig.from_aria2_defaults(self.app.config.aria2_defaults)

    @on(Button.Pressed, "#start-btn")
    def _start_pressed(self) -> None:
        self.action_start()

    def action_start(self) -> None:
        from .aria2_progress import Aria2ProgressScreen

        session_config = self._build_session_config()
        all_inputs = [self.input_value, *self.extra_inputs]
        self.app.pop_screen()
        self.app.push_screen(Aria2ProgressScreen(input_values=all_inputs, session_config=session_config))
