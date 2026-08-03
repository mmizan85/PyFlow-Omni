"""SmartRouter: classify raw input (URL/magnet/torrent/batch file) and pick an engine.

Media-URL detection asks yt-dlp's own extractors directly via their offline
`suitable(url)` regex check — no network call is made, so this is safe and
fast to run on every keystroke's worth of pasted input.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import aiofiles

logger = logging.getLogger(__name__)


class InputKind(str, Enum):
    HTTP_URL = "http_url"
    MAGNET = "magnet"
    TORRENT_FILE = "torrent_file"
    BATCH_FILE = "batch_file"
    MEDIA_URL = "media_url"
    UNKNOWN = "unknown"


@dataclass
class RouteDecision:
    kind: InputKind
    engine: Optional[str]  # "aria2" | "ytdlp" | None if ambiguous / needs disambiguation
    original: str
    extractor_name: Optional[str] = None
    ambiguous: bool = False


class SmartRouter:
    """Inspects a single input string (or batch file) and picks aria2 vs yt-dlp."""

    def __init__(self) -> None:
        self._extractors: Optional[list] = None

    def _load_extractors(self) -> list:
        if self._extractors is None:
            from yt_dlp.extractor import gen_extractor_classes

            self._extractors = list(gen_extractor_classes())
        return self._extractors

    def _matches_media_extractor(self, url: str) -> Optional[str]:
        """Name of a specific (non-Generic) yt-dlp extractor that claims `url`.

        Purely offline: each extractor's `suitable()` is a regex/prefix
        check against the URL string, no network access is made.
        """
        for ie in self._load_extractors():
            try:
                ie_key = ie.ie_key()
            except Exception:
                continue
            if ie_key == "Generic":
                continue
            try:
                if ie.suitable(url):
                    return ie_key
            except Exception:
                continue
        return None

    def classify(self, raw_input: str) -> RouteDecision:
        text = raw_input.strip()

        if text.lower().startswith("magnet:?xt=urn:btih:"):
            return RouteDecision(kind=InputKind.MAGNET, engine="aria2", original=text)

        if text.lower().endswith(".torrent"):
            return RouteDecision(kind=InputKind.TORRENT_FILE, engine="aria2", original=text)

        if text.lower().endswith(".txt") and Path(text).expanduser().exists():
            return RouteDecision(kind=InputKind.BATCH_FILE, engine=None, original=text)

        parsed = urlparse(text)
        if parsed.scheme in ("http", "https", "ftp", "sftp"):
            extractor = self._matches_media_extractor(text)
            if extractor:
                return RouteDecision(
                    kind=InputKind.MEDIA_URL, engine="ytdlp", original=text, extractor_name=extractor
                )
            return RouteDecision(kind=InputKind.HTTP_URL, engine="aria2", original=text)

        return RouteDecision(kind=InputKind.UNKNOWN, engine=None, original=text, ambiguous=True)

    async def classify_async(self, raw_input: str) -> RouteDecision:
        """Same as `classify`, off the event loop — the first call per process
        pays a ~0.3s one-time cost to import yt-dlp's extractor table."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.classify, raw_input)

    async def read_batch_file(self, path: str) -> List[str]:
        """Read a batch file asynchronously; skip blank lines and `#` comments."""
        entries: List[str] = []
        async with aiofiles.open(Path(path).expanduser(), mode="r", encoding="utf-8", errors="replace") as f:
            async for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                entries.append(stripped)
        return entries

    async def classify_batch(self, path: str) -> List[RouteDecision]:
        entries = await self.read_batch_file(path)
        loop = asyncio.get_running_loop()
        return [await loop.run_in_executor(None, self.classify, entry) for entry in entries]
