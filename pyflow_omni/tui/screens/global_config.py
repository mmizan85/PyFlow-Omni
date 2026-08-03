"""Global Configuration screen: edit permanent settings, persisted via ConfigManager (spec 4.6)."""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static, Switch


class GlobalConfigScreen(Screen):
    """Permanent settings editor — reachable from the main app or `pyflow-omni --config`."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        cfg = self.app.config
        with VerticalScroll(classes="glass-panel", id="settings-scroll"):
            yield Label("Global Settings", classes="section-title")

            yield Label("Default download directory:")
            yield Input(cfg.download_dir, id="cfg-download-dir")

            yield Label("aria2c / ffmpeg / ffprobe paths:")
            with Horizontal():
                yield Input(cfg.aria2c_path, id="cfg-aria2c-path", placeholder="aria2c")
                yield Input(cfg.ffmpeg_path, id="cfg-ffmpeg-path", placeholder="ffmpeg")
                yield Input(cfg.ffprobe_path, id="cfg-ffprobe-path", placeholder="ffprobe")

            yield Label("yt-dlp output template:")
            yield Input(cfg.ytdlp_output_template, id="cfg-ytdlp-template")

            yield Label("Max concurrent yt-dlp downloads:")
            yield Input(str(cfg.ytdlp_max_concurrent), id="cfg-ytdlp-concurrent")

            yield Label("Theme:")
            yield Select(
                [("Midnight (dark blue/green)", "midnight"), ("High contrast", "high-contrast")],
                value=cfg.theme, id="cfg-theme", allow_blank=False,
            )

            yield Label("Proxy (HTTP / SOCKS5) — leave blank for none:")
            with Horizontal():
                yield Input(cfg.proxy_http, id="cfg-proxy-http", placeholder="http://host:port")
                yield Input(cfg.proxy_socks5, id="cfg-proxy-socks5", placeholder="socks5://host:port")

            yield Label("Notification command (blank = auto-detect notify-send/osascript):")
            yield Input(cfg.notification_command, id="cfg-notify-cmd")

            with Horizontal():
                yield Label("Clipboard monitoring:")
                yield Switch(value=cfg.clipboard_monitor_enabled, id="cfg-clipboard-monitor")

            yield Label("Aria2 permanent defaults:", classes="section-title")
            with Horizontal():
                yield Label("Max concurrent (aria2):")
                yield Input(str(cfg.aria2_defaults.max_concurrent_downloads), id="cfg-a2-concurrent")
                yield Label("Conns/server:")
                yield Input(str(cfg.aria2_defaults.max_connection_per_server), id="cfg-a2-connections")
                yield Label("Split:")
                yield Input(str(cfg.aria2_defaults.split), id="cfg-a2-split")
            with Horizontal():
                yield Label("Download limit:")
                yield Input(cfg.aria2_defaults.max_overall_download_limit, id="cfg-a2-dllimit")
                yield Label("Upload limit:")
                yield Input(cfg.aria2_defaults.max_upload_limit, id="cfg-a2-ullimit")
                yield Label("Seed ratio:")
                yield Input(str(cfg.aria2_defaults.seed_ratio), id="cfg-a2-seedratio")

            yield Label("Bandwidth Scheduler (time-based speed caps):", classes="section-title")
            yield Static(self._format_rules(), id="rules-display")
            with Horizontal():
                yield Input(placeholder="09:00", id="rule-start")
                yield Input(placeholder="18:00", id="rule-end")
                yield Input(placeholder="2M", id="rule-limit")
                yield Button("Add rule", id="add-rule-btn")

        # Docked *outside* the VerticalScroll above and pinned to the bottom
        # of the screen, so Save/Cancel are always reachable by mouse click
        # without scrolling — a real concern here since this form is taller
        # than a traditional 80x24 terminal.
        with Horizontal(id="settings-actionbar"):
            yield Button("Save", id="save-btn", classes="accent-button")
            yield Button("Cancel", id="cancel-btn")

    def _format_rules(self) -> str:
        rules = self.app.config.bandwidth_rules
        if not rules:
            return "(no rules — full speed at all times)"
        return "\n".join(f"  {r.start}–{r.end}  ->  {r.limit}" for r in rules)

    @on(Button.Pressed, "#add-rule-btn")
    def _add_rule(self) -> None:
        from ...config_manager import BandwidthRule

        start = self.query_one("#rule-start", Input).value.strip() or "09:00"
        end = self.query_one("#rule-end", Input).value.strip() or "18:00"
        limit = self.query_one("#rule-limit", Input).value.strip() or "2M"
        self.app.config.bandwidth_rules.append(BandwidthRule(start=start, end=end, limit=limit))
        self.query_one("#rules-display", Static).update(self._format_rules())

    def action_cancel(self) -> None:
        self.app.config_manager.load(force=True)  # discard in-memory edits
        self.app.pop_screen()

    @on(Button.Pressed, "#cancel-btn")
    def _cancel_pressed(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#save-btn")
    def _save_pressed(self) -> None:
        cfg = self.app.config
        try:
            cfg.download_dir = self.query_one("#cfg-download-dir", Input).value.strip()
            cfg.aria2c_path = self.query_one("#cfg-aria2c-path", Input).value.strip() or "aria2c"
            cfg.ffmpeg_path = self.query_one("#cfg-ffmpeg-path", Input).value.strip() or "ffmpeg"
            cfg.ffprobe_path = self.query_one("#cfg-ffprobe-path", Input).value.strip() or "ffprobe"
            cfg.ytdlp_output_template = self.query_one("#cfg-ytdlp-template", Input).value.strip()
            cfg.ytdlp_max_concurrent = int(self.query_one("#cfg-ytdlp-concurrent", Input).value)
            cfg.theme = str(self.query_one("#cfg-theme", Select).value)
            cfg.proxy_http = self.query_one("#cfg-proxy-http", Input).value.strip()
            cfg.proxy_socks5 = self.query_one("#cfg-proxy-socks5", Input).value.strip()
            cfg.notification_command = self.query_one("#cfg-notify-cmd", Input).value.strip()
            cfg.clipboard_monitor_enabled = self.query_one("#cfg-clipboard-monitor", Switch).value

            cfg.aria2_defaults.max_concurrent_downloads = int(self.query_one("#cfg-a2-concurrent", Input).value)
            cfg.aria2_defaults.max_connection_per_server = int(self.query_one("#cfg-a2-connections", Input).value)
            cfg.aria2_defaults.split = int(self.query_one("#cfg-a2-split", Input).value)
            cfg.aria2_defaults.max_overall_download_limit = self.query_one("#cfg-a2-dllimit", Input).value.strip()
            cfg.aria2_defaults.max_upload_limit = self.query_one("#cfg-a2-ullimit", Input).value.strip()
            cfg.aria2_defaults.seed_ratio = float(self.query_one("#cfg-a2-seedratio", Input).value)
        except ValueError as exc:
            self.notify(f"Invalid value: {exc}", severity="error")
            return

        self.app.config_manager.save(cfg)
        self.notify("Settings saved.", timeout=3)
        if len(self.app.screen_stack) > 1:
            self.app.pop_screen()
