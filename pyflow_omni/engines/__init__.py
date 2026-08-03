"""Download backends. Every engine implements the uniform `Engine` interface in base.py."""
from __future__ import annotations

from .aria2_engine import Aria2Engine, Aria2SessionConfig
from .base import DownloadResult, Engine, ProgressUpdate, TaskStatus
from .ytdlp_engine import YtdlpChoice, YtdlpEngine

__all__ = [
    "Engine",
    "ProgressUpdate",
    "DownloadResult",
    "TaskStatus",
    "Aria2Engine",
    "Aria2SessionConfig",
    "YtdlpEngine",
    "YtdlpChoice",
]
