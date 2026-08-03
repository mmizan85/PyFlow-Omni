"""Main Textual application: screen orchestration, shared engines/config, global bindings.

Also owns the two background power features that don't belong to any one
screen: the Bandwidth Scheduler (time-windowed speed caps) and the optional
Clipboard Monitor (spec 4.8).
"""
from __future__ import annotations

import sys
import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from ..config_manager import AppConfig, ConfigManager
from ..engines.aria2_engine import Aria2Engine
from ..engines.base import DownloadResult
from ..engines.ytdlp_engine import YtdlpEngine
from ..router import InputKind, RouteDecision, SmartRouter
from ..utils.clipboard import get_clipboard_text, looks_like_downloadable, monitor_clipboard
from ..utils.subprocess_utils import which
from .screens.aria2_pre import Aria2PreScreen
from .screens.dashboard import DashboardScreen
from .screens.global_config import GlobalConfigScreen
from .screens.main_menu import MainMenuScreen
from .screens.ytdlp_select import YtdlpSelectScreen
from .widgets.system_monitor import SystemMonitor

logger = logging.getLogger(__name__)


def _build_theme():
    """Registers the spec's exact palette (deep dark / electric blue / vibrant green)
    as a real Textual Theme, so every screen's `$background`/`$primary`/`$success`
    etc. resolve to it automatically — no hardcoded colors scattered around."""
    from textual.theme import Theme

    return Theme(
        name="pyflow-omni-midnight",
        primary="#007bff",
        secondary="#00a8ff",
        success="#00d68f",
        warning="#f0a020",
        error="#ff4d4f",
        accent="#00d68f",
        foreground="#e6ecf5",
        background="#0a0e17",
        surface="#0f1420",
        panel="#141a2b",
        dark=True,
    )


def _build_high_contrast_theme():
    from textual.theme import Theme

    return Theme(
        name="pyflow-omni-high-contrast",
        primary="#3ea6ff",
        secondary="#3ea6ff",
        success="#00ff9c",
        warning="#ffcc00",
        error="#ff5555",
        accent="#00ff9c",
        foreground="#ffffff",
        background="#000000",
        surface="#0a0a0a",
        panel="#1a1a1a",
        dark=True,
    )

def resolve_resource_path(relative_path: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).parent / relative_path

