"""Filesystem helpers: atomic writes, human-readable sizes, cross-platform folder opening."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)


def human_size(num_bytes: float) -> str:
    """Format a byte count as e.g. '12.3 MB'."""
    size = float(max(num_bytes, 0))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def atomic_write_text(path: Union[str, Path], content: str, encoding: str = "utf-8") -> None:
    """Write `content` to `path` atomically: write-temp, fsync, then os.replace.

    Guarantees a reader never observes a half-written config file, even if
    the process is killed mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


async def open_in_file_manager(path: Union[str, Path]) -> None:
    """Open `path` in the OS's file manager. Best-effort; failures are logged, not raised."""
    target = str(path)
    try:
        if sys.platform.startswith("linux"):
            proc = await asyncio.create_subprocess_exec("xdg-open", target)
            await proc.wait()
        elif sys.platform == "darwin":
            proc = await asyncio.create_subprocess_exec("open", target)
            await proc.wait()
        elif sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("Could not open file manager for %s", target, exc_info=True)


def notify(message: str, *, title: str = "PyFlow Omni", command_override: str = "") -> None:
    """Fire an OS notification, using `command_override` from config if set.

    Fully best-effort and synchronous-but-fast (spawns and forgets); a
    missing notify-send/osascript must never interrupt a download.
    """
    import shlex
    import subprocess

    try:
        if command_override:
            subprocess.Popen(shlex.split(command_override) + [title, message])  # noqa: S603
        elif sys.platform.startswith("linux") and _has("notify-send"):
            subprocess.Popen(["notify-send", title, message])  # noqa: S603
        elif sys.platform == "darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.Popen(["osascript", "-e", script])  # noqa: S603
    except Exception:
        logger.debug("Notification failed", exc_info=True)


def _has(binary: str) -> bool:
    import shutil

    return shutil.which(binary) is not None
