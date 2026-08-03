"""Subprocess helpers: spawning, line-streaming, port discovery, and graceful shutdown.

Centralised here so every place that manages a child process (the aria2c
daemon today, potentially others later) terminates it the same safe way and
never blocks the event loop while waiting on it.
"""
from __future__ import annotations

import asyncio
import contextlib
import shutil
import socket
from typing import AsyncIterator, Optional


def which(binary: str) -> Optional[str]:
    """Cross-platform PATH lookup for a binary name or absolute path."""
    return shutil.which(binary)


def find_free_port(preferred: Optional[int] = None) -> int:
    """Return a free TCP port on localhost, trying `preferred` first if given."""
    if preferred is not None:
        with contextlib.suppress(OSError):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", preferred))
                return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def stream_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """Yield decoded, stripped lines from an asyncio stream as they arrive."""
    while True:
        line = await stream.readline()
        if not line:
            return
        yield line.decode(errors="replace").rstrip("\n")


async def spawn(*args: str, cwd: Optional[str] = None) -> asyncio.subprocess.Process:
    """Spawn a subprocess with piped stdout/stderr, without blocking the event loop."""
    return await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def terminate_gracefully(process: asyncio.subprocess.Process, *, timeout: float = 5.0) -> None:
    """Terminate `process`, escalating to a hard kill if it doesn't exit within `timeout`s."""
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()
