"""Clipboard access with graceful degradation when no clipboard backend exists.

Headless boxes (CI, containers, SSH sessions without X11/Wayland forwarding)
have no clipboard mechanism at all — that's an expected, common situation
here, not an error, so failures are swallowed and reported as "nothing to
paste" rather than raised.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_URL_OR_MAGNET_RE = re.compile(
    r"^(https?://\S+|ftp://\S+|magnet:\?xt=urn:btih:\S+)$", re.IGNORECASE
)


def get_clipboard_text() -> Optional[str]:
    """Return clipboard text, or None if unavailable/empty. Never raises."""
    try:
        import pyperclip

        text = pyperclip.paste()
    except Exception:
        logger.debug("Clipboard unavailable (no xclip/xsel/wl-clipboard/pbcopy/win32)", exc_info=True)
        return None
    text = (text or "").strip()
    return text or None


def looks_like_downloadable(text: str) -> bool:
    """Cheap check for whether clipboard text is worth offering as a pre-filled input."""
    return bool(_URL_OR_MAGNET_RE.match(text.strip()))


async def monitor_clipboard(
    on_new_link: Callable[[str], Awaitable[None]],
    *,
    interval: float = 2.0,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Poll the clipboard every `interval`s; call `on_new_link` when a *new* URL/magnet appears.

    Runs the blocking `pyperclip.paste()` call in the default executor each
    tick so it never stalls the event loop. Intended to be launched as a
    background task (e.g. `asyncio.create_task`) and stopped via `stop_event`.
    """
    loop = asyncio.get_running_loop()
    last_seen: Optional[str] = None
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        text = await loop.run_in_executor(None, get_clipboard_text)
        if text and text != last_seen:
            last_seen = text
            if looks_like_downloadable(text):
                await on_new_link(text)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
