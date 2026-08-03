"""Abstract engine interface shared by the aria2 and yt-dlp backends.

The `SmartRouter` decides *which* engine handles a given input; the TUI and
the --no-tui CLI path both drive engines purely through this interface, so
neither has to know whether a task is an HTTP file, a torrent, or a
yt-dlp-extracted video underneath.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional


class TaskStatus(str, Enum):
    QUEUED = "queued"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETE = "complete"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class ProgressUpdate:
    """A single snapshot of a task's progress, pushed to subscribers via callback."""

    task_id: str
    name: str
    status: TaskStatus
    downloaded_bytes: int = 0
    total_bytes: Optional[int] = None
    speed_bytes_per_sec: float = 0.0
    eta_seconds: Optional[int] = None
    message: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)  # peers/seeds, fragment counts, etc.

    @property
    def percent(self) -> Optional[float]:
        if not self.total_bytes:
            return None
        return min(100.0, 100.0 * self.downloaded_bytes / self.total_bytes)


@dataclass
class DownloadResult:
    """Final outcome of one `Engine.process()` call, for the post-download dashboard."""

    task_id: str
    name: str
    status: TaskStatus
    output_paths: List[str] = field(default_factory=list)
    total_bytes: int = 0
    elapsed_seconds: float = 0.0
    average_speed: float = 0.0
    error: Optional[str] = None


ProgressCallback = Callable[[ProgressUpdate], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]


class Engine(ABC):
    """Uniform interface every download backend implements."""

    name: str = "engine"

    @abstractmethod
    async def process(
        self,
        input_value: str,
        config: Any,
        progress_callback: ProgressCallback,
        log_callback: Optional[LogCallback] = None,
    ) -> DownloadResult:
        """Download `input_value`, reporting progress via `progress_callback`.

        `config` is the shared `AppConfig` (plus, by convention, an
        engine-specific override attribute — see `aria2_session` on
        `Aria2Engine.process` and `ytdlp_choice` on `YtdlpEngine.process`).
        """
        raise NotImplementedError

    @abstractmethod
    async def pause(self, task_id: str) -> None: ...

    @abstractmethod
    async def resume(self, task_id: str) -> None: ...

    @abstractmethod
    async def cancel(self, task_id: str) -> None: ...

    async def shutdown(self) -> None:
        """Release engine-wide resources (daemons, sessions). Default: no-op."""
        return None