class PyFlowOmniApp(App):
    """PyFlow Omni's Textual shell. Owns the long-lived engines, config, and schedulers."""

    CSS_PATH = resolve_resource_path("app.tcss")
    TITLE = "PyFlow Omni"
    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("ctrl+c", "quit_app", "Abort"),
        ("ctrl+d", "show_dashboard", "Dashboard"),
        ("ctrl+s", "show_settings", "Settings"),
    ]

    def __init__(self, *, initial_input: Optional[str] = None, config_manager: Optional[ConfigManager] = None,open_settings: bool = False):
        super().__init__()
        self.open_settings = open_settings
        self.config_manager = config_manager or ConfigManager()
        self.config: AppConfig = self.config_manager.load()
        self.router = SmartRouter()
        self.aria2_engine = Aria2Engine(
            aria2c_path=self.config.aria2c_path,
            max_concurrent_downloads=self.config.aria2_defaults.max_concurrent_downloads,
        )
        self.ytdlp_engine = YtdlpEngine(ffmpeg_path=self.config.ffmpeg_path)
        self.initial_input: Optional[str] = initial_input
        self.completed_results: List[DownloadResult] = []
        self._clipboard_stop_event: Optional[asyncio.Event] = None
        self._clipboard_task: Optional[asyncio.Task] = None
        self._active_bandwidth_limit: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield SystemMonitor()
        yield Footer()

    async def on_mount(self) -> None:
        for theme in (_build_theme(), _build_high_contrast_theme()):
            self.register_theme(theme)
        self.theme = "pyflow-omni-high-contrast" if self.config.high_contrast else "pyflow-omni-midnight"

        missing = self._check_dependencies()
        if missing:
            self.notify(
                f"Missing tools on PATH: {', '.join(missing)}. Related features won't work until installed.",
                severity="warning", timeout=10,
            )

        prefill = self.initial_input
        if not prefill:
            clip = get_clipboard_text()
            if clip and looks_like_downloadable(clip):
                prefill = clip
        self.push_screen(MainMenuScreen(prefill=prefill))

        if self.config.clipboard_monitor_enabled:
            self._start_clipboard_monitor()
        if self.config.bandwidth_rules:
            self.set_interval(60.0, self._apply_bandwidth_schedule)
            self._apply_bandwidth_schedule()  # apply immediately rather than waiting a minute
        if self.open_settings:
            self.action_show_settings()

    def _check_dependencies(self) -> List[str]:
        missing = []
        for tool, path in (
            ("aria2c", self.config.aria2c_path),
            ("ffmpeg", self.config.ffmpeg_path),
            ("ffprobe", self.config.ffprobe_path),
        ):
            if which(path) is None:
                missing.append(tool)
        return missing

    # -- routing -------------------------------------------------------------

    async def route_and_launch(self, raw_input: str) -> None:
        """Classify `raw_input` and push the matching engine's pre-flight screen."""
        decision = await self.router.classify_async(raw_input)
        self.config_manager.add_recent_input(raw_input)

        if decision.kind == InputKind.BATCH_FILE:
            await self._launch_batch(raw_input)
            return

        await self._launch_for_decision(decision)

    async def _launch_batch(self, path: str) -> None:
        decisions = await self.router.classify_batch(path)
        aria2_items = [d.original for d in decisions if d.engine == "aria2"]
        ytdlp_items = [d.original for d in decisions if d.engine == "ytdlp"]
        skipped = [d.original for d in decisions if d.engine is None]

        if skipped:
            self.notify(f"Skipped {len(skipped)} unrecognised line(s) in the batch file.", severity="warning")
        if not aria2_items and not ytdlp_items:
            self.notify("No downloadable links found in that batch file.", severity="warning")
            return
        if aria2_items:
            self.push_screen(Aria2PreScreen(input_value=aria2_items[0], extra_inputs=aria2_items[1:]))
        if ytdlp_items:
            self.push_screen(YtdlpSelectScreen(input_value=ytdlp_items[0], extra_inputs=ytdlp_items[1:]))

    async def _launch_for_decision(self, decision: RouteDecision) -> None:
        if decision.ambiguous or decision.engine is None:
            self.notify(f"Couldn't confidently classify: {decision.original}", severity="warning")
            return
        if decision.engine == "aria2":
            self.push_screen(Aria2PreScreen(input_value=decision.original))
        else:
            self.push_screen(YtdlpSelectScreen(input_value=decision.original))

    # -- power features --------------------------------------------------------

    def _start_clipboard_monitor(self) -> None:
        self._clipboard_stop_event = asyncio.Event()

        async def on_new_link(link: str) -> None:
            self.notify(f"New link on clipboard: {link[:60]}", timeout=5)
            top = self.screen
            if isinstance(top, MainMenuScreen):
                try:
                    top.query_one("#main-input").value = link  # type: ignore[union-attr]
                except Exception:
                    pass

        self._clipboard_task = asyncio.create_task(
            monitor_clipboard(on_new_link, interval=2.0, stop_event=self._clipboard_stop_event)
        )

    def _stop_clipboard_monitor(self) -> None:
        if self._clipboard_stop_event is not None:
            self._clipboard_stop_event.set()
        if self._clipboard_task is not None:
            self._clipboard_task.cancel()

    def _apply_bandwidth_schedule(self) -> None:
        """Compares the current local time against configured windows and pushes
        the matching speed cap to the *running* aria2 daemon, if any."""
        now = datetime.now().strftime("%H:%M")
        matching_limit = None
        for rule in self.config.bandwidth_rules:
        
            is_normal_window = rule.start <= rule.end and rule.start <= now <= rule.end
            is_overnight_window = rule.start > rule.end and (now >= rule.start or now <= rule.end)
            
            if is_normal_window or is_overnight_window:
                matching_limit = rule.limit
                break

        target = matching_limit or self.config.aria2_defaults.max_overall_download_limit
        if target != self._active_bandwidth_limit:
            self._active_bandwidth_limit = target
            self.run_worker(self.aria2_engine.apply_bandwidth_limit(target), exclusive=False)

    # -- navigation shortcuts ----------------------------------------------

    def action_show_dashboard(self) -> None:
        if not any(isinstance(s, DashboardScreen) for s in self.screen_stack):
            self.push_screen(DashboardScreen())

    def action_show_settings(self) -> None:
        if not any(isinstance(s, GlobalConfigScreen) for s in self.screen_stack):
            self.push_screen(GlobalConfigScreen())

    def action_quit_app(self) -> None:
        self.exit()

    async def on_unmount(self) -> None:
        self._stop_clipboard_monitor()
        await self.aria2_engine.shutdown()
