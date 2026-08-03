"""Header widget showing live CPU% and memory usage, refreshed on a timer."""
from __future__ import annotations

from textual.widgets import Static


class SystemMonitor(Static):
    """Small always-on resource readout, docked in the header row."""

    DEFAULT_CSS = """
    SystemMonitor {
        width: auto;
        color: $text-muted;
        padding: 0 2;
    }
    """

    def on_mount(self) -> None:
        self.set_interval(2.0, self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            self.update(f"CPU {cpu:4.1f}%  ·  MEM {mem.percent:4.1f}%")
        except Exception:
            self.update("CPU --.-%  ·  MEM --.-%")
