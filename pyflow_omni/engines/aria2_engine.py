"""Aria2c-backed engine: files, torrents, magnets, and metalinks via JSON-RPC.

The RPC client is hand-rolled on aiohttp rather than the synchronous
`aria2p` wrapper, so every call is genuinely non-blocking on the event
loop — important when polling hundreds of active downloads several times
a second (spec: "200+ simultaneous tasks without freezing").
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import itertools
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from ..utils.subprocess_utils import find_free_port, spawn, stream_lines, terminate_gracefully, which
from .base import DownloadResult, Engine, LogCallback, ProgressCallback, ProgressUpdate, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class Aria2SessionConfig:
    """Per-batch overrides (spec 4.3: Session Quick Config). Never persisted to disk."""

    max_connection_per_server: int = 8
    split: int = 8
    max_overall_download_limit: str = "0"
    max_upload_limit: str = "1M"
    seed_ratio: float = 1.0
    pause_metadata: bool = False
    download_dir: Optional[str] = None

    @classmethod
    def from_aria2_defaults(cls, defaults: Any) -> "Aria2SessionConfig":
        """Build a session config seeded from the permanent `Aria2Defaults`."""
        return cls(
            max_connection_per_server=defaults.max_connection_per_server,
            split=defaults.split,
            max_overall_download_limit=defaults.max_overall_download_limit,
            max_upload_limit=defaults.max_upload_limit,
            seed_ratio=defaults.seed_ratio,
        )


class Aria2RpcError(RuntimeError):
    pass


class Aria2RpcClient:
    """Minimal async JSON-RPC 2.0 client for aria2's RPC interface."""

    def __init__(self, port: int, secret: str, *, host: str = "127.0.0.1") -> None:
        self._url = f"http://{host}:{port}/jsonrpc"
        self._secret = secret
        self._id_counter = itertools.count(1)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _ensure_open(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _auth_params(self, params: Optional[List[Any]]) -> List[Any]:
        base = [f"token:{self._secret}"] if self._secret else []
        return base + list(params or [])

    async def call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        session = await self._ensure_open()
        full_method = method if method.startswith("aria2.") else f"aria2.{method}"
        payload = {
            "jsonrpc": "2.0",
            "id": next(self._id_counter),
            "method": full_method,
            "params": self._auth_params(params),
        }
        async with session.post(self._url, data=json.dumps(payload)) as resp:
            body = await resp.json(content_type=None)
        if "error" in body:
            raise Aria2RpcError(body["error"].get("message", "unknown aria2 RPC error"))
        return body.get("result")

    async def add_uri(self, uris: List[str], options: Dict[str, Any]) -> str:
        return await self.call("addUri", [uris, options])

    async def add_torrent(self, torrent_b64: str, options: Dict[str, Any]) -> str:
        return await self.call("addTorrent", [torrent_b64, [], options])

    async def tell_status(self, gid: str, keys: Optional[List[str]] = None) -> Dict[str, Any]:
        return await self.call("tellStatus", [gid, keys] if keys else [gid])

    async def pause(self, gid: str) -> None:
        await self.call("pause", [gid])

    async def unpause(self, gid: str) -> None:
        await self.call("unpause", [gid])

    async def remove(self, gid: str) -> None:
        await self.call("remove", [gid])

    async def get_version(self) -> Dict[str, Any]:
        return await self.call("getVersion")

    async def shutdown(self) -> None:
        await self.call("shutdown")

    async def change_global_option(self, options: Dict[str, str]) -> None:
        await self.call("changeGlobalOption", [options])


@dataclass
class _ManagedTask:
    gid: str
    task_id: str
    name: str


class Aria2Engine(Engine):
    """Downloads HTTP(S)/FTP files, magnets, and .torrent files through aria2c."""

    name = "aria2"

    def __init__(self, aria2c_path: str = "aria2c", *, max_concurrent_downloads: int = 5) -> None:
        self._aria2c_path = aria2c_path
        self._max_concurrent_downloads = max(1, max_concurrent_downloads)
        self._process: Optional[asyncio.subprocess.Process] = None
        self._client: Optional[Aria2RpcClient] = None
        self._started_daemon = False
        self._active_tasks: Dict[str, _ManagedTask] = {}
        self._lock = asyncio.Lock()

    # -- daemon lifecycle ------------------------------------------------

    async def _ensure_daemon(self, log_callback: Optional[LogCallback]) -> Aria2RpcClient:
        async with self._lock:
            if self._client is not None:
                return self._client

            # Prefer an already-running daemon on the conventional port —
            # avoids spawning a redundant process if the user (or another
            # tool) already has one up.
            probe = Aria2RpcClient(6800, secret="")
            try:
                await probe.get_version()
                with contextlib.suppress(Exception):
                    # Best-effort: an externally-running daemon might reject
                    # this if it enforces its own RPC auth, but there's no
                    # harm in trying — without it, a pre-existing daemon
                    # would silently ignore our configured concurrency cap.
                    await probe.change_global_option(
                        {"max-concurrent-downloads": str(self._max_concurrent_downloads)}
                    )
                self._client = probe
                return probe
            except Exception:
                await probe.close()

            if which(self._aria2c_path) is None:
                raise RuntimeError(
                    f"'{self._aria2c_path}' was not found on PATH. Install aria2 "
                    "(e.g. `apt install aria2`, `brew install aria2`, or download "
                    "from https://aria2.github.io/) or fix aria2c_path in the config."
                )

            port = find_free_port(preferred=6800)
            secret = secrets.token_hex(16)
            self._process = await spawn(
                self._aria2c_path,
                "--enable-rpc",
                f"--rpc-listen-port={port}",
                f"--rpc-secret={secret}",
                "--rpc-listen-all=false",
                "--continue=true",
                "--summary-interval=1",
                "--file-allocation=falloc",
                f"--max-concurrent-downloads={self._max_concurrent_downloads}",
            )
            self._started_daemon = True
            if log_callback is not None:
                asyncio.create_task(self._pump_daemon_logs(log_callback))

            client = Aria2RpcClient(port, secret)
            for _ in range(50):  # wait up to ~5s for the RPC socket to come up
                try:
                    await client.get_version()
                    break
                except Exception:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("aria2c started but never became reachable over RPC.")
            self._client = client
            return client

    _ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

    async def _pump_daemon_logs(self, log_callback: LogCallback) -> None:
        # aria2c writes its NOTICE/WARN/ERROR log lines to stdout (confirmed
        # empirically — stderr is essentially unused), with ANSI color
        # codes that need stripping before they reach a plain-text log panel.
        if self._process is None or self._process.stdout is None:
            return
        try:
            async for line in stream_lines(self._process.stdout):
                clean = self._ANSI_RE.sub("", line).strip()
                if clean:
                    await log_callback(clean)
        except Exception:
            logger.debug("aria2c log pump ended", exc_info=True)

    async def _maybe_shutdown_daemon(self) -> None:
        if self._active_tasks:
            return
        if self._client is not None and self._started_daemon:
            try:
                await self._client.shutdown()
            except Exception:
                logger.debug("aria2 RPC shutdown failed, hard-terminating instead", exc_info=True)
            await self._client.close()
            self._client = None
        if self._process is not None:
            await terminate_gracefully(self._process)
            self._process = None
        self._started_daemon = False

    # -- helpers -----------------------------------------------------------

    def _build_options(self, session: Aria2SessionConfig, fallback_dir: str) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "dir": session.download_dir or fallback_dir,
            "max-connection-per-server": str(session.max_connection_per_server),
            "split": str(session.split),
            "max-overall-download-limit": session.max_overall_download_limit,
            "max-upload-limit": session.max_upload_limit,
            "seed-ratio": str(session.seed_ratio),
        }
        if session.pause_metadata:
            options["pause-metadata"] = "true"
        return options

    async def _load_torrent_bytes(self, location: str) -> bytes:
        if location.startswith("http://") or location.startswith("https://"):
            async with aiohttp.ClientSession() as session:
                async with session.get(location) as resp:
                    resp.raise_for_status()
                    return await resp.read()
        return Path(location).expanduser().read_bytes()

    async def _add_task(self, client: Aria2RpcClient, input_value: str, options: Dict[str, Any]) -> str:
        if input_value.startswith("magnet:"):
            return await client.add_uri([input_value], options)
        if input_value.lower().endswith(".torrent"):
            torrent_bytes = await self._load_torrent_bytes(input_value)
            return await client.add_torrent(base64.b64encode(torrent_bytes).decode("ascii"), options)
        return await client.add_uri([input_value], options)

    _STATE_MAP = {
        "active": TaskStatus.ACTIVE,
        "waiting": TaskStatus.QUEUED,
        "paused": TaskStatus.PAUSED,
        "complete": TaskStatus.COMPLETE,
        "removed": TaskStatus.CANCELLED,
        "error": TaskStatus.ERROR,
    }

    # -- Engine interface ----------------------------------------------------

    async def process(
        self,
        input_value: str,
        config: Any,
        progress_callback: ProgressCallback,
        log_callback: Optional[LogCallback] = None,
    ) -> DownloadResult:
        client = await self._ensure_daemon(log_callback)
        session_cfg: Aria2SessionConfig = getattr(config, "aria2_session", None) or Aria2SessionConfig()
        out_dir = getattr(config, "download_dir", str(Path.home()))
        options = self._build_options(session_cfg, out_dir)

        started = time.monotonic()
        gid = await self._add_task(client, input_value, options)
        task_id = f"aria2-{gid}"
        self._active_tasks[task_id] = _ManagedTask(gid=gid, task_id=task_id, name=input_value)

        status: Dict[str, Any] = {}
        result_status = TaskStatus.ACTIVE
        display_name = input_value
        try:
            while True:
                try:
                    status = await client.tell_status(
                        gid,
                        ["status", "totalLength", "completedLength", "downloadSpeed",
                        "files", "errorMessage", "bittorrent", "connections", "followedBy"],
                    )
                except Exception as exc:
                    logger.warning(f"RPC fetch failed for {task_id}: {exc}")
                    await asyncio.sleep(1)
                    continue 
                
                state = status.get("status", "active")

                # A magnet link finishes its *metadata* download under this
                # gid, then aria2 spawns a brand-new gid for the actual
                # torrent content. Without this, magnets would report
                # "complete" after only fetching a few KB of metadata.
                followed = status.get("followedBy")
                if state == "complete" and followed:
                    gid = followed[0]
                    self._active_tasks[task_id].gid = gid
                    continue

                total = int(status.get("totalLength") or 0)
                done = int(status.get("completedLength") or 0)
                speed = float(status.get("downloadSpeed") or 0)
                files = status.get("files") or []
                if files and files[0].get("path"):
                    display_name = Path(files[0]["path"]).name
                bittorrent = status.get("bittorrent") or {}
                extra = {
                    "connections": status.get("connections"),
                    "torrent_name": (bittorrent.get("info") or {}).get("name"),
                }
                eta = int((total - done) / speed) if speed > 0 and total > done else None
                result_status = self._STATE_MAP.get(state, TaskStatus.ERROR)

                await progress_callback(ProgressUpdate(
                    task_id=task_id,
                    name=display_name,
                    status=result_status,
                    downloaded_bytes=done,
                    total_bytes=total or None,
                    speed_bytes_per_sec=speed,
                    eta_seconds=eta,
                    message=status.get("errorMessage", ""),
                    extra=extra,
                ))

                if state in ("complete", "error", "removed"):
                    break
                await asyncio.sleep(0.5)

            elapsed = time.monotonic() - started
            output_paths = [f["path"] for f in (status.get("files") or []) if f.get("path")]
            return DownloadResult(
                task_id=task_id,
                name=display_name,
                status=result_status,
                output_paths=output_paths,
                total_bytes=int(status.get("totalLength") or 0),
                elapsed_seconds=elapsed,
                average_speed=(int(status.get("totalLength") or 0) / elapsed) if elapsed > 0 else 0.0,
                error=status.get("errorMessage") or None,
            )
        finally:
            self._active_tasks.pop(task_id, None)
            await self._maybe_shutdown_daemon()

    async def pause(self, task_id: str) -> None:
        task = self._active_tasks.get(task_id)
        if task and self._client:
            await self._client.pause(task.gid)

    async def resume(self, task_id: str) -> None:
        task = self._active_tasks.get(task_id)
        if task and self._client:
            await self._client.unpause(task.gid)

    async def cancel(self, task_id: str) -> None:
        task = self._active_tasks.get(task_id)
        if task and self._client:
            await self._client.remove(task.gid)

    async def apply_bandwidth_limit(self, limit: str) -> None:
        """Used by the Bandwidth Scheduler to change the *running* daemon's cap live."""
        if self._client is not None:
            await self._client.change_global_option({"max-overall-download-limit": limit})

    async def shutdown(self) -> None:
        self._active_tasks.clear()
        await self._maybe_shutdown_daemon()
