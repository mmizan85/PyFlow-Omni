"""Headless TUI tests via Textual's run_test() pilot.

Navigation and layout tests need no network or aria2c — only `test_real_download_
through_the_ui` spawns an actual aria2c daemon and downloads a real file, so
that one alone is marked `network` and skipped where either is unavailable.
"""
from __future__ import annotations

import asyncio
import os

import pytest
from textual.widgets import Button, DataTable, Input

from pyflow_omni.config_manager import ConfigManager
from pyflow_omni.engines.base import DownloadResult, TaskStatus
from pyflow_omni.engines.ytdlp_engine import YtdlpChoice
from pyflow_omni.tui.app import PyFlowOmniApp
from pyflow_omni.tui.screens.aria2_pre import Aria2PreScreen
from pyflow_omni.tui.screens.aria2_progress import Aria2ProgressScreen
from pyflow_omni.tui.screens.dashboard import DashboardScreen
from pyflow_omni.tui.screens.global_config import GlobalConfigScreen
from pyflow_omni.tui.screens.main_menu import MainMenuScreen
from pyflow_omni.tui.screens.ytdlp_select import YtdlpSelectScreen
from pyflow_omni.tui.screens.ytdlp_progress import YtdlpProgressScreen


async def _wait_until(predicate, pilot, timeout=8.0, step=0.1):
    elapsed = 0.0
    while elapsed < timeout:
        await pilot.pause()
        if predicate():
            return True
        await asyncio.sleep(step)
        elapsed += step
    return False


@pytest.fixture
def app(config_dir):
    cm = ConfigManager(config_dir=config_dir)
    return PyFlowOmniApp(config_manager=cm)


async def test_boots_to_main_menu_with_registered_theme(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MainMenuScreen)
        assert app.theme == "pyflow-omni-midnight"


async def test_no_widget_overflows_the_screen_on_main_menu(app):
    """Regression test for a real bug: Input defaults to width:100%, which
    inside a Horizontal row pushes sibling Buttons off-screen."""
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause()
        screen_w = app.screen.size.width
        for widget in app.screen.query(Input):
            assert widget.region.x + widget.region.width <= screen_w, widget.id
        for widget in app.screen.query(Button):
            assert widget.region.x + widget.region.width <= screen_w, widget.id


async def test_no_widget_overflows_aria2_quick_config(app):
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        app.push_screen(Aria2PreScreen(input_value="magnet:?xt=urn:btih:AAAA", extra_inputs=["magnet:?xt=urn:btih:BBBB"]))
        await pilot.pause()
        await pilot.press("c")  # reveal Session Quick Config
        await pilot.pause()
        screen_w = app.screen.size.width
        for widget in app.screen.query(Input):
            assert widget.region.x + widget.region.width <= screen_w, widget.id


async def test_no_widget_overflows_global_config(app):
    async with app.run_test(size=(100, 50)) as pilot:
        await pilot.pause()
        app.push_screen(GlobalConfigScreen())
        await pilot.pause()
        screen_w = app.screen.size.width
        for widget in app.screen.query(Input):
            assert widget.region.width > 0
            assert widget.region.x + widget.region.width <= screen_w, widget.id


async def test_bare_letter_typed_into_focused_input_does_not_trigger_global_action(app):
    """Regression test: 's' used to be a global Ctrl-less binding that a
    focused Input would swallow as text instead — switched to Ctrl+S. This
    confirms plain typing lands in the input rather than doing nothing/both."""
    async with app.run_test() as pilot:
        await pilot.pause()
        main_input = app.screen.query_one("#main-input", Input)
        main_input.value = ""
        await pilot.press("s")
        await pilot.pause()
        assert main_input.value == "s"
        assert isinstance(app.screen, MainMenuScreen)  # did NOT navigate away


async def test_ctrl_s_opens_settings_regardless_of_input_focus(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        ok = await _wait_until(lambda: isinstance(app.screen, GlobalConfigScreen), pilot)
        assert ok


async def test_settings_save_persists_to_disk(app, config_dir):
    async with app.run_test() as pilot:  # default (80, 24) — exercises the docked-actionbar fix
        await pilot.pause()
        await pilot.press("ctrl+s")
        await _wait_until(lambda: isinstance(app.screen, GlobalConfigScreen), pilot)

        app.screen.query_one("#cfg-download-dir", Input).value = "/tmp/somewhere-else"
        await pilot.click("#save-btn")
        ok = await _wait_until(lambda: isinstance(app.screen, MainMenuScreen), pilot)
        assert ok
        assert app.config.download_dir == "/tmp/somewhere-else"

    reloaded = ConfigManager(config_dir=config_dir).load()
    assert reloaded.download_dir == "/tmp/somewhere-else"


async def test_settings_cancel_discards_in_memory_edits(app):
    async with app.run_test() as pilot:
        await pilot.pause()
        original = app.config.download_dir
        await pilot.press("ctrl+s")
        await _wait_until(lambda: isinstance(app.screen, GlobalConfigScreen), pilot)

        app.screen.query_one("#cfg-download-dir", Input).value = "/tmp/should-not-stick"
        await pilot.click("#cancel-btn")
        ok = await _wait_until(lambda: isinstance(app.screen, MainMenuScreen), pilot)
        assert ok
        assert app.config.download_dir == original


async def test_settings_action_bar_stays_reachable_on_a_short_80x24_terminal(app):
    """Regression test for a real bug: with 20+ settings rows inside a
    VerticalScroll, Save/Cancel used to scroll out of reach on a
    traditional 80x24 terminal. They're now docked outside the scroll
    region — this confirms both buttons render fully on-screen at that size."""
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await _wait_until(lambda: isinstance(app.screen, GlobalConfigScreen), pilot)
        screen_w, screen_h = app.screen.size.width, app.screen.size.height
        for button_id in ("#save-btn", "#cancel-btn"):
            region = app.screen.query_one(button_id).region
            assert region.x + region.width <= screen_w
            assert region.y + region.height <= screen_h


async def test_multi_input_rows_do_not_collapse_to_zero_height(app):
    """Regression test for a real bug (found via a user-supplied screenshot):
    Textual's Horizontal defaults to height:1fr. Inside a VerticalScroll with
    many sibling rows all fighting over the same limited flexible-height
    budget, every multi-field Horizontal row (paths, proxy, aria2 defaults,
    bandwidth rule) collapsed to ~1 row tall, clipping its 3-row Input
    children down to an unreadable sliver of just their top border. A bare
    width-overflow check would never catch this — it needs an explicit
    height assertion."""
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        app.push_screen(GlobalConfigScreen())
        await pilot.pause()
        multi_input_row_fields = (
            "cfg-aria2c-path", "cfg-ffmpeg-path", "cfg-ffprobe-path",
            "cfg-proxy-http", "cfg-proxy-socks5",
            "cfg-a2-concurrent", "cfg-a2-connections", "cfg-a2-split",
            "cfg-a2-dllimit", "cfg-a2-ullimit", "cfg-a2-seedratio",
        )
        for field_id in multi_input_row_fields:
            region = app.screen.query_one(f"#{field_id}").region
            assert region.height == 3, f"{field_id} collapsed to height={region.height}"
            assert region.width > 5, f"{field_id} collapsed to width={region.width}"


async def test_hidden_section_does_not_steal_default_focus(app):
    """Regression test for a real bug: Aria2PreScreen's Session Quick Config
    is display=False until toggled, but Textual's auto-focus-first-widget
    picks the first focusable widget in DOM order regardless of display
    state — which was one of the hidden Quick Config Inputs. That silently
    ate every keystroke typed on the screen, including the 'C' meant to
    reveal it, so the toggle appeared completely unresponsive."""
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause()
        app.push_screen(Aria2PreScreen(input_value="magnet:?xt=urn:btih:AAAA"))
        await pilot.pause()

        assert not isinstance(app.focused, Input), (
            f"default focus landed on a (possibly hidden) Input: {app.focused!r}"
        )

        qc = app.screen.query_one("#quick-config")
        assert qc.display is False
        await pilot.press("c")
        await pilot.pause()
        assert qc.display is True, "'c' keypress never reached the toggle action"


async def test_ytdlp_select_degrades_gracefully_when_probe_fails(app):
    """Simulates the real sandboxed-network failure mode without waiting on
    a real timeout — probe() raises immediately, screen must stay usable."""

    async def fake_probe(url):
        raise ConnectionError("simulated: no network access")

    app.ytdlp_engine.probe = fake_probe

    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(YtdlpSelectScreen(input_value="https://www.youtube.com/watch?v=x"))
        await _wait_until(lambda: isinstance(app.screen, YtdlpSelectScreen), pilot)

        from textual.widgets import OptionList

        ok = await _wait_until(
            lambda: "Couldn't fetch metadata" in str(app.screen.query_one("#info-line").content), pilot
        )
        assert ok
        assert app.screen.query_one("#preset-list", OptionList).option_count == 8


@pytest.mark.network
async def test_real_download_through_the_ui(app, config_dir, require_aria2c, require_network, download_dir):
    """The strongest test in this suite: drives an actual download entirely
    through the real UI — routing, pre-flight, worker, progress, dashboard."""
    app.config.download_dir = str(download_dir)

    async with app.run_test(size=(100, 45)) as pilot:
        await pilot.pause()
        main_input = app.screen.query_one("#main-input", Input)
        main_input.value = "https://raw.githubusercontent.com/git/git/master/README.md"
        await pilot.click("#go-btn")

        ok = await _wait_until(lambda: isinstance(app.screen, Aria2PreScreen), pilot)
        assert ok
        await pilot.click("#start-btn")

        ok = await _wait_until(lambda: isinstance(app.screen, Aria2ProgressScreen), pilot)
        assert ok

        ok = await _wait_until(lambda: bool(app.completed_results), pilot, timeout=10)
        assert ok
        result = app.completed_results[0]
        assert result.status.value == "complete"
        assert os.path.exists(result.output_paths[0])

        await pilot.press("ctrl+d")
        ok = await _wait_until(lambda: isinstance(app.screen, DashboardScreen), pilot)
        assert ok
        assert app.screen.query_one("#results-table", DataTable).row_count == 1


async def test_ytdlp_concurrency_is_actually_capped(app):
    """Regression test for a real gap: `ytdlp_max_concurrent` existed in
    config and was user-editable, but nothing ever enforced it — every
    queued item fired at once, uncapped. Verifies the semaphore actually
    limits how many downloads run *simultaneously*, using a fake engine
    call so this doesn't depend on real network."""
    app.config.ytdlp_max_concurrent = 2
    concurrent_count = 0
    max_concurrent_seen = 0
    lock = asyncio.Lock()

    async def fake_process(input_value, config, progress_callback, log_callback=None):
        nonlocal concurrent_count, max_concurrent_seen
        async with lock:
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
        await asyncio.sleep(0.2)
        async with lock:
            concurrent_count -= 1
        return DownloadResult(task_id=input_value, name=input_value, status=TaskStatus.COMPLETE)

    app.ytdlp_engine.process = fake_process

    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        urls = [f"https://example.com/video-{i}" for i in range(6)]
        app.push_screen(YtdlpProgressScreen(input_values=urls, choice=YtdlpChoice(preset=1)))

        ok = await _wait_until(lambda: len(app.completed_results) == 6, pilot, timeout=10)
        assert ok, f"only {len(app.completed_results)}/6 completed"
        assert max_concurrent_seen <= 2, f"saw {max_concurrent_seen} run simultaneously, cap was 2"
        assert max_concurrent_seen == 2, "cap should actually bind with 6 queued items and a limit of 2"


async def test_dashboard_shows_stat_cards_and_format_column(app):
    from pyflow_omni.tui.screens.dashboard import DashboardScreen

    app.completed_results = [
        DownloadResult(task_id="1", name="clip.mp4", status=TaskStatus.COMPLETE,
                        output_paths=["/tmp/clip.mp4"], total_bytes=1000, elapsed_seconds=1.0, average_speed=1000),
        DownloadResult(task_id="2", name="broken.zip", status=TaskStatus.ERROR, output_paths=[], total_bytes=0, elapsed_seconds=0.5),
    ]
    async with app.run_test(size=(130, 45)) as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen())
        await pilot.pause()

        screen_w = app.screen.size.width
        for card_id in ("#stat-total", "#stat-ok", "#stat-failed", "#stat-size", "#stat-time"):
            region = app.screen.query_one(card_id).region
            assert region.width > 3, f"{card_id} collapsed"
            assert region.x + region.width <= screen_w, f"{card_id} overflows"

        table = app.screen.query_one("#results-table", DataTable)
        column_labels = [str(c.label) for c in table.columns.values()]
        assert "Format" in column_labels, "the spec asked for a Format column and it's missing"
        assert table.row_count == 2
